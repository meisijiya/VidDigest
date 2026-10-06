/**
 * 管理员入口的可见性随登录/登出变化（工单 #33）。
 *
 * ## 这个缺陷为什么文本断言抓不到
 *
 * `admin-ui.test.mjs` 里曾有 3 条只断「`refreshAdminFlag` 这个函数名在源码里」的
 * 文本断言，把整个函数掏空（保留签名）它们照样全绿。缺的是**行为**：
 * 入口这一帧到底显不显示。那 3 条已在工单 #33 收尾时删除，判据全部落在本文件。
 *
 * 根因是 `App.vue` 里的 `isAdmin` 只在两处被写——模块初始化（`:185`）与
 * `onMounted` 里的 `refreshAdminFlag()`（`:659`）。`handleAuthSuccess`（`:603`）
 * 与 `handleLogout`（`:607`）都不碰它，而 `AppHeader.vue:59` 是**裸的**
 * `v-if="isAdmin"`，没有并联 `user`。于是两个方向都错：
 *
 *   登录：SPA 内登录（不刷新）→ 入口不出现，要按 F5
 *   登出：`currentUser` 置空但 `isAdmin` 留 true → 登出态下入口还在
 *
 * ⚠️ **这不是权限绕过。** 真正的边界在后端 `require_admin` 的 403（ADR 0010）。
 * 这里修的是「用户看得见摸不着的管理功能」。
 *
 * ## 两条用例都跑在真挂载上
 *
 * 断言「渲染出了什么」→ 走 `vitest run`（本文件 `.spec.mjs`），不走
 * `node --test`。断言对象是 `AppHeader` 里那个带 `aria-label="管理后台"`
 * 的按钮在**同一个 wrapper 生命周期内**出没。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

// vi.mock 必须留在本文件顶部：它靠「被提升到 App.vue 的 import 之前」生效。
// 下沉到共用夹具后失效是**静默的**——组件真去发 axios 请求，jsdom 没 XHR，
// 形态是「挂载即炸」，看着像真故障，其实在测一个不存在的世界。
//
// ⚠️ 只替换 `fetchMe` / `login` / `register` 这三个**走网络**的出口，
// `getSavedUser` / `isLoggedIn` / `saveAuth` / `logout` 保留真实现：
// 它们读写的正是 localStorage，而 `refreshAdminFlag()` 的第一道守卫
// `if (!isLoggedIn())` 就靠它。mock 掉的话这条路径会短路成
// 「永远不是管理员」，测的就不是产品行为了。
vi.mock('../src/api/auth.js', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    fetchMe: vi.fn(),
    login: vi.fn(),
    register: vi.fn(),
  }
})

// ByokDialog 的 `onMounted(loadCatalog)` 会打一次 `/api/models`。
vi.mock('../src/api/models.js', () => ({ fetchPublicModelCatalog: vi.fn() }))

import App from '../src/App.vue'
import { fetchMe, login } from '../src/api/auth.js'
import { fetchPublicModelCatalog } from '../src/api/models.js'

// jsdom 没有 IntersectionObserver。这是**探针环境缺口，不是产品缺陷**：
// 四个营销区块（Feature/HowTo/Comparison/Platform）在 onMounted 里建观察器。
// 放在模块顶层而不是 describe 体里——模块加载失败是响的，describe 体抛异常是哑的。
class IntersectionObserverStub {
  constructor(cb) { this.cb = cb }
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
}
globalThis.IntersectionObserver = IntersectionObserverStub

/** 管理员入口按钮。AppHeader.vue:60 上带着 aria-label，是最稳的抓手。 */
const adminEntry = () => document.body.querySelector('button[aria-label="管理后台"]')

/** flushPromises 只清一趟微任务队列；这里嵌套了两层 async（login → fetchMe）。 */
async function settle() {
  await flushPromises()
  await nextTick()
  await flushPromises()
  await nextTick()
}

async function mountApp() {
  const wrapper = mount(App, { attachTo: document.body })
  await settle()
  return wrapper
}

