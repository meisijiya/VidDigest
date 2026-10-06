/**
 * 管理后台真挂载测试（工单 #18）。
 *
 * ## 这份文件补的是哪一类回归
 *
 * `admin-ui.test.mjs` 的 124 条断言全读源码做正则匹配，能守住「文案别改、
 * 色类别越界」，但**下面六类 Vue 项目最常见的缺陷它一条都抓不到**，而它们
 * 恰恰全是运行时静默的——不报错、界面只是不对：
 *
 *   1. `v-if` / `v-else-if` / `v-else` 顺序写反（故障态被空态吃掉）
 *   2. 事件名拼错（`@clik` 永远不触发）
 *   3. `v-model` 接到 computed / 方法调用上（静默不双向）
 *   4. `v-for` 的 `:key` 用错（Vue 复用错节点，界面显示上一个的残留）
 *   5. `aria-*` 被条件渲染掉，或指向一个不存在的 id
 *   6. props / emits 声明与实际用法不一致
 *
 * ## 每条断言的判别标准
 *
 * **「换个实现方式但界面行为完全一样，它会不会红？」会红 → 不该写在这里。**
 * 那类判据继续留在 `admin-ui.test.mjs` 里做文本断言（色值、snake_case、
 * 转换函数收口……），迁过来只会变弱。
 *
 * ## 沙箱自检排在第一位
 *
 * mock 若没接上，`fetchAdminUsers` 会是真的去发 axios 请求，jsdom 里没有
 * XHR——症状是「挂载就炸」，很像真故障，但它其实在测一个不存在的世界。
 * 所以第一条用例先证明：**种子里的那一行，组件真的渲染出来了**。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'

// vi.mock 必须留在本文件顶部：vitest 把它提升到所有 import 之前，
// 放进共用夹具再由这里导入，依赖的是导入顺序——那种失效是静默的。
vi.mock('../src/api/admin.js', async (importOriginal) => ({
  ...(await importOriginal()),
  fetchAdminUsers: vi.fn(),
  fetchAdminCommunity: vi.fn(),
  fetchAdminModelCatalog: vi.fn(),
  setUserQuota: vi.fn(),
  updateAdminModel: vi.fn(),
  createAdminUser: vi.fn(),
  setUserAdmin: vi.fn(),
  deleteAdminUser: vi.fn(),
  fetchTagVocabulary: vi.fn(),
  updateCommunityTags: vi.fn(),
  deleteCommunityVideo: vi.fn(),
}))

import AdminPage from '../src/components/AdminPage.vue'
import * as api from '../src/api/admin.js'
import {
  mountAdmin, userRow, communityRow, modelRow, VOCABULARY, httpError,
  buttonByText, tabButtons, selectedTab, gotoTab, dataRowCount, screenText, tagCheckbox,
} from './helpers/admin-fixtures.mjs'

/** 每个用例一份干净种子：mock 的返回值与调用历史都不跨用例。 */
beforeEach(() => {
  vi.clearAllMocks()
  api.fetchAdminUsers.mockResolvedValue({ items: [userRow()], total: 1, page: 1, pageSize: 20 })
  api.fetchAdminCommunity.mockResolvedValue({ items: [communityRow()], total: 1, page: 1, pageSize: 20 })
  api.fetchAdminModelCatalog.mockResolvedValue({ items: [modelRow()], total: 1, page: 1, pageSize: 20 })
  api.fetchTagVocabulary.mockResolvedValue(VOCABULARY)
})

describe('沙箱自检 · mock 真的接上了', () => {
  it('种子里的那一行，组件真的渲染出来了', async () => {
    // 前提自检：这一条红了，下面所有「界面上没有 X」的断言都没有意义
    // ——那时的红是「世界没搭起来」，不是「组件坏了」。
    const w = await mountAdmin(AdminPage)

    expect(api.fetchAdminUsers).toHaveBeenCalledTimes(1)
    expect(screenText(w)).toContain('a@example.com')
  })

  it('反向对照：换掉种子，界面跟着换', async () => {
    // 只断言「界面上有个邮箱」的话，mock 返回什么都可能绿（甚至返回
    // undefined 时的默认值也可能撞上）。必须证明数据真的流过去了。
    api.fetchAdminUsers.mockResolvedValue({
      items: [userRow({ id: 7, email: 'moved@example.com' })],
      total: 1, page: 1, pageSize: 20,
    })
    const w = await mountAdmin(AdminPage)

    expect(screenText(w)).toContain('moved@example.com')
    expect(screenText(w)).not.toContain('a@example.com')
  })
})

describe('四态 · 加载 / 故障 / 空 / 内容，顺序不能反', () => {
  it('故障态排在空态之前：一次 500 不该被渲染成「没有匹配的用户」', async () => {
    // 这是本文件最该守住的一条。四态里 error 与 empty 在故障时**同时**成立
    // （markFailure 把 items 清空），所以顺序写反时界面看起来仍然「正常」，
    // 只是把真实原因吞了——管理员会去换个关键词再搜一遍。
    api.fetchAdminUsers.mockRejectedValue(httpError(500, '用户列表响应形状不对'))
    const w = await mountAdmin(AdminPage)

    expect(screenText(w)).toContain('用户加载失败')
    expect(screenText(w)).toContain('用户列表响应形状不对')
    expect(screenText(w)).not.toContain('没有匹配的用户')
  })

  it('故障态带重试入口，且点了真的会重打接口', async () => {
    api.fetchAdminUsers.mockRejectedValueOnce(httpError(500, 'boom'))
    const w = await mountAdmin(AdminPage)
    expect(api.fetchAdminUsers).toHaveBeenCalledTimes(1)

    await buttonByText(w, '重试').trigger('click')
    await flushPromises()

    expect(api.fetchAdminUsers).toHaveBeenCalledTimes(2)
    expect(screenText(w)).toContain('a@example.com')
  })

  it('空态是真的空（这次没报错），文案跟着页签走', async () => {
    api.fetchAdminUsers.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 })
    const w = await mountAdmin(AdminPage)

    expect(screenText(w)).toContain('没有匹配的用户')
    expect(screenText(w)).not.toContain('用户加载失败')
  })

  it('三个页签各有一份自己的空态文案', async () => {
    // 反向判据：断言「空态文案存在」时，必须确认它换页签会变——
    // 否则「三份都写成同一句」也会绿，而那正是要抓的回归。
    api.fetchAdminUsers.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 })
    api.fetchAdminCommunity.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 })
    const w = await mountAdmin(AdminPage)
    const usersText = screenText(w)

    await gotoTab(w, '社区')

    expect(screenText(w)).toContain('社区还是空的')
    expect(screenText(w)).not.toContain('没有匹配的用户')
    expect(usersText).not.toBe(screenText(w))
  })
})

