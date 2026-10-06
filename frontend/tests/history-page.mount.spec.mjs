/**
 * HistoryPage.vue 真挂载（工单 #27 第 4 项）。
 *
 * ## 为什么单独开一个文件
 *
 * 全前端 6 个挂载 spec 里没有它——HistoryPage 486 行、三组筛选、两条删除路径，
 * 只有 `history-page.test.mjs` 的读源码判据。工单点名的三处缺口（封面代理、
 * 故障态 vs 空态、删收藏的第二道确认）都在这里；探针之外还查出一处票面没写的。
 *
 * ## 判据只走「用户看到什么 / 点完之后发生了什么」
 *
 * `<script setup>` 不暴露内部状态，DOM 与发出的事件是唯一出口。所以本文件不 import
 * 也不调用 `params` / `hasFilters` / `proxyThumbnail`——那是实现细节。
 *
 * 唯一的例外是「发出去的请求长什么样」：那本来就是接口契约，从 mock 的调用记录读。
 */
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// vi.mock 必须在顶部：它靠「被提升到组件 import 之前」生效。下沉到共用夹具就依赖
// 导入顺序，失效是静默的——组件真去发 axios 请求，jsdom 没有 XHR，形态是「挂载即炸」。
vi.mock('../src/api/history.js', () => ({
  fetchHistories: vi.fn(),
  fetchHistoryFacets: vi.fn(),
  deleteHistory: vi.fn(),
  setHistoryFavorite: vi.fn(),
  clearHistories: vi.fn(),
  fetchHistoryDetail: vi.fn(),
}))

import HistoryPage from '../src/components/HistoryPage.vue'
import {
  clearHistories, deleteHistory, fetchHistories, fetchHistoryDetail,
  fetchHistoryFacets, setHistoryFavorite,
} from '../src/api/history.js'

// ── 造数据 ─────────────────────────────────────────────────

/**
 * 一条解析历史。字段名与 `database.py` 里列表出口产出的键一一对齐，不自造：
 * 后端在同一个循环里补 `summary_preview` / `has_chat` / `has_ai_result`，
 * 并把 `cover_url` 归一成字符串（前端不必再判 null）。
 */
const row = (over = {}) => ({
  id: 11,
  video_url: 'https://v.example/a',
  video_title: '甲视频',
  cover_url: '/u/a.jpg',
  summary_preview: '这是摘要的开头',
  has_ai_result: true,
  is_favorite: false,
  has_chat: false,
  tags: ['科普'],
  updated_at: '2026-10-06T10:00:00',
  ...over,
})

/** 列表是完整信封（`api/history.js` 不做转换，原样透传）。 */
const env = (over = {}) => ({
  items: [],
  total: 0,
  page: 1,
  page_size: 20,
  total_pages: 0,
  ...over,
})

const FACETS = [{ tag: '科普', count: 2 }, { tag: '教程', count: 1 }]

async function mountPage(envelope = env()) {
  fetchHistories.mockResolvedValue(envelope)
  fetchHistoryFacets.mockResolvedValue(FACETS)
  const w = mount(HistoryPage)
  await flushPromises()
  return w
}

/** 历史行。TagFilterRow 的 chip 是 div 里的 button，不在 li 内，所以这个选择器干净。 */
const rows = (w) => w.findAll('li')
/**
 * 一行里「可点的那块」。⚠️ 是 li 的**第一个 div**，不是 li 本身：
 * `@click="openRecord(item)"` 挂在 `li > div` 上，而 `trigger('click')` 派发的事件
 * 从 li 出发只会往上冒、不会往下走到内层 div——第一版把 click 打在 li 上，
 * 结果 `fetchHistoryDetail` 没被调用，形状是「组件的点击接线坏了」。
 * 真实用户点的是那一块（li 只是 v-for 的壳），所以这里也点那一块。
 */
const recordArea = (w, i = 0) => rows(w)[i].findAll('div')[0]
/** 星标 / 删除是两个图标按钮，靠 aria-label 与 title 认，不靠下标。 */
const starBtn = (w) => w.find('button[aria-label="收藏这条解析"]')
const delBtn = (w) => w.find('button[title="删除记录"]')
const btnNamed = (w, name) => w.findAll('button').find((b) => b.text().trim() === name)

