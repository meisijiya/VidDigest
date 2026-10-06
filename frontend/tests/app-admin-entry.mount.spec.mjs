/**
 * 管理员入口的可见性随登录/登出变化（工单 #33）。
 *
 * ## 这个缺陷为什么文本断言抓不到
 *
 * `admin-ui.test.mjs:273-275` 那 3 条只断「`refreshAdminFlag` 这个函数名在源码里」，
 * 把整个函数删掉它们照样全绿。缺的是**行为**：入口这一帧到底显不显示。
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