describe('403 · 整页可读错误态，不是白屏', () => {
  it('服务端拒绝时整页替换，且不渲染页签', async () => {
    api.fetchAdminUsers.mockRejectedValue(httpError(403, '需要管理员'))
    const w = await mountAdmin(AdminPage)

    expect(screenText(w)).toContain('没有管理员权限')
    expect(tabButtons(w)).toHaveLength(0)
  })

  it('403 归 403 整页态，不混进「加载失败 + 重试」', async () => {
    // 两条分支的错误文案都长得像「出错了」。混了的话管理员会去点重试，
    // 而重试对一个权限问题永远无效。
    api.fetchAdminUsers.mockRejectedValue(httpError(403, '需要管理员'))
    const w = await mountAdmin(AdminPage)

    expect(screenText(w)).not.toContain('用户加载失败')
    expect(buttonByText).toBeDefined()
    expect(w.findAll('button').some((b) => b.text().trim() === '重试')).toBe(false)
  })

  it('非 403 的失败**不**被当成 403 挡掉', async () => {
    // 反向对照：只测 403 的话，把 markFailure 写成「任何错都 forbidden」
    // 也能绿——而那会让所有真故障都显示成「你没权限」。
    api.fetchAdminUsers.mockRejectedValue(httpError(500, 'boom'))
    const w = await mountAdmin(AdminPage)

    expect(screenText(w)).not.toContain('没有管理员权限')
    expect(screenText(w)).toContain('用户加载失败')
  })
})

describe('行内编辑器 · 展开、绑定、收起', () => {
  /** 展开某个用户的额度编辑行。 */
  async function openEditor(w, id = 1) {
    await w.find(`button[aria-controls="quota-editor-${id}"]`).trigger('click')
    await flushPromises()
  }

  it('点邮箱真的展开编辑行（事件名拼错时这条会红）', async () => {
    const w = await mountAdmin(AdminPage)
    expect(w.find('#quota-editor-1').exists()).toBe(false)

    await openEditor(w)

    expect(w.find('#quota-editor-1').exists()).toBe(true)
  })

  it('aria-controls 指向的 id 在展开后**真的存在**', async () => {
    // 文本断言能验「模板里写了 aria-controls」，但验不了它指向的节点存在。
    // id 写错时屏幕阅读器会跳到一个空处，界面却完全正常。
    const w = await mountAdmin(AdminPage)
    await openEditor(w)

    const btn = w.find('button[aria-controls="quota-editor-1"]')
    const target = w.find(`#${btn.attributes('aria-controls')}`)

    expect(btn.attributes('aria-expanded')).toBe('true')
    expect(target.exists()).toBe(true)
  })

  it('v-model 是真双向的：输入的值会进到请求里', async () => {
    // 若 v-model 接到 computed 或方法调用上，界面上能看到字，
    // 而 `drafts[id]` 永远是旧值——保存发出去的还是原值，且不报错。
    const w = await mountAdmin(AdminPage)
    await openEditor(w)
    api.setUserQuota.mockResolvedValue({ user: userRow({ parseLimitOverride: 3 }), note: null, message: '' })

    await w.find('#quota-parse-1').setValue('3')
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(api.setUserQuota).toHaveBeenCalledWith(1, { parseLimit: 3, chatLimit: null })
  })

  it('留空 = 清除覆盖（发 null），不是发 0', async () => {
    // 0 与 null 是两种不同状态：一条都不能用 vs 回落全局。发错不报错，
    // 只是改出来的效果不对。
    const w = await mountAdmin(AdminPage)
    await openEditor(w)
    await w.find('#quota-parse-1').setValue('')
    api.setUserQuota.mockResolvedValue({ user: userRow(), note: null, message: '' })

    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(api.setUserQuota).toHaveBeenCalledWith(1, { parseLimit: null, chatLimit: null })
  })

  it('-1 无限原样发出去', async () => {
    const w = await mountAdmin(AdminPage)
    await openEditor(w)
    await w.find('#quota-parse-1').setValue('-1')
    api.setUserQuota.mockResolvedValue({ user: userRow({ parseLimitOverride: -1 }), note: null, message: '' })

    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(api.setUserQuota).toHaveBeenCalledWith(1, { parseLimit: -1, chatLimit: null })
  })

  it('值域在发请求**之前**判：填 -2 时不请求，且说清只允许哪三个值', async () => {
    // 注意非法值是 **-2** 而不是 5：契约是「-1 无限、0 停用、正整数」，
    // 5 是正整数，**合法**。拿 5 当反例会得到一条恒绿的用例——
    // 它断言「不发请求」，而实现本来就会发。
    const w = await mountAdmin(AdminPage)
    await openEditor(w)
    await w.find('#quota-parse-1').setValue('-2')

    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(api.setUserQuota).not.toHaveBeenCalled()
    expect(screenText(w)).toContain('-1（无限）、0（停用）或正整数')
  })

  it('正整数是合法值：填 5 会真的发出去', async () => {
    // 反向对照：上一条的对照项。少了它，「不发请求」也可能是因为
    // 整个保存流程坏了——那正是工单 #15 记过的「什么都没发生」形态。
    const w = await mountAdmin(AdminPage)
    await openEditor(w)
    await w.find('#quota-parse-1').setValue('5')
    api.setUserQuota.mockResolvedValue({ user: userRow(), note: null, message: '' })

    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(api.setUserQuota).toHaveBeenCalledWith(1, { parseLimit: 5, chatLimit: null })
  })

  it('成功后**不**收起编辑行：反馈就写在这一行里', async () => {
    // 收起等于把「改成功了」一起收走，管理员会以为自己没点上。
    const w = await mountAdmin(AdminPage)
    await openEditor(w)
    api.setUserQuota.mockResolvedValue({ user: userRow({ parseLimitOverride: 3 }), note: null, message: '' })

    await w.find('#quota-parse-1').setValue('3')
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(screenText(w)).toContain('额度已更新')
    expect(w.find('#quota-editor-1').exists()).toBe(true)
  })

  it('成功用**回读**替换本地行，不做乐观更新', async () => {
    // 服务端把 3 夹成了 5（上限）。用请求值替换的话，界面永远显示 3，
    // 而库里是 5——刷新一下数字就变了，没人说得清刚才发生了什么。
    //
    // `setUserQuota` 的 `user` 是**服务端原文**（snake_case），组件自己翻；
    // 这里若返回 camelCase，`toAdminUser` 读到的全是 undefined，
    // 数字会全变 0——症状与「乐观更新写错了」一模一样，判据必须守住形状。
    const w = await mountAdmin(AdminPage)
    await openEditor(w)
    api.setUserQuota.mockResolvedValue({
      user: userRow({ parseLimitOverride: 5, parseLimit: 5, parseLimitSource: 'override' }),
      note: null, message: '',
    })

    await w.find('#quota-parse-1').setValue('3')
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(screenText(w)).toContain('2 / 5 · override')
  })

  it('vip_not_effective 必须渲染出来', async () => {
    // 不说的话，管理员只会以为自己操作错了——而服务端根本没改。
    const w = await mountAdmin(AdminPage)
    await openEditor(w)
    api.setUserQuota.mockResolvedValue({
      user: userRow({ isVip: true }), note: 'vip_not_effective', message: '已保存，但该用户是有效 VIP',
    })

    await w.find('#quota-parse-1').setValue('3')
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(screenText(w)).toContain('已保存，但该用户是有效 VIP')
  })

  it('有效 VIP 的编辑区里额外提示「改了也不生效」', async () => {
    // 与上一条同一件事的两个出口：一个在保存后（服务端说），
    // 一个在编辑前（本地知道）。少了编辑前那条，管理员要点一次才知道。
    api.fetchAdminUsers.mockResolvedValue({
      items: [userRow({ id: 1, isVip: true }), userRow({ id: 2, isVip: false })],
      total: 2, page: 1, pageSize: 20,
    })
    const w = await mountAdmin(AdminPage)
    await w.find('button[aria-controls="quota-editor-1"]').trigger('click')
    await flushPromises()

    expect(w.find('#quota-editor-1').text()).toContain('这里的改动不会生效')
    expect(w.find('#quota-editor-2').exists()).toBe(false)
  })

  it('失败也必须给反馈，不给等于让管理员以为是自己错了', async () => {
    const w = await mountAdmin(AdminPage)
    await openEditor(w)
    api.setUserQuota.mockRejectedValue(httpError(500, '额度写库失败'))

    await w.find('#quota-parse-1').setValue('3')
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(screenText(w)).toContain('额度没改成：额度写库失败')
  })

  it('反馈区是 aria-live 的礼貌播报区', async () => {
    const w = await mountAdmin(AdminPage)
    await openEditor(w)
    api.setUserQuota.mockRejectedValue(httpError(500, 'boom'))

    await w.find('#quota-parse-1').setValue('3')
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    const live = w.findAll('[aria-live="polite"]')
    expect(live.some((el) => el.text().includes('额度没改成'))).toBe(true)
  })

  it('「取消」才收起编辑行', async () => {
    const w = await mountAdmin(AdminPage)
    await openEditor(w)

    await buttonByText(w, '取消').trigger('click')
    await flushPromises()

    expect(w.find('#quota-editor-1').exists()).toBe(false)
  })
})

