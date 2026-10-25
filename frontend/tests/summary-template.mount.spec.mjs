/**
 * 总结模板下拉框的真挂载（工单 #105）。
 *
 * ## 判据只走「用户看到什么」与「后端收到什么」
 *
 * `<script setup>` 不暴露内部状态，DOM 是唯一出口。所以：
 * - 「下拉框渲染出来了、选项是服务端给的那几个」→ 读 DOM；
 * - 「用户选的那个模板真的发给了后端」→ 读 mock 的调用记录（那就是接口契约）。
 *
 * ## 每条守卫都配一条对照
 *
 * 「选了 brief 会发 brief」这条，如果请求体里写死的就是 brief，它会恒真。
 * 所以配一条「不选时发的是服务端说的默认模板」——默认模板与服务端一致才是对的状态，
 * 两者不同才说明选择真的透传了。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// vi.mock 必须在顶部：它靠「被提升到组件 import 之前」生效。
vi.mock('../src/api/summarize.ts', () => ({
  summarizeVideo: vi.fn(),
  chatWithVideo: vi.fn(),
  fetchQuota: vi.fn(),
  fetchSummaryTemplates: vi.fn(),
}))
vi.mock('../src/api/history.ts', () => ({
  fetchChatSession: vi.fn(),
  saveHistory: vi.fn(),
}))
vi.mock('../src/lib/byok.ts', () => ({
  getRequestCredential: vi.fn(() => ({ provider: 'openai' })),
}))

import VideoSummary from '../src/components/VideoSummary.vue'
import { summarizeVideo, fetchSummaryTemplates } from '../src/api/summarize.ts'

const VIDEO_URL = 'https://youtu.be/dQw4w9WgXcQ'

const TEMPLATES = {
  default: 'full',
  templates: [
    { id: 'full', name: '详细总结', description: '完整分析：详实的总结正文、思维导图与标签。' },
    { id: 'brief', name: '精简摘要', description: '只留最关键的结论，一屏读完。' },
  ],
}

function makeStream() {
  let finish
  const done = new Promise((res) => {
    finish = res
  })
  return {
    handle: { done, cancel: vi.fn() },
    finish: () => finish({ done: true, value: undefined }),
  }
}

/**
 * 挂载到「还没开始」的待启动区块：那个下拉框就在那儿。
 * hasCommunityResult=false ⇒ 组件停在待启动块，不自动发起。
 */
async function mountSummary({ templates = TEMPLATES, fail = false } = {}) {
  const stream = makeStream()
  summarizeVideo.mockImplementation(() => stream.handle)
  fetchSummaryTemplates.mockImplementation(() =>
    fail ? Promise.reject(new Error('获取总结模板失败')) : Promise.resolve(templates),
  )

  const w = mount(VideoSummary, {
    props: { videoUrl: VIDEO_URL, user: { id: 1 }, hasCommunityResult: false },
  })
  await flushPromises()
  return w
}

/** 「开始 AI 解析」按钮：待启动块里唯一带这行字的按钮。 */
function startButton(w) {
  const btn = w.findAll('button').find((b) => b.text().includes('开始 AI 解析'))
  expect(btn, '没有停在待启动区块').toBeTruthy()
  return btn
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('总结模板下拉框', () => {
  test('选项按服务端给的清单渲染出来，默认选中服务端说的那个', async () => {
    const w = await mountSummary()

    const sel = w.find('#summary-template-select')
    expect(sel.exists(), '模板下拉框没渲染出来').toBe(true)
    expect(sel.findAll('option').map((o) => o.text().trim())).toEqual(['详细总结', '精简摘要'])
    expect(sel.element.value, '默认没选中服务端说的默认模板').toBe('full')
  })

  test('换一个模板后，请求里带上的是它', async () => {
    const w = await mountSummary()

    await w.find('#summary-template-select').setValue('brief')
    await startButton(w).trigger('click')
    await flushPromises()

    expect(summarizeVideo).toHaveBeenCalledTimes(1)
    const options = summarizeVideo.mock.calls[0][3]
    expect(options.summaryTemplate, '用户选的模板没进请求，后端会拿默认模板来总结').toBe('brief')
  })

  test('对照组：不动下拉框时，带的是服务端说的默认模板', async () => {
    const w = await mountSummary()

    await startButton(w).trigger('click')
    await flushPromises()

    expect(summarizeVideo).toHaveBeenCalledTimes(1)
    const options = summarizeVideo.mock.calls[0][3]
    expect(options.summaryTemplate).toBe('full')
  })

  test('清单取不到时不挡解析（服务端会用它的默认模板）', async () => {
    const w = await mountSummary({ fail: true })

    expect(w.find('#summary-template-select').exists(), '清单没取到却渲染了下拉框').toBe(false)
    expect(w.text()).toContain('模板清单没取到')

    await startButton(w).trigger('click')
    await flushPromises()

    expect(summarizeVideo).toHaveBeenCalledTimes(1)
    const options = summarizeVideo.mock.calls[0][3]
    // 空串 = 「你决定」。前端不猜：猜错一次的症状是「界面写着 A，拿到的是 B 的总结」。
    expect(options.summaryTemplate, '清单没取到时前端自己编了一个模板 id').toBe('')
  })
})