/** 打开登录框、填表、提交——走的是真实的 AuthModal 表单，不是直接 emit。 */
async function loginThroughModal(user) {
  const headerLogin = [...document.body.querySelectorAll('header button')]
    .find((b) => b.textContent.trim() === '登录')
  headerLogin.click()
  await settle()

  const inputs = document.body.querySelectorAll('form input')
  inputs[0].value = user.email
  inputs[0].dispatchEvent(new Event('input'))
  inputs[1].value = 'secret1'
  inputs[1].dispatchEvent(new Event('input'))
  await nextTick()

  document.body.querySelector('form button[type="submit"]').click()
  await settle()
}

beforeEach(() => {
  localStorage.clear()
  // reset 而不是 clear：clear 只清调用历史不清实现，会让漏设前提的用例默默
  // 继承上一条的种子，断言了另一个场景还照样绿。
  vi.resetAllMocks()
  fetchMe.mockResolvedValue({ id: 1, email: 'a@example.com', is_admin: false })
  fetchPublicModelCatalog.mockResolvedValue({ items: [] })
  document.body.innerHTML = ''
})

afterEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
})

describe('沙箱自检 · mock 真的接上了', () => {
  it('种子里的登录用户，AppHeader 真的渲染出了那个邮箱', async () => {
    // 前提自检：这一条红了，下面所有「入口出没出现」都没有意义。
    localStorage.setItem('auth_token', 'tok-selfcheck')
    localStorage.setItem('auth_user', JSON.stringify({ id: 1, email: 'selfcheck@example.com' }))

    const w = await mountApp()

    expect(fetchMe).toHaveBeenCalled()
    expect(w.text()).toContain('selfcheck@example.com')
    w.unmount()
  })

  it('反向对照：换掉种子，界面跟着换', async () => {
    // 只断「某个按钮在不在」的话，「不出现」可能恒真（根本没渲染也算）。
    // 这一条证明渲染链路是通的：种子说 is_admin，它就得在。
    //
    // ⚠️ localStorage 与 /api/auth/me **两处都要种**：`isAdmin` 在 setup 时
    // 按 localStorage 求值一次（App.vue:185），紧接着 `onMounted` 的
    // `refreshAdminFlag()` 会用 `/api/auth/me` 的结果**覆盖**它。
    // 第一版只种了 localStorage、留着 beforeEach 的 `is_admin: false`，
    // 于是这一条是「挂载时被回查翻成 false」而红——不是渲染链路断了。
    // 每条用例要用的非默认数据都得在自己体内设一次。
    const seeded = { id: 2, email: 'control@example.com', is_admin: true }
    localStorage.setItem('auth_token', 'tok-control')
    localStorage.setItem('auth_user', JSON.stringify(seeded))
    fetchMe.mockResolvedValue(seeded)

    const w = await mountApp()

    expect(w.text()).toContain('control@example.com')
    expect(adminEntry(), '管理员登录后入口本该在').toBeTruthy()
    w.unmount()
  })
})

describe('工单 #33 判据 1 · 登录后入口出现（SPA 内，不刷新）', () => {
  it('登出态登录成管理员，管理后台入口出现，全程没有刷新页面', async () => {
    // 起点必须是**登出态**：localStorage 里没有 token 时 isAdmin 恒 false，
    // 修复前入口不出现，修复后出现。
    expect(localStorage.getItem('auth_token'), '用例前提不成立：起点不是登出态').toBeNull()

    // 登录响应带 is_admin（后端登录响应确实带），/api/auth/me 回查也带。
    const admin = { id: 7, email: 'admin@example.com', is_admin: true }
    login.mockImplementation(async () => {
      // 真实现 login() 会 saveAuth，App 的 isLoggedIn() 守卫就靠 localStorage
      // 里的 token。少了这一步 refreshAdminFlag() 第一行就 return 了。
      localStorage.setItem('auth_token', 'tok-new')
      localStorage.setItem('auth_user', JSON.stringify(admin))
      return admin
    })
    fetchMe.mockResolvedValue(admin)

    const w = await mountApp()

    // 对照组：登出态下入口本来就不该在，否则下面那条会恒真。
    expect(adminEntry(), '对照：登出态下入口不该出现').toBeNull()

    // 「不刷新页面」的可观察证据：给根节点打个标记，全程必须在，
    // 而且节点本身不能被换掉（换了等于重新挂载，也等价于刷新）。
    const sameNode = w.element
    sameNode.dataset.sentinel = 'no-reload'

    await loginThroughModal(admin)

    expect(w.element, '登录过程把根节点换掉了，等价于重新挂载').toBe(sameNode)
    expect(w.element.dataset.sentinel, '登录过程发生过刷新').toBe('no-reload')
    expect(adminEntry(), '管理员登录后管理后台入口没有出现').toBeTruthy()
    expect(w.text(), '入口出现了，用户却是登出态').toContain(admin.email)

    w.unmount()
  })
})