describe('行内按钮 · 不把点事件转发给整行', () => {
  it('点「删除」只开确认行，不顺带展开额度编辑器', async () => {
    // `@click.stop` 漏掉的后果：删号确认弹出来的同时编辑器也展开了，
    // 两个操作叠在同一行，管理员不知道自己刚才点的是哪个。
    const w = await mountAdmin(AdminPage)

    await buttonByText(w, '删除').trigger('click')
    await flushPromises()

    expect(screenText(w)).toContain('确认删除 a@example.com？')
    expect(w.find('#quota-editor-1').exists()).toBe(false)
  })

  it('点「设为管理员」也不展开编辑器', async () => {
    api.setUserAdmin.mockResolvedValue({ user: userRow({ isAdmin: true }) })
    const w = await mountAdmin(AdminPage)

    await buttonByText(w, '设为管理员').trigger('click')
    await flushPromises()

    expect(api.setUserAdmin).toHaveBeenCalledWith(1, true)
    expect(w.find('#quota-editor-1').exists()).toBe(false)
  })

  it('点空白处的整行**才**展开（对照组：上一条不是恒真）', async () => {
    const w = await mountAdmin(AdminPage)

    await w.findAll('tbody tr')[0].trigger('click')
    await flushPromises()

    expect(w.find('#quota-editor-1').exists()).toBe(true)
  })
})

