/**
 * AuthModal.vue 真挂载（工单 #19 第 2 项）。
 *
 * ## 为什么是这个文件
 *
 * 扫全仓 18 个组件的覆盖度时，它是**唯一一个有行为的组件却零测试覆盖**的：
 * `frontend/tests/**` 里 grep 不到 `AuthModal.vue` 的任何一处断言。
 *
 * 其余几个零覆盖的是 `ComparisonSection` / `FeatureSection` / `HowToSection` /
 * `PlatformSection` / `PricingSection`——静态营销区块，断言它们等于给文案拍照。
 * 而这个是**登录与注册表单**，用户每天都会走它。
 *
 * 所以这个文件补的是**真空**，不是迁移：既有用例一条都不动。
 *
 * ## 它为什么会漏
 *
 * 「零覆盖」的成因是这类组件的失明面全部落在**模板绑定与 watch 上**：
 * 它们读 `props.initialMode`、写 `ref`、在 `catch` 里读 `err.response?.data?.detail`。
 * 文本断言看得见「`watch(() => props.visible` 这行存在」，看不见它**重置了什么**、
 * **有没有重置**。
 *
 * ## 每条断言都配一条对照组
 *
 * 「不该发生」的那一半容易恒真（例如「关闭后邮箱框是空的」——如果根本没渲染，
 * 也算空）。所以下面每组都有一条「确实该发生」的对照，确保断言跑在能区分两种
 * 实现的状态上。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

// vi.mock 必须留在本文件顶部：它靠「被提升到组件 import 之前」生效。
vi.mock('../src/api/auth.js', () => ({
  login: vi.fn(),
  register: vi.fn(),
}))

import AuthModal from '../src/components/AuthModal.vue'
import { login, register } from '../src/api/auth.js'

const okUser = { id: 1, email: 'a@example.com' }

/**
 * 按**真实的打开方式**挂载。
 *
 * `App.vue:169` 是 `authModalVisible = ref(false)`，`showAuthModal(mode)` 做的是
 * false → true 那一次跃迁。组件的 watch 没有 `immediate`，也就是说**初始化只发生
 * 在那次跃迁上**。
 *
 * 第一版这里直接 `visible: true` 起挂，于是 watch 一次没跑、`mode` 停在默认的
 * 'login'——四条用例红，读起来像「注册入口坏了」，真相是测试绕开了唯一的
 * 初始化机制。现按真实路径挂载；这个依赖本身由下一条用例钉住。
 */
async function mountModal(props = {}) {
  const wrapper = mount(AuthModal, {
    props: { visible: false, ...props },
    attachTo: document.body,
  })
  await nextTick()
  await wrapper.setProps({ visible: true })
  await nextTick()
  return wrapper
}

const body = () => document.body
const inputs = () => body().querySelectorAll('input')
const emailInput = () => inputs()[0]
const passwordInput = () => inputs()[1]
const submitButton = () => body().querySelector('button[type="submit"]')

/** 填原生 input 并触发 v-model 监听的那次 input 事件。 */
function fill(el, value) {
  el.value = value
  el.dispatchEvent(new Event('input'))
}

/** 填邮箱与密码两格。 */
function fillCredentials(email = 'a@example.com', password = 'secret1') {
  fill(emailInput(), email)
  fill(passwordInput(), password)
}

/** 401 / 422 之类，detail 有时是字符串、有时是 FastAPI 校验错误的数组。 */
function httpError(detail, status = 401) {
  const err = new Error('boom')
  err.response = { status, data: { detail } }
  return err
}

beforeEach(() => {
  // reset 而不是 clear：clear 只清调用历史不清实现，会让漏设前提的用例默默
  // 继承上一条的种子，断言了另一个场景还照样绿。
  vi.resetAllMocks()
  login.mockResolvedValue(okUser)
  register.mockResolvedValue(okUser)
  body().innerHTML = ''
})

// ── 打开时的形态 ───────────────────────────────────────────