describe('工单 #33 判据 2 · 登出后入口消失', () => {
  it('管理员登出后，管理后台入口不再显示', async () => {
    const admin = { id: 7, email: 'admin@example.com', is_admin: true }
    localStorage.setItem('auth_token', 'tok-admin')
    localStorage.setItem('auth_user', JSON.stringify(admin))
    fetchMe.mockResolvedValue(admin)

    const w = await mountApp()

    // 对照组：登出前入口必须在，否则「登出后不见了」可能只是它从来没出现过。
    expect(adminEntry(), '对照：登出前入口必须在').toBeTruthy()

    const logoutBtn = [...document.body.querySelectorAll('header button')]
      .find((b) => b.textContent.trim() === '退出')
    logoutBtn.click()
    await settle()

    expect(adminEntry(), '登出后管理后台入口还在').toBeNull()
    expect(w.text(), '登出后顶栏还挂着那个邮箱').not.toContain(admin.email)

    w.unmount()
  })
})

describe('工单 #33 复审 · 登出后在途回查不得把入口重新点亮', () => {
  it('挂起 /api/auth/me：登录后立刻登出，让在途请求回来，入口不重新出现', async () => {
    // 这一条是**本次修复自己引入的**竞态，方向与判据 2 相反。
    //
    // `auth.js` 的拦截器在**请求时**读 token，所以登出前发出的那发
    // /api/auth/me 带着**有效的旧 token** 到达服务端并成功返回。它 resolve
    // 之后若无条件写 isAdmin，就把 handleLogout 的同步重置覆盖掉——
    // **登出之后、token 已经是 null 的情况下，管理后台入口重新亮了。**
    //
    // ⚠️ 靠 `catch` 兜底防不住：这一路走的是 `try` 的**成功**分支。
    // 只能让过期结果自己作废（代次守卫）。
    const admin = { id: 7, email: 'admin@example.com', is_admin: true }
    login.mockImplementation(async () => {
      localStorage.setItem('auth_token', 'tok-new')
      localStorage.setItem('auth_user', JSON.stringify(admin))
      return admin
    })

    // ⚠️ **手动控制 resolve，不用 sleep 撞时序。** await sleep 出来的竞态用例
    // 在 CI 上时好时坏，而「偶发红」最容易被登记成「未定因」——本仓已经吃过
    // 一次亏。这里把 promise 真的挂起，由本用例在断言之后手动放行，
    // 于是「在途窗口」在任何机器上都必然存在。
    let releaseFetchMe
    fetchMe.mockImplementation(() => new Promise((resolve) => {
      releaseFetchMe = () => resolve(admin)
    }))

    const w = await mountApp()
    // 起点必须是**登出态**：否则 `refreshAdminFlag` 第一道 `if (!isLoggedIn())`
    // 就 return 了，那发请求压根发不出去，下面的竞态无从谈起。
    expect(fetchMe, '登出态挂载时不该发 /api/auth/me —— 前提不成立').not.toHaveBeenCalled()

    await loginThroughModal(admin)

    // 登录后回查**在途**，结果还没回来 —— 此刻入口本就不该出现。
    expect(fetchMe, '登录后应该发出了一发 /api/auth/me').toHaveBeenCalled()
    expect(adminEntry(), '回查还在途，入口不该出现').toBeNull()

    // 登出，落在请求在途的窗口里。
    const logoutBtn = [...document.body.querySelectorAll('header button')]
      .find((b) => b.textContent.trim() === '退出')
    logoutBtn.click()
    await settle()
    expect(adminEntry(), '登出后入口本该消失').toBeNull()
    expect(localStorage.getItem('auth_token'), '登出后 token 应当已清').toBeNull()

    // 现在放行那一发在途请求。它是登出**前**发出的，服务端会成功返回
    // is_admin: true —— 这正是覆盖同步重置的那一刀。
    releaseFetchMe()
    await settle()

    expect(adminEntry(), '在途回查把登出后的管理员入口重新点亮了').toBeNull()
    expect(w.text(), '在途回查把登出后的用户也复活了').not.toContain(admin.email)

    w.unmount()
  })
})