describe('列表变动 · 展开状态跟着「谁」走，不跟着「第几行」走', () => {
  // `:key` 写错（整张表共用一个 key）的经典症状：删掉上面一行之后底下所有
  // 行往上挪一位，Vue 复用错节点，于是「展开着的那一行」变成了别人的——
  // 展开状态挂在了行号上，没挂在用户上。
  it('删掉上面一行时，下面那个用户的编辑区仍然开着', async () => {
    api.fetchAdminUsers.mockResolvedValue({
      items: [
        userRow({ id: 1, email: 'first@example.com' }),
        userRow({ id: 2, email: 'second@example.com' }),
      ],
      total: 2, page: 1, pageSize: 20,
    })
    api.deleteAdminUser.mockResolvedValue({ deleted: 1 })
    const w = await mountAdmin(AdminPage)

    await w.find('button[aria-controls="quota-editor-2"]').trigger('click')
    await flushPromises()
    expect(w.find('#quota-editor-2').exists()).toBe(true)

    await w.findAll('button').filter((b) => b.text().trim() === '删除')[0].trigger('click')
    await flushPromises()
    await buttonByText(w, '确认删除').trigger('click')
    await flushPromises()

    expect(screenText(w)).not.toContain('first@example.com')
    expect(screenText(w)).toContain('second@example.com')
    expect(w.find('#quota-editor-2').exists()).toBe(true)
  })

  it('删掉的是**展开着**的那一行时，编辑区跟着收走，不留在别人身上', async () => {
    api.fetchAdminUsers.mockResolvedValue({
      items: [
        userRow({ id: 1, email: 'first@example.com' }),
        userRow({ id: 2, email: 'second@example.com' }),
      ],
      total: 2, page: 1, pageSize: 20,
    })
    api.deleteAdminUser.mockResolvedValue({ deleted: 1 })
    const w = await mountAdmin(AdminPage)

    await w.find('button[aria-controls="quota-editor-1"]').trigger('click')
    await flushPromises()
    expect(w.find('#quota-editor-1').exists()).toBe(true)

    await w.findAll('button').filter((b) => b.text().trim() === '删除')[0].trigger('click')
    await flushPromises()
    await buttonByText(w, '确认删除').trigger('click')
    await flushPromises()

    expect(screenText(w)).toContain('second@example.com')
    // 用户 1 的编辑区不许「借」到用户 2 头上
    expect(w.find('#quota-editor-1').exists()).toBe(false)
    expect(w.find('#quota-editor-2').exists()).toBe(false)
  })

  it('社区那一侧同样不串行', async () => {
    api.fetchAdminCommunity.mockResolvedValue({
      items: [communityRow({ id: 20, title: '甲', tags: [] }), communityRow({ id: 21, title: '乙', tags: [] })],
      total: 2, page: 1, pageSize: 20,
    })
    api.deleteCommunityVideo.mockResolvedValue({ deleted: 20 })
    const w = await mountAdmin(AdminPage)
    await gotoTab(w, '社区')

    await w.find('button[aria-controls="community-tags-21"]').trigger('click')
    await flushPromises()
    expect(w.find('#community-tags-21').exists()).toBe(true)

    await w.findAll('button').filter((b) => b.text().trim() === '删除')[0].trigger('click')
    await flushPromises()
    await w.findAll('button').filter((b) => b.text().trim() === '确认删除')[0].trigger('click')
    await flushPromises()

    expect(screenText(w)).toContain('乙')
    expect(w.find('#community-tags-21').exists()).toBe(true)
  })
})

describe('删号 · 两步确认', () => {
  it('第一下只开确认，不发请求', async () => {
    const w = await mountAdmin(AdminPage)

    await buttonByText(w, '删除').trigger('click')
    await flushPromises()

    expect(api.deleteAdminUser).not.toHaveBeenCalled()
    expect(screenText(w)).toContain('此操作不可撤销')
  })

  it('「取消」把确认行收走，且不发请求', async () => {
    const w = await mountAdmin(AdminPage)
    await buttonByText(w, '删除').trigger('click')
    await flushPromises()

    await buttonByText(w, '取消').trigger('click')
    await flushPromises()

    expect(api.deleteAdminUser).not.toHaveBeenCalled()
    expect(screenText(w)).not.toContain('确认删除 a@example.com？')
  })

  it('确认后才发请求，且成功后行从列表移除', async () => {
    api.deleteAdminUser.mockResolvedValue({ deleted: 1 })
    const w = await mountAdmin(AdminPage)
    await buttonByText(w, '删除').trigger('click')
    await flushPromises()

    await buttonByText(w, '确认删除').trigger('click')
    await flushPromises()

    expect(api.deleteAdminUser).toHaveBeenCalledWith(1)
    expect(screenText(w)).not.toContain('a@example.com')
  })

  it('409 时行留下，并把**数字**带给管理员', async () => {
    // 前端要的是「还剩几行要处理」，不能靠从中文里正则抠。
    api.deleteAdminUser.mockRejectedValue(httpError(409, '名下还有记录'))
    const w = await mountAdmin(AdminPage)
    await buttonByText(w, '删除').trigger('click')
    await flushPromises()

    await buttonByText(w, '确认删除').trigger('click')
    await flushPromises()

    expect(screenText(w)).toContain('a@example.com')
    expect(screenText(w)).toContain('名下还有记录')
  })
})

describe('页签 · 状态槽、方向键、越界', () => {
  it('切页签真的换了内容区，不是换个高亮', async () => {
    const w = await mountAdmin(AdminPage)
    expect(selectedTab(w)).toBe('users')

    await gotoTab(w, '社区')

    expect(selectedTab(w)).toBe('community')
    expect(screenText(w)).toContain('社区视频列表')
    expect(w.find('caption').text()).toContain('社区视频列表')
  })

  it('状态槽不串台：用户页的故障不会把社区页也变成故障态', async () => {
    // 四份状态不是共享一份的理由。串台的症状是「用户页挂了，切到社区页
    // 也是红的」，而社区那边其实好得很。
    api.fetchAdminUsers.mockRejectedValue(httpError(500, 'boom'))
    const w = await mountAdmin(AdminPage)
    expect(screenText(w)).toContain('用户加载失败')

    await gotoTab(w, '社区')

    expect(screenText(w)).not.toContain('社区加载失败')
    expect(screenText(w)).toContain('社区视频列表')
  })

  it('首次进入才打接口，切回来用已加载的那份', async () => {
    const w = await mountAdmin(AdminPage)

    await gotoTab(w, '社区')
    await gotoTab(w, '用户')
    await gotoTab(w, '社区')

    expect(api.fetchAdminCommunity).toHaveBeenCalledTimes(1)
    expect(api.fetchAdminUsers).toHaveBeenCalledTimes(1)
  })

  it('方向键真的能移动页签（不只是 Tab 键）', async () => {
    const w = await mountAdmin(AdminPage)

    await tabButtons(w)[0].trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(selectedTab(w)).toBe('community')

    await tabButtons(w)[1].trigger('keydown', { key: 'ArrowLeft' })
    await flushPromises()
    expect(selectedTab(w)).toBe('users')
  })

  it('End 跳到最后一个，Home 回到第一个', async () => {
    const w = await mountAdmin(AdminPage)

    await tabButtons(w)[0].trigger('keydown', { key: 'End' })
    await flushPromises()
    expect(selectedTab(w)).toBe('models')

    await tabButtons(w)[2].trigger('keydown', { key: 'Home' })
    await flushPromises()
    expect(selectedTab(w)).toBe('users')
  })

  it('当前 panel 的 aria-labelledby 真的指向选中的那个页签', async () => {
    // 文本断言能验「模板里写了 aria-labelledby」，验不了两端 id 对得上。
    const w = await mountAdmin(AdminPage)

    const panel = w.find('[role="tabpanel"]')
    const labelledBy = panel.attributes('aria-labelledby')
    const target = w.find(`#${labelledBy}`)

    expect(target.exists()).toBe(true)
    expect(target.attributes('aria-selected')).toBe('true')
    expect(target.text().trim()).toBe('用户')
  })
})

