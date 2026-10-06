/**
 * 管理后台挂载测试的共用夹具（工单 #18）。
 *
 * ## 为什么不放 vi.mock
 *
 * `vi.mock` 会被 vitest 提升到**所在文件**的 import 之前才生效。放进本文件
 * 再由 spec 导入，依赖的是「helper 先于组件被求值」这条时序——它今天成立，
 * 换个人调整导入顺序就静默失效，而失效的形态是**组件真的发起了 axios 请求**，
 * jsdom 里没有 XHR，测试变成「挂载即报错」，看着还挺像真故障。
 *
 * 所以分工是：`vi.mock` 留在每个 spec 文件顶部（显式、一眼看得见），
 * 本文件只提供**种子数据 + 挂载 + 查询**三样不依赖 mock 时序的东西。
 *
 * ## 种子一律是 **camelCase**
 *
 * 组件拿到的是 api 层已经转好的形状。种子写成 snake_case 等于在测试里
 * 绕过 api 层，测到的是一条组件永远走不到的数据流——那正是工单 #15
 * 实测过的「读不到的键退化成默认值，永远不报错」那一类。
 */
import { mount, flushPromises } from '@vue/test-utils'

/** 一条后台用户行，形状与 `toAdminUser` 的返回值逐字段同形。 */
export function userRow(overrides = {}) {
  return {
    id: 1,
    email: 'a@example.com',
    isAdmin: false,
    isVip: false,
    vipExpireAt: null,
    createdAt: '2026-01-02 03:04:05',
    parseUsed: 2,
    chatUsed: 1,
    parseLimit: 10,
    chatLimit: 20,
    parseLimitOverride: null,
    chatLimitOverride: null,
    parseLimitSource: 'global',
    chatLimitSource: 'global',
    ...overrides,
  }
}

/** 一条社区行，形状与 `toAdminCommunityItem` 的返回值同形。 */
export function communityRow(overrides = {}) {
  return {
    id: 10,
    videoUrl: 'https://v.example/x',
    title: '一个标题',
    authorEmail: 'a@example.com',
    tags: ['科普'],
    status: 'ready',
    createdAt: '2026-01-02 03:04:05',
    ...overrides,
  }
}

/**
 * 一条模型清单行，形状与 `toAdminModelItem` 的返回值同形。
 *
 * 键名是 `id` 不是 `key`：api 层 `toModelItem` 投影出的是 `id`，
 * 而模板里 `v-for="m in view.items" :key="m.id"`、`openModelEditor(m.id)`、
 * 草稿槽 `modelDrafts[m.id]` 全部按 `id` 走。种子写成 `key` 时组件看到的是
 * `undefined` —— 而**界面不会崩**，只会渲染出一张没有 id 的卡片，
 * 展开、编辑、保存三条路一起静默失效。
 */
export function modelRow(overrides = {}) {
  return {
    id: 'openai',
    label: 'OpenAI',
    hint: '',
    baseUrl: 'https://api.openai.com/v1',
    models: ['gpt-x'],
    defaultModel: 'gpt-x',
    isReal: 1,
    enabled: 1,
    sortOrder: 0,
    ...overrides,
  }
}

/**
 * 一条**服务端原文**的用户行（snake_case）。
 *
 * 与上面的 `userRow()` 刻意分开：列表出口 `fetchAdminUsers` 在 api 层已经
 * 转成 camelCase，而 `setUserQuota` 的 `user` 是**没转过的服务端原文**，
 * 由组件自己调 `toAdminUser` 翻一次。两种形状混用的话，回读那条路径上的
 * 字段全是 `undefined`，界面不报错，只是数字全变成 0 —— 正是 AGENTS.md
 * 记的「翻两次 / 形状不对」那一族静默故障。
 */
export function rawUserRow(overrides = {}) {
  return {
    id: 1,
    email: 'a@example.com',
    is_admin: 0,
    is_vip: 0,
    vip_expire_at: null,
    created_at: '2026-01-02 03:04:05',
    parse_used: 2,
    chat_used: 1,
    parse_limit: 10,
    chat_limit: 20,
    parse_limit_override: null,
    chat_limit_override: null,
    parse_limit_source: 'global',
    chat_limit_source: 'global',
    ...overrides,
  }
}