// ─────────────────────────────────────────────────────────
// 工单 #33 收尾
// ─────────────────────────────────────────────────────────

/**
 * 只假 setTimeout —— `checkPaymentResult` 里那 1000ms 就是它。
 *
 * ⚠️ 不能用默认的 toFake：默认集合里含 setImmediate，而 `@vue/test-utils`
 * 的 `flushPromises()` 内部正是 setImmediate。一起被冻住的话 `settle()`
 * 永远挂住，而挂住的失败形状是**超时**，很容易误读成「组件没更新」。
 */
function fakeOnlyTimeouts() {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
}

function restoreTimers() {
  vi.useRealTimers()
  // `checkPaymentResult` 会把 payment 参数洗掉，但**只有走到那一支**才洗。
  // 无条件复位，免得某条用例把 URL 弄脏后影响后面的挂载。
  window.history.replaceState({}, '', '/')
}

/** 推进假定时器，并让落在新窗口里的 promise 跑完。 */
async function advance(ms) {
  vi.advanceTimersByTime(ms)
  await settle()
}

describe('工单 #33 收尾 · ① 付款成功回跳刷新会话', () => {
  beforeEach(fakeOnlyTimeouts)
  afterEach(restoreTimers)

  it('A 阳性：?payment=success 挂载，1000ms 后顶栏换成 /me 返回的用户', async () => {
    // 这条路径本轮**被改过**：`setTimeout(async () => { currentUser.value = await fetchMe() })`
    // 收口成了 `setTimeout(() => refreshSession(true))`，而改动前零测试覆盖
    // （`grep -rn "checkPaymentResult|payment" tests/` 当时 0 命中）。
    //
    // 可观察的载体只能是**顶栏邮箱**：`features.js` 的 MEMBERSHIP_ENABLED === false，
    // VIP 徽章恒不渲染，`is_vip` 字段本身看不到。能证明的是承载它的
    // `currentUser` 被整体替换了 —— 也就是付款那一发传了 syncUser=true。
    //
    // ⚠️ syncUser 传错的后果**不对称**，两个方向都得钉：
    //   · 付款传 false → VIP 状态永不刷新（付完仍显示非 VIP，直到 F5），
    //     **唯一造成用户可见损失的方向**；
    //   · 挂载/登录传 true → 用 /me 覆盖 localStorage 种子与登录响应，
    //     静默丢掉两者有、/me 没有的字段。
    // 第一段断言（挂载那一发**不得**改写 currentUser）钉的就是第二个方向。
    const seed = { id: 3, email: 'before-payment@example.com', is_admin: true }
    localStorage.setItem('auth_token', 'tok-pay')
    localStorage.setItem('auth_user', JSON.stringify(seed))

    const fresh = { id: 3, email: 'after-payment@example.com', is_admin: true }
    fetchMe.mockResolvedValue(fresh)
    window.history.replaceState({}, '', '/?payment=success')

    const w = await mountApp()

    // 挂载本身也发一次回查（refreshAdminFlag → refreshSession(false)）。
    expect(fetchMe).toHaveBeenCalledTimes(1)
    expect(w.text(), '挂载那一发传了 syncUser=true，种子被静默覆盖了')
      .toContain('before-payment@example.com')

    await advance(1000)

    // 付款回跳补发的那一发：它才是 VIP 状态唯一的更新途径。
    expect(fetchMe, '付款成功回跳没有补发 /api/auth/me').toHaveBeenCalledTimes(2)
    expect(w.text(), '付款回跳没有刷新当前用户').toContain('after-payment@example.com')
    expect(w.text(), '种子用户还挂在顶栏上').not.toContain('before-payment@example.com')

    w.unmount()
  })

  it('B 反向对照：URL 不带 payment=success，推进同样久也不会多发回查', async () => {
    // ⚠️ **B 不可省。** 只有 A 的话，「顶栏变了」有两个同样说得通的来源：
    // 付款分支补发了第二发，或者**挂载时就已经是新的**（种子写入时序、
    // setup 求值时机）。两者都让 A 变绿，却不是同一回事。B 把付款分支
    // 单独隔离出来：不带参数时，顶栏必须停在种子上、且只有一次回查。
    const seed = { id: 4, email: 'no-payment@example.com', is_admin: true }
    localStorage.setItem('auth_token', 'tok-nopay')
    localStorage.setItem('auth_user', JSON.stringify(seed))

    // 故意让 /me 返回**另一个**用户：若 mount 时序让种子提前被换掉，
    // 或者付款分支被改成无条件刷新，这里就会露馅。
    fetchMe.mockResolvedValue({ id: 4, email: 'after-payment@example.com', is_admin: true })
    window.history.replaceState({}, '', '/')

    const w = await mountApp()
    await advance(1000)

    expect(fetchMe, '没有付款回跳就不该有第二发').toHaveBeenCalledTimes(1)
    expect(w.text(), '没有付款回跳却把顶栏用户换了').not.toContain('after-payment@example.com')
    expect(w.text(), '顶栏应当停在 localStorage 种子上').toContain('no-payment@example.com')

    w.unmount()
  })
})