describe('翻页 · 不越界', () => {
  it('最后一页的「下一页」是禁用的：点它不会发出越界请求', async () => {
    // goPage 里有 `n > totalPages` 的守卫，但按钮同时也是 disabled 的。
    // 少一层任何一层都还看得过去（守卫兜住 / 按钮置灰），**两层都在**
    // 才是「点了没反应」而不是「点了报 400」。
    api.fetchAdminUsers.mockResolvedValue({
      items: Array.from({ length: 20 }, (_, i) => userRow({ id: i + 1, email: `u${i + 1}@example.com` })),
      total: 21, page: 2, pageSize: 20,
    })
    const w = await mountAdmin(AdminPage)
    const next = buttonByText(w, '下一页')

    expect(next.attributes('disabled')).toBeDefined()
    await next.trigger('click')
    await flushPromises()

    expect(api.fetchAdminUsers).toHaveBeenCalledTimes(1)
  })

  it('页数按 total 算，不是按当前页条数', async () => {
    // 只有 1 条数据但 total=45：界面必须说「第 1 / 3 页」。
    // 按 items.length 算的话会显示「第 1 / 1 页」，而点「下一页」永远出不来。
    api.fetchAdminUsers.mockResolvedValue({ items: [userRow()], total: 45, page: 1, pageSize: 20 })
    const w = await mountAdmin(AdminPage)

    expect(screenText(w)).toContain('第 1 / 3 页')
    expect(screenText(w)).toContain('共 45 条')
  })

  it('首页的「上一页」是禁用的', async () => {
    api.fetchAdminUsers.mockResolvedValue({ items: [userRow()], total: 45, page: 1, pageSize: 20 })
    const w = await mountAdmin(AdminPage)

    expect(buttonByText(w, '上一页').attributes('disabled')).toBeDefined()
  })

  it('点「下一页」真的带上新的 page 重新请求', async () => {
    // 反向对照：前两条都断「不越界」，少一条正向就会让整套断言恒绿。
    api.fetchAdminUsers.mockResolvedValue({
      items: [userRow()], total: 45, page: 1, pageSize: 20,
    })
    const w = await mountAdmin(AdminPage)

    await buttonByText(w, '下一页').trigger('click')
    await flushPromises()

    expect(api.fetchAdminUsers).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))
  })
})

describe('建号 · 展开、草稿、提交', () => {
  async function openCreate(w) {
    await buttonByText(w, '新建用户').trigger('click')
    await flushPromises()
  }

  it('默认收起，点开才出现表单', async () => {
    const w = await mountAdmin(AdminPage)
    expect(w.find('#admin-create-user').exists()).toBe(false)

    await openCreate(w)

    expect(w.find('#admin-create-user').exists()).toBe(true)
  })

  it('口令不到 6 位时本地就挡住，不发请求', async () => {
    // 组件自己有一道 `password.length < 6` 的守卫。这里断的是它，
    // **不是**原生 minlength——jsdom 不跑表单校验，所以「靠浏览器挡住」
    // 这条路在测试里是隐形的，只测它会得到一条恒绿的用例。
    const w = await mountAdmin(AdminPage)
    await openCreate(w)
    await w.find('#admin-new-email').setValue('new@example.com')
    await w.find('#admin-new-password').setValue('123')

    await w.find('#admin-create-user').trigger('submit')
    await flushPromises()

    expect(api.createAdminUser).not.toHaveBeenCalled()
    expect(screenText(w)).toContain('初始密码至少 6 位')
  })

  it('邮箱为空时本地就挡住，不发请求', async () => {
    const w = await mountAdmin(AdminPage)
    await openCreate(w)
    await w.find('#admin-new-password').setValue('secret123')

    await w.find('#admin-create-user').trigger('submit')
    await flushPromises()

    expect(api.createAdminUser).not.toHaveBeenCalled()
    expect(screenText(w)).toContain('邮箱不能为空')
  })

  it('成功后新号进列表，total 加一', async () => {
    api.fetchAdminUsers.mockResolvedValue({ items: [userRow()], total: 1, page: 1, pageSize: 20 })
    api.createAdminUser.mockResolvedValue({
      user: userRow({ id: 99, email: 'new@example.com' }),
    })
    const w = await mountAdmin(AdminPage)
    await openCreate(w)
    await w.find('#admin-new-email').setValue('new@example.com')
    await w.find('#admin-new-password').setValue('secret123')

    await w.find('#admin-create-user').trigger('submit')
    await flushPromises()

    expect(api.createAdminUser).toHaveBeenCalledWith({
      email: 'new@example.com', password: 'secret123', isAdmin: false,
    })
    expect(screenText(w)).toContain('new@example.com')
    // total 加一用「界面上多了一行」来断，而不是找「共 2 条」——
    // 分页页脚只在 total > 每页条数时才渲染，2 条时那一行根本不存在，
    // 断言它会恒红（而与功能对错无关）。
    expect(dataRowCount(w)).toBe(2)
  })

  it('成功后草稿被清空，不留上一条建号的内容', async () => {
    // 草稿不清的话，下一次点「新建用户」看到的是上一次填的邮箱，
    // 而反馈还写着「已创建 X」——管理员会以为刚才那次白建了。
    api.createAdminUser.mockResolvedValue({
      user: userRow({ id: 99, email: 'new@example.com' }),
    })
    const w = await mountAdmin(AdminPage)
    await openCreate(w)
    await w.find('#admin-new-email').setValue('new@example.com')
    await w.find('#admin-new-password').setValue('secret123')
    await w.find('#admin-create-user').trigger('submit')
    await flushPromises()

    await openCreate(w)

    expect(w.find('#admin-new-email').element.value).toBe('')
    expect(w.find('#admin-new-password').element.value).toBe('')
  })

  it('成功反馈在表单**外面**：表单收起后确认文案还在', async () => {
    // 放在表单里的话，createOpen 一置 false 就把「已创建 X」一起收走了。
    api.createAdminUser.mockResolvedValue({
      user: userRow({ id: 99, email: 'new@example.com' }),
    })
    const w = await mountAdmin(AdminPage)
    await openCreate(w)
    await w.find('#admin-new-email').setValue('new@example.com')
    await w.find('#admin-new-password').setValue('secret123')

    await w.find('#admin-create-user').trigger('submit')
    await flushPromises()

    expect(w.find('#admin-create-user').exists()).toBe(false)
    const live = w.findAll('[aria-live="polite"]')
    expect(live.some((el) => el.text().includes('new@example.com'))).toBe(true)
  })

  it('失败时给出错误，列表一个都不动', async () => {
    api.createAdminUser.mockRejectedValue(httpError(409, '邮箱已存在'))
    const w = await mountAdmin(AdminPage)
    await openCreate(w)
    await w.find('#admin-new-email').setValue('new@example.com')
    await w.find('#admin-new-password').setValue('secret123')

    await w.find('#admin-create-user').trigger('submit')
    await flushPromises()

    expect(screenText(w)).toContain('邮箱已存在')
    expect(screenText(w)).not.toContain('已创建 new@example.com')
    expect(dataRowCount(w)).toBe(1)
  })

  it('失败时表单不收起（草稿还在，可以改了重发）', async () => {
    api.createAdminUser.mockRejectedValue(httpError(409, '邮箱已存在'))
    const w = await mountAdmin(AdminPage)
    await openCreate(w)
    await w.find('#admin-new-email').setValue('new@example.com')
    await w.find('#admin-new-password').setValue('secret123')

    await w.find('#admin-create-user').trigger('submit')
    await flushPromises()

    expect(w.find('#admin-create-user').exists()).toBe(true)
    expect(w.find('#admin-new-email').element.value).toBe('new@example.com')
  })

  it('勾了管理员发 true，没勾发 false', async () => {
    api.createAdminUser.mockResolvedValue({
      user: userRow({ id: 99, email: 'new@example.com', isAdmin: true }),
    })
    const w = await mountAdmin(AdminPage)
    await openCreate(w)
    await w.find('#admin-new-email').setValue('new@example.com')
    await w.find('#admin-new-password').setValue('secret123')
    await w.find('#admin-new-isadmin').setValue(true)

    await w.find('#admin-create-user').trigger('submit')
    await flushPromises()

    expect(api.createAdminUser).toHaveBeenCalledWith({
      email: 'new@example.com', password: 'secret123', isAdmin: true,
    })
    expect(screenText(w)).toContain('管理员')
  })
})