beforeEach(() => {
  // ⚠️ clearAllMocks 只清**调用历史**，不清**实现**。每个 mock 都必须在这里设一次默认
  // 实现，否则某条用例会悄悄用着前面某条留下的实现，表现为「全量绿、单跑红」。
  vi.clearAllMocks()
  fetchHistories.mockResolvedValue(env())
  fetchHistoryFacets.mockResolvedValue(FACETS)
  deleteHistory.mockResolvedValue({ ok: true })
  setHistoryFavorite.mockResolvedValue({ ok: true })
  clearHistories.mockResolvedValue({ ok: true })
  fetchHistoryDetail.mockResolvedValue({ id: 11, video_title: '甲视频' })

  // ⚠️ 必须**每条**都备着，不能只在需要的那条里设。jsdom 的 window.confirm 没有实现，
  // 调用它只在虚拟控制台打一行「Not implemented」并返回 undefined——于是
  // `!item.is_favorite && !confirm(...)` 恒真，`removeItem` 第一行就 return，
  // 删除请求压根没发出去。症状是「点删除没有发出请求」，看着像组件坏了。
  vi.stubGlobal('confirm', vi.fn(() => true))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// ── 行内按钮不顺手打开详情 ─────────────────────────────────────
//
// 票面没写、但探针查出来最值钱的一处。每行外面包着一个 `@click="openRecord(item)"
// 的 div，三个行内按钮（星标 / 删除 / 确认删除 / 取消）全靠 `@click.stop` 才不会
// 顺手把详情也打开。文本判据能看见 `@click.stop=` 这四个字，但**「点了没顺带打开
// 详情」这件事只有运行时看得见**——而它坏掉时的症状是：用户点「确认删除」，记录
// 真的被删了，页面却同时跳进了那条记录的详情，于是他以为删除失败了又点一次。
//
// ⚠️ 这一组必须带**正向对照**。只断言「没调 fetchHistoryDetail」的话，判据可能在
// 「点击压根没触发」的状态下也绿——那测的是 jsdom 不是组件。所以第一条先把
// 「点卡片确实会打开详情」钉死，后面几条的「没调用」才有意义。

describe('行内按钮不顺手打开详情', () => {
  const oneRow = () => env({ items: [row()], total: 1, page: 1, total_pages: 1 })

  test('正向对照：点卡片本身会去取详情并抛出 open-record', async () => {
    const w = await mountPage(oneRow())
    await recordArea(w).trigger('click')
    await flushPromises()

    expect(fetchHistoryDetail, '前提不成立：点卡片没去取详情，后面几条「没调用」就成了恒真')
      .toHaveBeenCalledWith(11)
    expect(w.emitted('open-record'), '点卡片没有抛出 open-record —— 后面的反向判据会跟着一起假')
      .toBeTruthy()
  })

  test('点收藏星标：只发收藏请求，不去取详情', async () => {
    const w = await mountPage(oneRow())
    await starBtn(w).trigger('click')
    await flushPromises()

    expect(setHistoryFavorite, '点星标没有发出收藏请求').toHaveBeenCalledWith(11, true)
    // @click.stop 失效时的症状：收藏成功了，页面却跳进详情，用户以为自己点错了
    expect(fetchHistoryDetail, '点星标顺手打开了详情 —— @click.stop 没生效').not.toHaveBeenCalled()
    expect(w.emitted('open-record')).toBeFalsy()
  })

  test('点删除图标：只发删除请求，不去取详情', async () => {
    const w = await mountPage(oneRow())
    await delBtn(w).trigger('click')
    await flushPromises()

    expect(deleteHistory, '点删除没有发出请求').toHaveBeenCalled()
    expect(fetchHistoryDetail, '点删除顺手打开了详情 —— 用户以为删成功了其实跳走了')
      .not.toHaveBeenCalled()
    expect(w.emitted('open-record')).toBeFalsy()
  })

  test('第二道确认里的两个按钮也不打开详情', async () => {
    // 收藏项要服务端拒绝才会出现这一行，所以先把 is_favorite 摆上、
    // 再让服务端返回 refused。不这么造状态的话，判据就挂在「按钮存不存在」上，
    // 而按钮压根没渲染，trigger 会抛错而不是变红——报错方向指向测试自己。
    const w = await mountPage(env({
      items: [row({ is_favorite: true })], total: 1, page: 1, total_pages: 1,
    }))
    deleteHistory.mockResolvedValue({ refused: true })
    await delBtn(w).trigger('click')
    await flushPromises()
    expect(btnNamed(w, '确认删除'), '前提不成立：第二道确认那一行没出现').toBeTruthy()

    fetchHistoryDetail.mockClear()
    w.emitted('open-record')
    await btnNamed(w, '确认删除').trigger('click')
    await flushPromises()

    expect(fetchHistoryDetail, '点「确认删除」顺手打开了详情 —— 记录被删了页面却跳走了')
      .not.toHaveBeenCalled()
    expect(w.emitted('open-record')).toBeFalsy()
  })
})

// ── 收藏项的第二道确认 ────────────────────────────────────────
//
// 服务端不收 force 就拒（409 → 前端翻成 `{refused:true}`），页面把那一行显示出来，
// 用户再点一次「确认删除」才带 force 重发。工单点名的缺口。

describe('收藏项的删除有第二道确认', () => {
  const fav = (over = {}) => row({ is_favorite: true, ...over })

  test('服务端拒绝时那一行真的出现，并说清代价', async () => {
    deleteHistory.mockResolvedValue({ refused: true })
    const w = await mountPage(env({ items: [fav()], total: 1, page: 1, total_pages: 1 }))

    await delBtn(w).trigger('click')
    await flushPromises()

    expect(w.text(), '服务端拒了却什么都不显示 —— 用户只会以为删除按钮坏了')
      .toContain('这是你收藏的记录，删掉就找不回来了。')
    expect(btnNamed(w, '确认删除')).toBeTruthy()
    expect(btnNamed(w, '取消')).toBeTruthy()
    // 第一次**不该**带 force：带了就没有第二道确认这回事了
    expect(deleteHistory.mock.calls[0][1], '第一次就带了 force —— 第二道确认形同虚设')
      .toBeUndefined()
    expect(rows(w).length, '被拒之后这一行不该消失').toBe(1)
  })

  test('点「确认删除」带 force 重发，行真的消失', async () => {
    deleteHistory.mockResolvedValue({ refused: true })
    const w = await mountPage(env({ items: [fav()], total: 1, page: 1, total_pages: 1 }))
    await delBtn(w).trigger('click')
    await flushPromises()

    deleteHistory.mockResolvedValue({ ok: true })
    await btnNamed(w, '确认删除').trigger('click')
    await flushPromises()

    expect(deleteHistory.mock.calls[1][1], '第二道确认没有带 force —— 服务端还会再拒一次')
      .toEqual({ force: true })
    expect(rows(w).length, '删掉了却还留在列表里').toBe(0)
    expect(w.text()).not.toContain('这是你收藏的记录')
  })

  test('点「取消」不发第二次请求，那一行收起', async () => {
    deleteHistory.mockResolvedValue({ refused: true })
    const w = await mountPage(env({ items: [fav()], total: 1, page: 1, total_pages: 1 }))
    await delBtn(w).trigger('click')
    await flushPromises()
    expect(btnNamed(w, '确认删除')).toBeTruthy()

    deleteHistory.mockClear()
    await btnNamed(w, '取消').trigger('click')
    await flushPromises()

    expect(deleteHistory, '点了「取消」还发删除请求 —— 取消等于没取消').not.toHaveBeenCalled()
    expect(btnNamed(w, '确认删除'), '取消之后那一行还挂着').toBeFalsy()
    expect(rows(w).length, '取消把记录也弄没了').toBe(1)
  })

  test('非收藏项第一下就删掉，没有第二道确认', async () => {
    // 对照那一半：只写上面三条的话，「第二道确认永远出现」也能全绿。
    deleteHistory.mockResolvedValue({ ok: true })
    const w = await mountPage(env({ items: [row()], total: 1, page: 1, total_pages: 1 }))

    await delBtn(w).trigger('click')
    await flushPromises()

    expect(btnNamed(w, '确认删除'), '非收藏项也被拦了一道 —— 多余的一次点击').toBeFalsy()
    expect(rows(w).length).toBe(0)
  })
})

// ── 封面代理与占位回落 ────────────────────────────────────────
//
// 与 CommunityPage 同一套口径（组件里两份 `proxyThumbnail` 是各自写的）。
// 文本层已有「列表项渲染 cover_url，取不到时回落占位图标」，但那是读模板；运行时
// 「代理到底套没套上」文本判据看不见——而它坏掉的症状是防盗链直连返回一片灰，
// 列表看着就是一堆坏图。

describe('封面走代理', () => {
  const withRow = (over) => env({ items: [row(over)], total: 1, page: 1, total_pages: 1 })

  test('站外地址被套上代理，原地址完整保留在查询串里', async () => {
    const w = await mountPage(withRow({ cover_url: 'https://img.example/c.jpg' }))
    expect(rows(w)[0].find('img').attributes('src'))
      .toBe(`/api/proxy/thumbnail?url=${encodeURIComponent('https://img.example/c.jpg')}`)
  })

  test('站内相对路径原样直取，不被塞进代理查询串', async () => {
    // 反向那一半。少了它 `startsWith('/')` 删掉也没人会红，后果是站内路径被套成
    // `/api/proxy/thumbnail?url=%2Fuploads%2Fx.png`——一个必然 404 的地址。
    const w = await mountPage(withRow({ cover_url: '/uploads/c.jpg' }))
    expect(rows(w)[0].find('img').attributes('src')).toBe('/uploads/c.jpg')
  })

  test('没有封面时走占位块而不是一个坏图', async () => {
    const w = await mountPage(withRow({ cover_url: '' }))
    expect(rows(w)[0].find('img').exists(), '没有封面却渲染出一个 <img> —— 界面上是一块碎图')
      .toBe(false)
  })
})

// ── 故障 / 空 / 筛选后空，三句话必须真的分开 ──────────────────
//
// 组件 `:103` 的注释写明「把 500 渲染成「暂无解析历史」，用户会以为是自己解析错了」。
// 文本判据只能验三个分支都存在，验不了界面上显示的是哪一句——而这正是要守的东西。

describe('故障与空在界面上分开', () => {
  test('服务端故障时显示故障文案，不显示「暂无解析历史」', async () => {
    fetchHistories.mockRejectedValue(new Error('boom'))
    const w = mount(HistoryPage)
    await flushPromises()

    expect(w.text()).toContain('历史记录加载失败')
    expect(w.text()).toContain('boom')
    expect(w.text(), '一次 500 被渲染成「暂无解析历史」——用户会以为是自己解析错了')
      .not.toContain('暂无解析历史')
  })

  test('故障态带一个可点的重试，点完真的会重新取', async () => {
    fetchHistories.mockRejectedValueOnce(new Error('boom'))
    const w = mount(HistoryPage)
    await flushPromises()
    expect(w.text()).toContain('历史记录加载失败')

    fetchHistories.mockResolvedValue(env({ items: [row()], total: 1, page: 1, total_pages: 1 }))
    await btnNamed(w, '重试').trigger('click')
    await flushPromises()

    expect(fetchHistories, '点了重试却没重新取一次').toHaveBeenCalledTimes(2)
    expect(w.text()).not.toContain('历史记录加载失败')
    expect(w.text()).toContain('甲视频')
  })

  test('空库时是「暂无解析历史」', async () => {
    const w = await mountPage(env())
    expect(w.text()).toContain('暂无解析历史')
    expect(w.text()).toContain('完成一次视频解析后，记录会出现在这里')
  })

  test('有筛选却没命中时是「没有匹配的记录」', async () => {
    // 与上一条成对读。少了它，`hasFilters` 整个删掉也没人会红——
    // 而用户按了「AI解析」看到「暂无解析历史」，会去以为自己把记录弄没了。
    const w = await mountPage(env())
    await w.find('input[type="search"]').setValue('机器学习')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(w.text()).toContain('没有匹配的记录')
    expect(w.text()).not.toContain('暂无解析历史')
  })
})

// ── 标签清单加载失败，与「真的还没有标签」分开 ─────────────────
//
// `facetError` 独立存在的理由与社区页的 `tagLoadError` 同款：失败时 facets 被清空，
// 于是筛选行空着 —— 和「历史里一个标签都还没有」在界面上完全一样。

describe('标签清单加载失败', () => {
  const oneRow = () => env({ items: [row()], total: 1, page: 1, total_pages: 1 })

  test('清单加载失败时说出原因，而列表本身照常渲染', async () => {
    fetchHistories.mockResolvedValue(oneRow())
    fetchHistoryFacets.mockRejectedValue(new Error('boom'))
    const w = mount(HistoryPage)
    await flushPromises()

    expect(w.text(), '标签挂了却什么都不说 —— 它与「历史里还没有标签」长得一模一样')
      .toContain('标签筛选没加载出来')
    expect(w.text()).toContain('boom')
    // 标签只是筛选器。它挂了不该把内容一起吃掉 —— 那是另一种故障。
    expect(w.text(), '标签挂了把整个列表也吃掉了，用户看不到任何记录').toContain('甲视频')
  })

  test('清单成功但真的为空时**不**显示失败（两件事必须分开）', async () => {
    fetchHistories.mockResolvedValue(oneRow())
    fetchHistoryFacets.mockResolvedValue([])
    const w = mount(HistoryPage)
    await flushPromises()

    // 这条是上一条的对照。只有两条都在，才证明界面分得清「挂了」与「没有」。
    expect(w.text(), '真的没有标签却说「加载失败」').not.toContain('标签筛选没加载出来')
    expect(w.text()).toContain('甲视频')
  })
})