describe('工单 #33 收尾 · ② 在途**失败**的旧回查不得抹掉更新的成功结果', () => {
  it('C：挂载那发在途失败、重新登录那发已成功，管理员入口仍在', async () => {
    // `refreshSession` 的 catch 里那个 `if (gen === adminFlagGen)` 就是这一条。
    // 缺了它，一次**在途失败**的旧请求会把更新的成功结果清成 isAdmin=false，
    // 管理员入口**无故消失**（方向是入口消失、不是提权，但仍是用户可见缺陷）。
    //
    // 造这个状态靠手动控制 resolve/reject，**不用 sleep 撞时序**：
    //   挂载（gen=1，在途）→ 登出（gen=2）→ 再登录（gen=3，成功 is_admin:true）
    //   → 这时才让**第 1 发**失败。gen(1) ≠ adminFlagGen(3)，整份作废。
    const admin = { id: 9, email: 'admin@example.com', is_admin: true }
    localStorage.setItem('auth_token', 'tok-1')
    localStorage.setItem('auth_user', JSON.stringify(admin))

    let rejectFirst
    let calls = 0
    fetchMe.mockImplementation(() => {
      calls += 1
      if (calls === 1) return new Promise((_, reject) => { rejectFirst = reject })
      return Promise.resolve(admin)
    })
    login.mockImplementation(async () => {
      localStorage.setItem('auth_token', 'tok-2')
      localStorage.setItem('auth_user', JSON.stringify(admin))
      return admin
    })

    const w = await mountApp()
    expect(fetchMe, '挂载回查应当已在途 —— 前提不成立').toHaveBeenCalledTimes(1)
    expect(adminEntry(), '对照：入口应当在（首帧按 localStorage 种子渲染）').toBeTruthy()

    // 登出，落在请求在途的窗口里。
    const logoutBtn = [...document.body.querySelectorAll('header button')]
      .find((b) => b.textContent.trim() === '退出')
    logoutBtn.click()
    await settle()
    expect(adminEntry(), '登出后入口本该消失 —— 前提不成立').toBeNull()

    // 重新登录成管理员：这一发拿到更新的代次，并成功落地。
    await loginThroughModal(admin)
    expect(adminEntry(), '重新登录后入口本该回来 —— 前提不成立').toBeTruthy()

    // 现在让**更旧**的那一发出错（它是在登出前发出、带着已失效 token 的）。
    rejectFirst(new Error('401 Unauthorized'))
    await settle()

    expect(adminEntry(), '在途失败的旧回查把更新的成功结果清成了非管理员').toBeTruthy()
    expect(w.text(), '用户还在，顶栏却不再是管理员').toContain(admin.email)

    w.unmount()
  })
})