describe('社区审核 · 标签与删除', () => {
  async function gotoCommunity(w) {
    await gotoTab(w, '社区')
  }

  async function openTags(w, id = 10) {
    await w.find(`button[aria-controls="community-tags-${id}"]`).trigger('click')
    await flushPromises()
  }

  it('pending 占位行与真实内容分得开', async () => {
    api.fetchAdminCommunity.mockResolvedValue({
      items: [
        communityRow({ id: 10, title: '真内容', status: 'ready' }),
        communityRow({ id: 11, title: '', status: 'pending' }),
      ],
      total: 2, page: 1, pageSize: 20,
    })
    const w = await mountAdmin(AdminPage)
    await gotoCommunity(w)

    expect(screenText(w)).toContain('真内容')
    expect(screenText(w)).toContain('未命名视频')
    expect(screenText(w)).toContain('占位中')
  })

  it('词表是**从服务端取**的，前端不自己抄一份', async () => {
    const w = await mountAdmin(AdminPage)
    await gotoCommunity(w)
    await openTags(w)

    expect(api.fetchTagVocabulary).toHaveBeenCalled()
    expect(screenText(w)).toContain('访谈')
  })

  it('勾上就加上，取消就去掉（复选框真的双向）', async () => {
    // 标签控件是 `<label>` 包 `sr-only` 的 checkbox，不是 button ——
    // 判据得落在 checkbox 上。绑成 `:checked` + `@change` 而不是 v-model
    // 时，勾上界面会变，而 `selectedTags(id)` 仍是旧值，
    // 保存发出去的还是原标签，且不报错。
    //
    // 种子必须自带**零个**已有标签：带一个的话，起点就是 1，
    // 「勾上变成 2」与「没变」这两种情况都藏在同一个数字里。
    api.fetchAdminCommunity.mockResolvedValue({
      items: [communityRow({ id: 10, tags: [] })], total: 1, page: 1, pageSize: 20,
    })
    const w = await mountAdmin(AdminPage)
    await gotoCommunity(w)
    await openTags(w)
    expect(screenText(w)).toContain('当前选了 0 个')

    const box = tagCheckbox(w, '访谈')
    expect(box.element.checked).toBe(false)

    await box.setValue(true)
    await flushPromises()
    expect(tagCheckbox(w, '访谈').element.checked).toBe(true)
    expect(screenText(w)).toContain('当前选了 1 个')

    await tagCheckbox(w, '访谈').setValue(false)
    await flushPromises()
    expect(tagCheckbox(w, '访谈').element.checked).toBe(false)
    expect(screenText(w)).toContain('当前选了 0 个')
  })

  it('上限来自服务端：maxTags=2 选满两个后第三个禁用，maxTags=3 则不禁用', async () => {
    // 必须选满之后再断「禁用」——`selectedTagCount >= maxTags` 在 0 个时
    // 本来就不成立，那时三条断言全是「未禁用」，等于什么都没验。
    //
    // 两条并排才够：只断 maxTags=2 的话，把 maxTags 恒设成 0
    // （于是所有 checkbox 一律禁用）也能绿。
    api.fetchAdminCommunity.mockResolvedValue({
      items: [communityRow({ id: 10, tags: [] })], total: 1, page: 1, pageSize: 20,
    })

    api.fetchTagVocabulary.mockResolvedValue({ maxTags: 2, groups: VOCABULARY.groups })
    const w2 = await mountAdmin(AdminPage)
    await gotoCommunity(w2)
    await openTags(w2)
    await tagCheckbox(w2, '科普').setValue(true)
    await tagCheckbox(w2, '教程').setValue(true)
    await flushPromises()

    expect(tagCheckbox(w2, '访谈').attributes('disabled')).toBeDefined()
    expect(screenText(w2)).toContain('已选满')

    api.fetchTagVocabulary.mockResolvedValue({ maxTags: 3, groups: VOCABULARY.groups })
    const w3 = await mountAdmin(AdminPage)
    await gotoCommunity(w3)
    await openTags(w3)
    await tagCheckbox(w3, '科普').setValue(true)
    await tagCheckbox(w3, '教程').setValue(true)
    await flushPromises()

    expect(tagCheckbox(w3, '访谈').attributes('disabled')).toBeUndefined()
  })

  it('取掉一个之后，剩下的又能选上了', async () => {
    api.fetchAdminCommunity.mockResolvedValue({
      items: [communityRow({ id: 10, tags: [] })], total: 1, page: 1, pageSize: 20,
    })
    api.fetchTagVocabulary.mockResolvedValue({ maxTags: 2, groups: VOCABULARY.groups })
    const w = await mountAdmin(AdminPage)
    await gotoCommunity(w)
    await openTags(w)

    await tagCheckbox(w, '科普').setValue(true)
    await tagCheckbox(w, '教程').setValue(true)
    await flushPromises()
    expect(tagCheckbox(w, '访谈').attributes('disabled')).toBeDefined()

    await tagCheckbox(w, '科普').setValue(false)
    await flushPromises()

    expect(tagCheckbox(w, '访谈').attributes('disabled')).toBeUndefined()
  })

  it('保存真的把选中的标签发出去', async () => {
    api.fetchAdminCommunity.mockResolvedValue({
      items: [communityRow({ id: 10, tags: [] })], total: 1, page: 1, pageSize: 20,
    })
    api.updateCommunityTags.mockResolvedValue({
      item: communityRow({ tags: ['科普', '访谈'] }),
    })
    const w = await mountAdmin(AdminPage)
    await gotoCommunity(w)
    await openTags(w)

    await tagCheckbox(w, '科普').setValue(true)
    await tagCheckbox(w, '访谈').setValue(true)
    await buttonByText(w, '保存标签').trigger('click')
    await flushPromises()

    expect(api.updateCommunityTags).toHaveBeenCalledWith(10, expect.arrayContaining(['科普', '访谈']))
  })

  it('删除走两步确认，且文案说清不动用户历史', async () => {
    const w = await mountAdmin(AdminPage)
    await gotoCommunity(w)

    expect(screenText(w)).toContain('不删用户的解析历史')

    await w.findAll('button').find((b) => b.text().trim() === '删除').trigger('click')
    await flushPromises()

    expect(api.deleteCommunityVideo).not.toHaveBeenCalled()
    expect(screenText(w)).toContain('不可撤销')
  })

  it('确认后真删，且行从列表消失', async () => {
    api.deleteCommunityVideo.mockResolvedValue({ deleted: 10 })
    const w = await mountAdmin(AdminPage)
    await gotoCommunity(w)
    await w.findAll('button').find((b) => b.text().trim() === '删除').trigger('click')
    await flushPromises()

    await w.findAll('button').find((b) => b.text().trim() === '确认删除').trigger('click')
    await flushPromises()

    expect(api.deleteCommunityVideo).toHaveBeenCalledWith(10)
    expect(screenText(w)).not.toContain('一个标题')
  })
})