describe('AuthModal 挂载 · 初始形态', () => {
  test('initialMode 为 register 时打开的是注册表单', async () => {
    await mountModal({ initialMode: 'register' })

    expect(body().textContent).toContain('创建账号')
    expect(body().textContent).toContain('注册')
    expect(body().textContent).toContain('已有账号？')
  })

  test('对照组：initialMode 为 login 时打开的是登录表单', async () => {
    await mountModal({ initialMode: 'login' })

    expect(body().textContent).toContain('欢迎回来')
    expect(body().textContent).toContain('还没有账号？')
  })

  test('visible 为 false 时整个弹窗不渲染', async () => {
    const w = mount(AuthModal, { props: { visible: false }, attachTo: document.body })
    await nextTick()

    // 对照组：上面两条必须真的渲染出了内容，否则这条会恒真。
    expect(body().querySelector('input')).toBeNull()
    expect(body().textContent).not.toContain('欢迎回来')
    w.unmount()
  })
})

// ── 重开时必须清空上一个用户的输入 ─────────────────────────

describe('AuthModal 挂载 · 重开时清场', () => {
  test('关闭再打开，上一位填的邮箱与密码都不残留', async () => {
    const w = await mountModal({ initialMode: 'login' })

    fillCredentials('first@example.com')
    await nextTick()
    expect(emailInput().value).toBe('first@example.com')

    await w.setProps({ visible: false })
    await nextTick()
    await w.setProps({ visible: true })
    await nextTick()

    // 登出后再打开登录框，看见的是**别人**上次填的邮箱——这既是体验问题，
    // 也是把一个人的凭据留给下一个人的隐私问题。
    expect(emailInput().value, '上一位用户的邮箱还留在表单里').toBe('')
    expect(passwordInput().value, '上一位用户的密码还留在表单里').toBe('')
    w.unmount()
  })

  test('重开时上一次留下的错误提示也被清掉', async () => {
    login.mockRejectedValueOnce(httpError('邮箱或密码不对'))
    const w = await mountModal({ initialMode: 'login' })

    fillCredentials()
    await nextTick()
    await submitButton().click()
    await flushPromises()
    expect(body().textContent).toContain('邮箱或密码不对')

    await w.setProps({ visible: false })
    await nextTick()
    await w.setProps({ visible: true })
    await nextTick()

    expect(body().textContent, '关掉再打开，上一次的报错还挂着').not.toContain('邮箱或密码不对')
    w.unmount()
  })

  test('重开时 initialMode 重新生效，而不是沿用上一次打开时的那个', async () => {
    const w = await mountModal({ initialMode: 'login' })
    expect(body().textContent).toContain('欢迎回来')

    // 上一次是从页头「注册」进来的登录框；这次从社区页点「登录」进来。
    // mode 只在 visible 跃迁时被播种，所以「重新播种」这件事得单独断——
    // 少它的话，把 watch 里那行 mode 赋值删掉，本组前两条仍全绿。
    await w.setProps({ visible: false })
    await nextTick()
    await w.setProps({ initialMode: 'register', visible: true })
    await nextTick()

    expect(body().textContent, '重开时 initialMode 没重新生效').toContain('创建账号')
    w.unmount()
  })
})

// ── 提交走对的那条路 ───────────────────────────────────────

describe('AuthModal 挂载 · 提交', () => {
  async function fillAndSubmit() {
    fillCredentials()
    await nextTick()
    await submitButton().click()
  }

  test('登录模式调 login，不调 register', async () => {
    const w = await mountModal({ initialMode: 'login' })
    await fillAndSubmit()
    await flushPromises()

    expect(login).toHaveBeenCalledWith('a@example.com', 'secret1')
    expect(register).not.toHaveBeenCalled()
    w.unmount()
  })

  test('注册模式调 register，不调 login', async () => {
    const w = await mountModal({ initialMode: 'register' })
    await fillAndSubmit()
    await flushPromises()

    expect(register).toHaveBeenCalledWith('a@example.com', 'secret1')
    expect(login).not.toHaveBeenCalled()
    w.unmount()
  })

  test('成功后依次 emit success 与 close', async () => {
    const w = await mountModal({ initialMode: 'login' })
    await fillAndSubmit()
    await flushPromises()

    // 顺序不是装饰：父组件靠 success 写 token，靠 close 收起弹窗；
    // 反了就是「登录成功但弹窗不动」。
    expect(w.emitted('success')).toBeTruthy()
    expect(w.emitted('success')[0][0]).toEqual(okUser)
    expect(w.emitted('close')).toHaveLength(1)
    w.unmount()
  })

  test('提交期间按钮禁用，连点两次只发一次请求', async () => {
    let resolve
    login.mockReturnValue(new Promise((r) => { resolve = r }))
    const w = await mountModal({ initialMode: 'login' })
    await fillAndSubmit()
    await nextTick()

    expect(submitButton().disabled, '请求在飞时按钮可点').toBe(true)

    // disabled 能否拦住 VTU 的点击因版本而异，所以「按钮禁用」与「只发一次」
    // 两条分开断：后者才是交付的行为。
    await submitButton().click()
    await submitButton().click()
    await flushPromises()
    resolve(okUser)
    await flushPromises()

    expect(login, '连点两次发了两次登录请求').toHaveBeenCalledTimes(1)
    w.unmount()
  })
})