describe('工单 #33 收尾 · ③ 更旧的在途成功结果不得覆盖更新的回查结果', () => {
  beforeEach(fakeOnlyTimeouts)
  afterEach(restoreTimers)

  it('D：付款回跳已按服务端撤权关掉入口，挂着的那发旧回查回来也不得再打开', async () => {
    // 这条守的是 `refreshSession` 里的 `++adminFlagGen`。
    //
    // 去掉 `++`，两次回查就共用同一个代次号，于是「更旧的在途结果后到」
    // 不再作废 —— 而这个场景**不经过登出**：挂载时 refreshAdminFlag() 发的那发
    // 与付款回跳 +1000ms 发的那发本来就会重叠（`onMounted` 里两个都调）。
    // 服务端在两者之间撤权时，更旧的 is_admin:true 会盖掉更新的 false，
    // 入口重新亮起来。
    const stale = { id: 5, email: 'stale-admin@example.com', is_admin: true }
    localStorage.setItem('auth_token', 'tok-stale')
    localStorage.setItem('auth_user', JSON.stringify(stale))
    window.history.replaceState({}, '', '/?payment=success')

    let releaseMountFetch
    fetchMe
      // 第 1 发（挂载）挂起：它带着的是**撤权之前**的 is_admin。
      .mockImplementationOnce(() => new Promise((resolve) => { releaseMountFetch = () => resolve(stale) }))
      // 第 2 发（付款回跳，syncUser=true）立即回来：服务端此刻已撤权。
      .mockResolvedValue({ id: 5, email: 'stale-admin@example.com', is_admin: false })

    const w = await mountApp()
    expect(fetchMe, '挂载应当发一次回查 —— 前提不成立').toHaveBeenCalledTimes(1)
    expect(adminEntry(), '对照：挂载首帧按种子渲染，入口本该在').toBeTruthy()

    await advance(1000)
    expect(fetchMe, '付款回跳应当补发一次 —— 前提不成立').toHaveBeenCalledTimes(2)
    expect(adminEntry(), '对照：服务端撤权后入口本该消失 —— 前提不成立').toBeNull()

    // 现在放行**更旧**的那一发。
    releaseMountFetch()
    await settle()

    expect(adminEntry(), '更旧的在途成功结果覆盖了更新的撤权结果').toBeNull()
    w.unmount()
  })
})

describe('工单 #33 收尾 · 管理员判据的两级：localStorage 首帧 + /me 回查', () => {
  it('E：陈旧种子下 /me 未 resolve 时入口已渲染，/me 回来后以服务端为准', async () => {
    // 替掉 admin-ui.test.mjs 里那条同义的**文本**断言
    // （`isAdmin = ref(...)` 之后 120 字符内出现 is_admin）。文本那条钉的是写法，
    // 这里钉的是行为：首帧按 localStorage 画（不闪），而 /me 一回来就以服务端为准
    // （撤权即时生效，ADR 0010）。
    //
    // ⚠️ /me 必须**挂起**：否则「首帧就渲染」恒真 —— setup 那一帧任何实现都会渲染，
    // 测不出它来自 localStorage 而不是来自 /me。
    const seeded = { id: 6, email: 'stale@example.com', is_admin: true }
    localStorage.setItem('auth_token', 'tok-stale-seed')
    localStorage.setItem('auth_user', JSON.stringify(seeded))

    let releaseFetchMe
    fetchMe.mockImplementation(() => new Promise((resolve) => {
      releaseFetchMe = () => resolve({ ...seeded, is_admin: false })
    }))

    const w = await mountApp()

    expect(fetchMe, '挂载应当发一次 /me 回查').toHaveBeenCalled()
    expect(adminEntry(), '陈旧种子下、/me 未 resolve 前，入口本该已渲染（防首帧闪）').toBeTruthy()
    expect(w.text()).toContain('stale@example.com')

    releaseFetchMe()
    await settle()

    expect(adminEntry(), '/me 说了不是管理员，入口还在 —— 撤权不即时生效').toBeNull()
    w.unmount()
  })
})