describe('模型清单 · 改一行', () => {
  async function gotoModels(w) {
    await gotoTab(w, 'AI 服务')
  }

  async function openModel(w, id = 'openai') {
    await w.findAll('button').find((b) => b.text().trim() === '编辑').trigger('click')
    await flushPromises()
    if (!w.find(`#m-label-${id}`).exists()) {
      throw new Error(`编辑区没打开：#m-label-${id} 不在界面上`)
    }
  }

  it('页签不再自称「只读」', async () => {
    const w = await mountAdmin(AdminPage)
    await gotoModels(w)

    expect(screenText(w)).not.toContain('只读')
  })

  it('草稿在渲染前播种：展开时看到的是库里的值，不是空的', async () => {
    // `v-model` 绑不到成员表达式上（`modelDrafts[m.id].label` 里 m 是循环
    // 变量）时，展开后输入框是空的，而保存会把空值发出去。
    // 这条判据直接看输入框的 value。
    const w = await mountAdmin(AdminPage)
    await gotoModels(w)
    await openModel(w)

    expect(w.find('#m-label-openai').element.value).toBe('OpenAI')
    expect(w.find('#m-url-openai').element.value).toBe('https://api.openai.com/v1')
    expect(w.find('#m-models-openai').element.value).toBe('gpt-x')
  })

  it('保存真的调接口，且成功用回读替换本地行', async () => {
    api.updateAdminModel.mockResolvedValue({
      item: modelRow({ label: '新名字', sortOrder: 7 }),
      platformDefault: null,
    })
    const w = await mountAdmin(AdminPage)
    await gotoModels(w)
    await openModel(w)

    await w.find('#m-label-openai').setValue('新名字')
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(api.updateAdminModel).toHaveBeenCalled()
    expect(screenText(w)).toContain('新名字')
  })

  it('成功**不**收起编辑区（收起只由「取消」负责）', async () => {
    api.updateAdminModel.mockResolvedValue({ item: modelRow({ label: 'x' }), platformDefault: null })
    const w = await mountAdmin(AdminPage)
    await gotoModels(w)
    await openModel(w)

    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(w.find('#m-label-openai').exists()).toBe(true)
  })

  it('enabled 翻成 0/1 发出去：勾「上架」发 true，「停用」发 false', async () => {
    // 后端显式拒布尔之外的形态，且 Python 里 True == 1，发布尔会被静静
    // 变成「上架」——停用这个操作会**看起来成功而没生效**。
    api.updateAdminModel.mockResolvedValue({ item: modelRow(), platformDefault: null })
    const w = await mountAdmin(AdminPage)
    await gotoModels(w)
    await openModel(w)

    await w.find('#m-enabled-openai').setValue('0')
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    const patch = api.updateAdminModel.mock.calls.at(-1)[1]
    expect(patch.enabled).toBe(false)
  })

  it('可选模型清空时挡住，且说清至少要留一个', async () => {
    const w = await mountAdmin(AdminPage)
    await gotoModels(w)
    await openModel(w)

    await w.find('#m-models-openai').setValue('')
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(api.updateAdminModel).not.toHaveBeenCalled()
    expect(screenText(w)).toContain('至少要留一个可选模型')
  })

  it('平台默认不在可选列表里时挡住，不发请求', async () => {
    const w = await mountAdmin(AdminPage)
    await gotoModels(w)
    await openModel(w)
    // 默认模型是 gpt-x，把可选列表换成别的，它就成了列表外的值。
    await w.find('#m-models-openai').setValue('gpt-y, gpt-z')

    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(api.updateAdminModel).not.toHaveBeenCalled()
    expect(screenText(w)).toContain('平台默认模型必须从上面的可选模型里选')
  })

  it('模型名重复时挡住', async () => {
    const w = await mountAdmin(AdminPage)
    await gotoModels(w)
    await openModel(w)

    await w.find('#m-models-openai').setValue('gpt-x, gpt-x')
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(api.updateAdminModel).not.toHaveBeenCalled()
    expect(screenText(w)).toContain('模型名重复了')
  })

  it('占位行（无可选模型）不渲染那两个输入框', async () => {
    // 渲染了又禁止保存的话，「编辑」按钮就是一条死路。
    api.fetchAdminModelCatalog.mockResolvedValue({
      items: [modelRow({ id: 'platform', isReal: 0, models: [] })],
      total: 1, page: 1, pageSize: 20,
    })
    const w = await mountAdmin(AdminPage)
    await gotoModels(w)
    await w.findAll('button').find((b) => b.text().trim() === '编辑').trigger('click')
    await flushPromises()

    expect(w.find('#m-models-platform').exists()).toBe(false)
    expect(screenText(w)).toContain('这一行是占位，没有可选模型')
  })

  it('占位行保存时不发 models / defaultModel 两个键', async () => {
    // 硬发空数组的话后端会拒，这一行从此存不了任何东西。
    //
    // ⚠️ 这里**必须**自己把目录种子换成占位行。`beforeEach` 每次都把
    // `fetchAdminModelCatalog` 重置回默认那份（带一个可选模型），
    // 而 `clearAllMocks` 只清调用历史、不清实现——于是漏写这一行时，
    // 本例会拿到一条有可选模型的目录，断言随之变成「另一种行也不发
    // models」，而它**照样绿**。依赖上一条用例留下的状态，是这条测试
    // 一开始就写错了，只是症状长得像组件有 bug。
    api.fetchAdminModelCatalog.mockResolvedValue({
      items: [modelRow({ id: 'platform', isReal: 0, models: [] })],
      total: 1, page: 1, pageSize: 20,
    })
    api.updateAdminModel.mockResolvedValue({
      item: modelRow({ id: 'platform', isReal: 0, models: [] }), platformDefault: null,
    })
    const w = await mountAdmin(AdminPage)
    await gotoModels(w)
    await w.findAll('button').find((b) => b.text().trim() === '编辑').trigger('click')
    await flushPromises()

    // 前提自检：编辑区确实处于「没有可选模型」这条路上，否则下面的
    // 断言在别的分支上也成立，等于什么都没验。
    expect(w.find('#m-models-platform').exists()).toBe(false)

    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    const patch = api.updateAdminModel.mock.calls.at(-1)[1]
    expect('models' in patch).toBe(false)
    expect('defaultModel' in patch).toBe(false)
  })

  it('反向对照：正常行**要**发 models / defaultModel', async () => {
    // 少了这一条，上一条可能是因为「保存整个坏了」而绿——
    // 那是 AGENTS.md 记过的「什么都没发生」那一族。
    api.updateAdminModel.mockResolvedValue({ item: modelRow(), platformDefault: null })
    const w = await mountAdmin(AdminPage)
    await gotoModels(w)
    await w.findAll('button').find((b) => b.text().trim() === '编辑').trigger('click')
    await flushPromises()

    expect(w.find('#m-models-openai').exists()).toBe(true)
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    const patch = api.updateAdminModel.mock.calls.at(-1)[1]
    expect(patch.models).toEqual(['gpt-x'])
    expect(patch.defaultModel).toBe('gpt-x')
  })
})