// ── 失败时用户看到什么 ─────────────────────────────────────

describe('AuthModal 挂载 · 失败反馈', () => {
  async function submitAndRead() {
    fillCredentials()
    await nextTick()
    await submitButton().click()
    await flushPromises()
    return body().textContent
  }

  test('后端返回字符串 detail 时原样显示', async () => {
    login.mockRejectedValueOnce(httpError('邮箱或密码不对'))
    const w = await mountModal({ initialMode: 'login' })

    expect(await submitAndRead()).toContain('邮箱或密码不对')
    w.unmount()
  })

  test('后端返回数组 detail 时，用户至少能看到后端给的那句 msg', async () => {
    // FastAPI 的 422 校验错误就是数组：detail: [{loc: [...], msg: '...'}]
    //
    // ⚠️ 这一条**不**断言「不出现 [object Object]」——那个失败形态不存在：Vue 的
    // 插值对数组走 JSON.stringify，渲染出来是一段 JSON 块而不是 [object Object]。
    // 变异实测：把它写成 not.toContain('[object Object]') 的话，去掉兜底之后本条
    // 照样绿——判据在替身自己身上过。留下的这一条是真的能红：去掉兜底它就空。
    login.mockRejectedValueOnce(
      httpError([{ loc: ['body', 'email'], msg: 'value is not a valid email address' }], 422))
    const w = await mountModal({ initialMode: 'login' })

    const text = await submitAndRead()
    expect(text).toContain('value is not a valid email address')
    w.unmount()
  })

  test('没有 response 的异常也有兜底文案，不渲染空白', async () => {
    login.mockRejectedValueOnce(new Error('network down'))
    const w = await mountModal({ initialMode: 'login' })

    const text = await submitAndRead()
    expect(text).toContain('操作失败')
    w.unmount()
  })

  test('失败不 emit success，也不 emit close', async () => {
    login.mockRejectedValueOnce(httpError('邮箱或密码不对'))
    const w = await mountModal({ initialMode: 'login' })
    await submitAndRead()

    // 失败时把弹窗关掉，用户连「密码打错了」这句话都来不及看。
    expect(w.emitted('success')).toBeFalsy()
    expect(w.emitted('close')).toBeFalsy()
    w.unmount()
  })
})

// ── 遮罩与关闭 ─────────────────────────────────────────────

describe('AuthModal 挂载 · 关闭', () => {
  test('点遮罩关闭，点弹窗内部不关', async () => {
    const w = await mountModal({ initialMode: 'login' })

    // @click.self —— 少了 .self，点弹窗里的输入框也会把弹窗关掉，
    // 用户填了一半的表单就没了，而且没有任何提示。
    body().querySelector('form').click()
    await nextTick()
    expect(w.emitted('close'), '点弹窗内部把弹窗关了').toBeFalsy()

    body().querySelector('.fixed').click()
    await nextTick()
    expect(w.emitted('close'), '点遮罩没关').toHaveLength(1)
    w.unmount()
  })

  test('切换登录/注册时按钮文案跟着换', async () => {
    const w = await mountModal({ initialMode: 'login' })
    const switchBtn = [...body().querySelectorAll('button')]
      .find((b) => b.textContent.includes('去注册'))
    expect(switchBtn, '没有切换到注册的入口').toBeTruthy()

    await switchBtn.click()
    await nextTick()

    expect(body().textContent).toContain('创建账号')
    w.unmount()
  })
})