/** 标签词表，默认给两组、上限 3。 */
export const VOCABULARY = {
  maxTags: 3,
  groups: [
    { name: '内容', tags: ['科普', '教程', '访谈'] },
    { name: '形态', tags: ['长视频', '短视频'] },
  ],
}

/** 造一个带 `.response.status` 的 axios 风格错误。 */
export function httpError(status, message = 'boom') {
  const err = new Error(message)
  err.response = { status, data: { detail: message } }
  return err
}

/**
 * 挂载 AdminPage 并等它把首个请求跑完。
 *
 * 组件由调用方传进来而不是这里 import：spec 文件要在自己顶部声明 `vi.mock`，
 * 那是唯一能保证 mock 生效的位置；本文件如果自己 import 组件，求值顺序就
 * 取决于两边的导入先后——那是静默失效的形态。
 *
 * 为什么必须 `flushPromises`：`onMounted` 里立刻 `loadUsers()`，而模板的
 * loading / 错误 / 空 / 内容四态是同一个 `view` 的互斥分支。不等它 settle
 * 就断言，测到的是骨架屏——**而骨架屏恰好是四态之一**，于是「故障态排在
 * 空态之前」这类顺序判据会拿到一个碰巧也说得通的答案。
 */
export async function mountAdmin(Component) {
  const wrapper = mount(Component, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

// ── 查询助手：全部按「用户真的看到什么」写，不碰组件内部状态 ──────────

/** 按可见文字找按钮。`<script setup>` 不暴露内部状态，DOM 是唯一出口。 */
export function buttonByText(wrapper, text) {
  const hit = wrapper.findAll('button').find((b) => b.text().trim() === text)
  if (!hit) throw new Error(`界面上没有文字为「${text}」的按钮`)
  return hit
}

/** 三个页签按钮（role="tab"）。 */
export function tabButtons(wrapper) {
  return wrapper.findAll('[role="tab"]')
}

/** 当前选中的页签 key（读 aria-selected，不读样式类）。 */
export function selectedTab(wrapper) {
  const t = tabButtons(wrapper).find((b) => b.attributes('aria-selected') === 'true')
  return t ? t.attributes('id').replace('admin-tab-', '') : null
}

/** 切到某个页签并等它的请求 settle。 */
export async function gotoTab(wrapper, label) {
  const t = tabButtons(wrapper).find((b) => b.text().trim() === label)
  if (!t) throw new Error(`没有名为「${label}」的页签`)
  await t.trigger('click')
  await flushPromises()
}

/** 表格正文里有多少条数据行（不含表头与展开出来的编辑行）。 */
export function dataRowCount(wrapper) {
  return wrapper.findAll('tbody tr').filter(
    (tr) => !tr.attributes('id')?.startsWith('quota-editor-') && !tr.text().includes('确认删除'),
  ).length
}

/** 界面全文。判「某句话有没有被渲染出来」时用它。 */
export function screenText(wrapper) {
  return wrapper.text()
}

/**
 * 按标签文字找到那个复选框。
 *
 * 后台的标签控件是 `<label>` 包一个 `sr-only` 的 `<input type="checkbox">`，
 * **不是 button**——按按钮找会得到 undefined，而 `find(...).text()` 那时
 * 恰好是空串，断言「不包含 X」直接绿。这正是 AGENTS.md 记的
 * 「断言压根没跑到，却报绿」那一族。
 */
export function tagCheckbox(wrapper, labelText) {
  const label = wrapper.findAll('label').find((l) => l.text().trim() === labelText)
  if (!label) throw new Error(`界面上没有文字为「${labelText}」的标签控件`)
  const box = label.find('input[type="checkbox"]')
  if (!box.exists()) throw new Error(`标签「${labelText}」里没有 checkbox`)
  return box
}