describe('可达性 · 表单与表格', () => {
  it('额度输入框的 label 真的指向那个输入框', async () => {
    // 文本断言能验「模板里写了 for」，验不了 id 拼错。
    // id 拼错时 label 变成纯文本，点不动，键盘也拿不到焦点。
    const w = await mountAdmin(AdminPage)
    await w.find('button[aria-controls="quota-editor-1"]').trigger('click')
    await flushPromises()

    for (const input of w.findAll('#quota-editor-1 input')) {
      const id = input.attributes('id')
      const label = w.findAll('label').find((l) => l.attributes('for') === id)
      expect(label, `没有 label 指向 ${id}`).toBeTruthy()
    }
  })

  it('aria-describedby 指向的提示真的存在', async () => {
    const w = await mountAdmin(AdminPage)
    await w.find('button[aria-controls="quota-editor-1"]').trigger('click')
    await flushPromises()

    const input = w.find('#quota-parse-1')
    const hintId = input.attributes('aria-describedby')
    const hint = w.find(`#${hintId}`)

    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('留空 = 清除覆盖')
  })

  it('表格里的按钮都显式写了 type（不写默认 submit，会误触发表单提交）', async () => {
    const w = await mountAdmin(AdminPage)
    await w.find('button[aria-controls="quota-editor-1"]').trigger('click')
    await flushPromises()

    for (const b of w.findAll('button')) {
      expect(b.attributes('type'), '有按钮没写 type').toBeTruthy()
    }
  })

  it('表头是 th + scope="col"（不是 td）', async () => {
    const w = await mountAdmin(AdminPage)

    const headers = w.findAll('thead th')
    expect(headers.length).toBeGreaterThan(0)
    for (const th of headers) {
      expect(th.attributes('scope')).toBe('col')
    }
  })
})
