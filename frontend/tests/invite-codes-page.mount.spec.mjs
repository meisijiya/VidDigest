/**
 * InviteCodesPage.vue 真挂载（工单 #104）。
 *
 * ## 为什么单独开一个文件
 *
 * 邀请码管理页是**独立路由**（`/admin/invite-codes`），它有自己的取数、
 * 自己的筛选与分页。工单 #99 那批测试只覆盖了「注册表单里那一格」，
 * 管理侧只有源码正则——那类断言验不了「点了真发出去、真渲染出表格」。
 * 验收时（🧐 Reality Checker）点出的正是这一条：读出口没有真挂载。
 *
 * ## 判据只走「用户看到什么 / 点完之后发生了什么」
 *
 * `<script setup>` 不暴露内部状态，DOM 与发出的事件是唯一出口。
 * 唯一的例外是「发出去的请求长什么样」：那本来就是接口契约，从 mock 的调用记录读。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// vi.mock 必须在顶部：它靠「被提升到组件 import 之前」生效。组件真去发
// axios 请求，jsdom 没有 XHR，形态是「挂载即炸」，而不是断言失败。
vi.mock('../src/api/admin.ts', () => ({
  fetchAdminInviteCodes: vi.fn(),
  createAdminInviteCodes: vi.fn(),
  revokeAdminInviteCode: vi.fn(),
}))

import InviteCodesPage from '../src/components/InviteCodesPage.vue'
import { fetchAdminInviteCodes, createAdminInviteCodes, revokeAdminInviteCode } from '../src/api/admin.ts'

// ── 造数据 ─────────────────────────────────────────────────

/** 一行邀请码。字段名与 api 层产出的 camelCase 一一对应，不自造。 */
const row = (over = {}) => ({
  id: 7,
  codeMasked: 'ABCD****WXYZ',
  createdBy: 1,
  createdByEmail: 'boss@example.com',
  createdAt: '2026-10-25T00:00:00',
  expiresAt: null,
  maxUses: 1,
  usedCount: 0,
  revoked: 0,
  note: '给张三',
  expired: false,
  exhausted: false,
  status: 'active',
  ...over,
})

/** 列表信封：api 层已经翻好形状，组件拿到什么就是什么。 */
const envelope = (items = [], over = {}) => ({
  items,
  total: items.length,
  limit: 20,
  offset: 0,
  page: 1,
  pageSize: 20,
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  fetchAdminInviteCodes.mockResolvedValue(envelope([]))
  createAdminInviteCodes.mockResolvedValue({ items: [] })
  revokeAdminInviteCode.mockResolvedValue({ item: row() })
})

async function mountPage() {
  const wrapper = mount(InviteCodesPage, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

describe('邀请码管理页取数', () => {
  test('打开页面就取列表：第 1 页、每页 20 枚、不筛状态', async () => {
    await mountPage()
    expect(fetchAdminInviteCodes).toHaveBeenCalledTimes(1)
    expect(fetchAdminInviteCodes).toHaveBeenCalledWith({ page: 1, pageSize: 20, status: '' })
  })

  test('列表里能看到脱敏码与发放人', async () => {
    fetchAdminInviteCodes.mockResolvedValue(envelope([row()]))
    const wrapper = await mountPage()
    const text = wrapper.text()
    expect(text).toContain('ABCD****WXYZ')
    expect(text).toContain('boss@example.com')
  })

  test('一枚都没有时说清楚是空，不是报错', async () => {
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('没有符合条件的邀请码')
  })

  test('反向对照：列表取不到时给的是错误提示，不是空列表装作没事', async () => {
    fetchAdminInviteCodes.mockResolvedValue(envelope([row()]))
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('ABCD****WXYZ')

    fetchAdminInviteCodes.mockRejectedValueOnce(new Error('boom'))
    await wrapper.find('select#invite-status').setValue('expired')
    await flushPromises()
    expect(wrapper.text()).toContain('邀请码列表取不到')
    expect(wrapper.text()).not.toContain('ABCD****WXYZ')
  })
})

describe('邀请码管理页筛选与分页', () => {
  test('换状态筛选会重新取数，并带上这个状态', async () => {
    fetchAdminInviteCodes.mockResolvedValue(envelope([row({ status: 'expired' })]))
    const wrapper = await mountPage()
    await wrapper.find('select#invite-status').setValue('expired')
    await flushPromises()
    expect(fetchAdminInviteCodes).toHaveBeenLastCalledWith({
      page: 1,
      pageSize: 20,
      status: 'expired',
    })
  })

  test('翻页把页码传给接口', async () => {
    fetchAdminInviteCodes.mockResolvedValue(envelope([row()], { total: 40 }))
    const wrapper = await mountPage()
    const next = wrapper.findAll('button').find((b) => b.text() === '下一页')
    expect(next).toBeTruthy()
    await next.trigger('click')
    await flushPromises()
    expect(fetchAdminInviteCodes).toHaveBeenLastCalledWith({
      page: 2,
      pageSize: 20,
      status: '',
    })
  })
})

describe('邀请码管理页发放与作废', () => {
  test('批量发放：数量、可用次数、有效期、备注都发出去，明文码只显示这一次', async () => {
    createAdminInviteCodes.mockResolvedValue({
      items: [{ code: 'PLAINTEXT-ONE' }, { code: 'PLAINTEXT-TWO' }],
    })
    const wrapper = await mountPage()

    await wrapper.find('#invite-count').setValue('2')
    await wrapper.find('#invite-max').setValue('3')
    await wrapper.find('#invite-hours').setValue('48')
    await wrapper.find('#invite-note').setValue('给张三')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(createAdminInviteCodes).toHaveBeenCalledWith({
      count: 2,
      maxUses: 3,
      expiresInHours: 48,
      note: '给张三',
    })
    const text = wrapper.text()
    expect(text).toContain('PLAINTEXT-ONE')
    expect(text).toContain('PLAINTEXT-TWO')
  })

  test('数量不合法时本地就拦下来，不为显然错的输入跑一趟网络', async () => {
    const wrapper = await mountPage()
    await wrapper.find('#invite-count').setValue('99')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(createAdminInviteCodes).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('发放数量要在 1 到 20 之间')
  })

  test('作废之后重新取列表', async () => {
    fetchAdminInviteCodes.mockResolvedValue(envelope([row()]))
    const wrapper = await mountPage()
    const revokeBtn = wrapper.findAll('button').find((b) => b.text() === '作废')
    expect(revokeBtn).toBeTruthy()
    await revokeBtn.trigger('click')
    await flushPromises()
    expect(revokeAdminInviteCode).toHaveBeenCalledWith(7)
    expect(fetchAdminInviteCodes).toHaveBeenCalledTimes(2)
  })
})
