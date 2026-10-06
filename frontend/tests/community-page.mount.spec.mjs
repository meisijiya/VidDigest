/**
 * CommunityPage.vue 真挂载（工单 #19 第 2 项 · 工单 #24）。
 *
 * ## 这个文件存在的原因：补的是「压根没有断言」的那几类
 *
 * 变异探针实测出 CommunityPage 的文本层在 4 类失败上失明（C1~C5，其中 C1 被
 * `community-page.test.mjs:233`「故障态排在空状态之前」抓住了）。逐条判定进一步确认：
 * **C2 / C3 / C5 三类在全前端测试树里一个断言都没有**，grep 不到任何用例覆盖。
 *
 * 这与「有文本断言但抓不住」是**两回事**——后者的修法是「保留文本 + 加挂载版」，
 * 前者只能靠挂载补上，因为文本层压根看不见运行时。
 *
 * 所以这个文件**不迁移任何既有用例**（判定的结论是 KEEP_TEXT 21 / ALREADY_BEHAVIOR 9
 * / MIGRATE 8 / UNMOUNTABLE 6，那些留在原处），只补真空。
 *
 * ## C4 不在这里，以及为什么
 *
 * 探针把「`goPage` 的上界守卫失效」也测成 SURVIVED，但那条路径**用户走不到**：
 * 模板 `:150`/`:154` 的翻页按钮是 `:disabled="page >= totalPages || loading"`，
 * 按钮禁用时根本触发不了 `goPage(page + 1)`。所以函数里那个 `n > totalPages`
 * 是**防御性冗余**——交付的是「不发生动作」，不是「那条分支被执行过」。
 * 为不可达路径写断言是给注释找陪葬。本文件只断言**可观察**的那一半（按钮该 disabled）。
 *
 * ## 判据只走「用户看到什么」
 *
 * `<script setup>` 不暴露内部状态，DOM 是唯一出口。所以本文件不 import 也不调用
 * `hasFilter` / `applyList` / `proxyThumbnail`——那是组件的实现细节。
 * 唯一的例外是「点完之后后端收到什么」：那本来就是接口契约，从 mock 的调用记录读。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

// vi.mock 必须在顶部：它靠「被提升到组件 import 之前」生效。下沉到共用夹具就依赖
// 导入顺序，失效是静默的——组件真去发 axios 请求，jsdom 没有 XHR，形态是「挂载即炸」。
vi.mock('../src/api/community.js', () => ({
  fetchCommunityVideos: vi.fn(),
  searchCommunity: vi.fn(),
  fetchCommunityTags: vi.fn(),
}))

import CommunityPage from '../src/components/CommunityPage.vue'
import { fetchCommunityVideos, searchCommunity, fetchCommunityTags } from '../src/api/community.js'

// ── 造数据 ─────────────────────────────────────────────────

/** 标签清单的形状是 `[{tag, count}]`，服务端给的完整清单（不是当前页汇总）。 */
const TAGS = [
  { tag: '科普', count: 3 },
  { tag: '教程', count: 5 },
  { tag: '编程', count: 2 },
  { tag: '生活', count: 1 },
  { tag: '测评', count: 4 },
]

/** 社区卡片：后端白名单投影里就这几个键。 */
const card = (over = {}) => ({
  id: 7,
  video_url: 'https://youtu.be/dQw4w9WgXcQ',
  cover_url: 'https://img.example/c.jpg',
  video_title: '机器学习入门',
  tags: ['人工智能', '科普'],
  ...over,
})

const page = (over = {}) => ({
  items: [],
  total: 0,
  page: 1,
  page_size: 20,
  total_pages: 0,
  ...over,
})

async function mountPage(listResponse = page()) {
  searchCommunity.mockResolvedValue(listResponse)
  fetchCommunityTags.mockResolvedValue(TAGS)
  const w = mount(CommunityPage)
  await flushPromises()
  return w
}

/** 所有标签 chip（`data-tag-chip` 是 chip 专有；「全部」按钮没有这个属性）。 */
const chips = (w) => w.findAll('[data-tag-chip]')
const chipNamed = (w, name) => chips(w).find((c) => c.text().includes(name))
const isPressed = (chip) => chip.attributes('aria-pressed') === 'true'

/** 点一个 chip 并等它带来的那次重取 settle。 */
async function clickChip(w, name) {
  const chip = chipNamed(w, name)
  if (!chip) throw new Error(`界面上没有名为「${name}」的标签 chip`)
  await chip.trigger('click')
  await flushPromises()
}

/** searchCommunity 的第 n 次调用参数（从 0 数）。 */
const callArg = (n) => searchCommunity.mock.calls[n][0]

beforeEach(() => {
  // ⚠️ clearAllMocks 只清**调用历史**，不清**实现**。所以每个 mock 都必须在这里
  // 设一次默认实现 —— 否则某条用例会悄悄用着前面某条留下的实现，
  // 表现为「全量跑绿、单跑 `-t` 红」的形状差异（本文件踩过一次：
  // 少了 fetchCommunityTags 的默认值，模板里 tagOptions.length 直接抛 TypeError）。
  //
  // 补充的纪律：一条用例要的非默认数据，都在自己体内设，不要指望同文件里别的用例。
  vi.clearAllMocks()
  fetchCommunityVideos.mockResolvedValue(page())
  fetchCommunityTags.mockResolvedValue(TAGS)
  searchCommunity.mockResolvedValue(page())
})

// ── C2：空态文案跟着「有没有筛选」走 ─────────────────────────
//
// 这是本次补的第一条。`hasFilter` 的判据是「关键词非空 **或** 标签非空」；
// 写成 `&&` 时，只按标签筛选会落回「社区还是空的」——用户以为社区没内容，
// 于是去换关键词，而真正的原因是筛选太窄。

describe('空状态文案跟着有没有筛选走', () => {
  test('没有任何筛选时是「社区还是空的」', async () => {
    const w = await mountPage(page())
    expect(w.text()).toContain('社区还是空的')
    expect(w.text()).toContain('第一个解析视频的人会把它放进来')
  })

  test('只按标签筛选、结果为空时是「没有匹配的内容」', async () => {
    const w = await mountPage(page())
    await clickChip(w, '科普')

    expect(w.text()).toContain('没有匹配的内容')
    expect(w.text()).not.toContain('社区还是空的')
    expect(w.text()).toContain('换个关键词，或按标签浏览')
  })

  test('按关键词搜索、结果为空时同样是「没有匹配的内容」', async () => {
    // 反向那一半：只用标签能覆盖住，用关键词也要覆盖住——否则 `||` 的另一半
    // 同样无人看守。
    const w = await mountPage(page())
    await w.find('input[type="search"]').setValue('机器学习')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(w.text()).toContain('没有匹配的内容')
  })
})

// ── C3：卡片上的标签 ────────────────────────────────────────

describe('卡片把标签显示出来', () => {
  test('每个标签都渲染成一个可读的片段', async () => {
    const w = await mountPage(page({ items: [card()], total: 1, total_pages: 1 }))
    const text = w.text()
    expect(text).toContain('机器学习入门')
    expect(text).toContain('人工智能')
    expect(text).toContain('科普')
  })

  test('tags 为空数组时不留下一个空的标签容器', async () => {
    // 空容器在界面上就是一段没有内容的框，而那正是「标签 disappeared 了」
    // 与「这个视频没有标签」分不开的形状——判据要落在「有没有渲染 span」上。
    const w = await mountPage(page({
      items: [card({ tags: [] })],
      total: 1,
      total_pages: 1,
    }))
    const html = w.html()
    expect(w.text()).toContain('机器学习入门')
    // 标签 span 带的就是标签文字本身；一个都没有时不该出现成对的空标签容器
    expect(html).not.toMatch(/<div class="flex flex-wrap gap-1\.5 mt-2">\s*<\/div>/)
  })

  test('多个标签各占一个片段，不挤成一段', async () => {
    const w = await mountPage(page({
      items: [card({ tags: ['人工智能', '科普', '入门'] })],
      total: 1,
      total_pages: 1,
    }))
    const spans = w.findAll('li span').map((s) => s.text())
    for (const t of ['人工智能', '科普', '入门']) {
      expect(spans, `标签「${t}」没有独立渲染`).toContain(t)
    }
  })
})

// ── C5：缩略图代理 ──────────────────────────────────────────

describe('缩略图走本站代理', () => {
  test('站外地址被套上代理，且原地址完整保留在查询串里', async () => {
    const w = await mountPage(page({ items: [card()], total: 1, total_pages: 1 }))
    const img = w.find('li img')
    expect(img.exists()).toBe(true)
    // 防盗链直连会返回一片灰，所以站外地址必须经代理
    expect(img.attributes('src')).toBe(
      `/api/proxy/thumbnail?url=${encodeURIComponent('https://img.example/c.jpg')}`,
    )
  })

  test('站内相对路径原样直取，不被塞进代理查询串', async () => {
    // 反向那一半。少了它，`startsWith('/')` 这个判断删掉也没人会红，
    // 而后果是站内路径被套成 `/api/proxy/thumbnail?url=%2Fuploads%2Fx.png`
    // ——一个必然 404 的地址。
    const w = await mountPage(page({
      items: [card({ cover_url: '/uploads/c.jpg' })],
      total: 1,
      total_pages: 1,
    }))
    expect(w.find('li img').attributes('src')).toBe('/uploads/c.jpg')
  })

  test('没有封面时走占位块而不是一个坏图', async () => {
    const w = await mountPage(page({
      items: [card({ cover_url: '' })],
      total: 1,
      total_pages: 1,
    }))
    expect(w.find('li img').exists()).toBe(false)
  })
})

// ── C4 的可观察那一半 ───────────────────────────────────────

describe('翻页按钮的可观察状态', () => {
  test('只有一页时整个翻页区都不渲染', async () => {
    // 模板 `:149` 是 `v-if="totalPages > 1"` —— 只有一页时压根没有翻页区，
    // 所以「下一页是禁用的」这个假设**是错的**（本文件第一版就是这么写错的，
    // 实测报 undefined）。可观察的事实是「翻页区不存在」。
    const w = await mountPage(page({
      items: [card()],
      total: 1,
      page: 1,
      total_pages: 1,
    }))
    const labels = w.findAll('button').map((b) => b.text().trim())
    expect(labels, '只有一页时不该出现翻页区').not.toContain('下一页')
    expect(labels).not.toContain('上一页')
  })

  test('在最后一页时「下一页」禁用、「上一页」可用', async () => {
    // 上一条把「不可达」写对了：按钮禁用时根本触发不了 `goPage(page + 1)`，
    // 所以函数里的上界守卫是防御性冗余。这一条是它**真正**拦下的那种形态。
    const w = await mountPage(page({
      items: [card()],
      total: 30,
      page: 2,
      total_pages: 2,
    }))
    const btn = (label) => w.findAll('button').find((b) => b.text().trim() === label)
    expect(btn('下一页').attributes('disabled')).toBeDefined()
    expect(btn('上一页').attributes('disabled')).toBeUndefined()
    expect(w.text()).toContain('2 / 2')
  })

  test('多页时「下一页」可点，点完页码前进', async () => {
    const w = await mountPage(page({
      items: [card()],
      total: 30,
      page: 1,
      total_pages: 2,
    }))
    const next = w.findAll('button').find((b) => b.text().trim() === '下一页')
    expect(next.attributes('disabled')).toBeUndefined()

    searchCommunity.mockResolvedValue(page({
      items: [card({ id: 8, video_title: '第二页的视频' })],
      total: 30,
      page: 2,
      total_pages: 2,
    }))
    await next.trigger('click')
    await flushPromises()

    expect(w.text()).toContain('2 / 2')
    expect(w.text()).toContain('第二页的视频')
  })
})

// ── 故障态与空态在界面上分开（C1 的挂载版）────────────────────
//
// C1 已被 `community-page.test.mjs:233` 的文本判据抓住（分支顺序）。这里补的是
// **界面上真的显示的是哪一句**——文本判据能验分支顺序，验不了渲染结果。

describe('故障与「没有内容」在界面上分开', () => {
  test('服务端故障时显示故障文案，不显示「社区还是空的」', async () => {
    searchCommunity.mockRejectedValue(new Error('boom'))
    fetchCommunityTags.mockResolvedValue(TAGS)
    const w = mount(CommunityPage)
    await flushPromises()

    expect(w.text()).toContain('社区列表加载失败')
    expect(w.text()).toContain('boom')
    expect(w.text()).not.toContain('社区还是空的')
  })

  test('故障态带一个可点的重试', async () => {
    searchCommunity.mockRejectedValue(new Error('boom'))
    const w = mount(CommunityPage)
    await flushPromises()
    const retry = w.findAll('button').find((b) => b.text().trim() === '重试')
    expect(retry, '故障态没有重试入口').toBeTruthy()
    expect(retry.attributes('disabled')).toBeUndefined()
  })
})

// ── 标签多选取并集 ──────────────────────────────────────────

describe('标签多选取并集', () => {
  test('点第二个标签不会顶掉第一个，两次都发出去', async () => {
    // 组件 `:198-201` 的注释写明用数组就是为了让「再加一个标签」真的成立：
    // 单值形态下点第二个就会顶掉第一个，用户只能先清空重来。
    const w = await mountPage(page({
      items: [card()],
      total: 1,
      total_pages: 1,
    }))

    await clickChip(w, '科普')
    await clickChip(w, '编程')

    expect(isPressed(chipNamed(w, '科普'))).toBe(true)
    expect(isPressed(chipNamed(w, '编程'))).toBe(true)
    expect(callArg(1).tag).toBe('科普')
    expect(callArg(2).tag, '第二个标签没有与第一个取并集').toBe('科普,编程')
  })

  test('选中之后筛选选项一个都不少（清单常驻）', async () => {
    // 组件 `:204-208` 的注释写明清单刻意不从当前页汇总——否则选中一个之后
    // 其余标签会从筛选行里消失，而那正是这一行存在的理由。
    // 基准直接用 TAGS 的长度，不必先挂一次拿「挂载前」的数字。
    const w = await mountPage(page({
      items: [card()],
      total: 1,
      total_pages: 1,
    }))
    expect(chips(w).length).toBe(TAGS.length)

    await clickChip(w, '科普')
    expect(chips(w).length, '选中之后标签变少了').toBe(TAGS.length)
    await clickChip(w, '编程')
    expect(chips(w).length, '选两个之后标签变少了').toBe(TAGS.length)
  })

  test('清空选择回到全量，请求里不再带 tag', async () => {
    const w = await mountPage(page())
    await clickChip(w, '科普')
    expect(callArg(1).tag).toBe('科普')

    const all = w.findAll('button').find((b) => b.text().trim() === '全部')
    await all.trigger('click')
    await flushPromises()

    expect(chipNamed(w, '科普').attributes('aria-pressed')).toBe('false')
    expect(callArg(2).tag).toBe('')
  })
})
// ── 标签清单加载失败，与「真的还没有标签」分开 ─────────────────
//
// `tagLoadError` 这个独立状态存在的**全部理由**就是这两者长得一模一样：
// 失败时 `tagOptions` 被清空，于是筛选行空着 —— 和「社区里一个标签都还没有」
// 在界面上完全一样。所以下面三条必须成组读，少任何一条都判别不出来。

describe('标签清单加载失败', () => {
  const oneCard = page({ items: [card()], total: 1, page: 1, total_pages: 1 })

  test('清单加载失败时说出原因，而列表本身照常渲染', async () => {
    searchCommunity.mockResolvedValue(oneCard)
    fetchCommunityTags.mockRejectedValue(new Error('boom'))
    const w = mount(CommunityPage)
    await flushPromises()

    expect(w.text(), '标签清单挂了却什么都不说 —— 它与「社区里还没有标签」长得一模一样')
      .toContain('标签列表加载失败')
    expect(w.text()).toContain('boom')
    // 标签只是筛选器。它挂了不该把内容一起吃掉 —— 那是另一种故障。
    expect(w.text(), '标签挂了把整个列表也吃掉了，用户看不到任何视频').toContain('机器学习入门')
    expect(chips(w).length, '失败时不该渲染出一行空标签').toBe(0)
  })

  test('清单成功但真的为空时**不**显示失败（两件事必须分开）', async () => {
    searchCommunity.mockResolvedValue(oneCard)
    fetchCommunityTags.mockResolvedValue([])
    const w = mount(CommunityPage)
    await flushPromises()

    // 这条是上一条的对照。只有两条都在，才证明界面分得清「挂了」与「没有」。
    expect(w.text(), '真的没有标签却说「加载失败」—— 用户会反复去点那个不存在的重试')
      .not.toContain('标签列表加载失败')
    expect(w.text(), '没有标签时列表也不该消失').toContain('机器学习入门')
  })

  test('点重试会重新拉清单；成功后提示消失、标签行出现', async () => {
    searchCommunity.mockResolvedValue(oneCard)
    fetchCommunityTags.mockRejectedValueOnce(new Error('boom'))
    const w = mount(CommunityPage)
    await flushPromises()
    expect(w.text()).toContain('标签列表加载失败')
    expect(fetchCommunityTags).toHaveBeenCalledTimes(1)

    fetchCommunityTags.mockResolvedValue(TAGS)
    const retry = w.findAll('button').find((b) => b.text().trim() === '重试')
    expect(retry, '标签清单挂了却没给重试入口 —— 用户只能刷新整个页面').toBeTruthy()
    await retry.trigger('click')
    await flushPromises()

    expect(w.text(), '重试成功了失败提示还挂着').not.toContain('标签列表加载失败')
    expect(chips(w).length, '重试成功后标签行没出来').toBe(TAGS.length)
    expect(fetchCommunityTags, '点重试没有真的重新拉一次').toHaveBeenCalledTimes(2)
  })

  test('重试也失败时提示留着，且说清是这次的失败', async () => {
    searchCommunity.mockResolvedValue(oneCard)
    fetchCommunityTags.mockRejectedValue(new Error('boom'))
    const w = mount(CommunityPage)
    await flushPromises()

    fetchCommunityTags.mockRejectedValue(new Error('还是不行'))
    const retry = w.findAll('button').find((b) => b.text().trim() === '重试')
    await retry.trigger('click')
    await flushPromises()

    expect(w.text()).toContain('标签列表加载失败')
    expect(w.text(), '重试后的失败原因没更新 —— 用户看到的还是上一次那句').toContain('还是不行')
  })
})

// ── clearSearch 与 needLogin ─────────────────────────────────
//
// 社区页的 mount 此前只点过标签 chip 与翻页按钮。这两条是剩下最显眼的两个：
// 「清除」把三种筛选状态一次归零，needLogin 把搜索挡掉但**保留**公开列表。
//
// 两者都各带一条反向判据：没有筛选时不该出现「清除」；
// 未登录时列表不该变成空的（搜索被挡 ≠ 整个页面不能用）。

describe('清除筛选', () => {
  const oneCard = page({ items: [card()], total: 1, page: 1, total_pages: 1 })
  const clearBtn = (w) => w.findAll('button').find((b) => b.text().trim() === '清除')

  test('没有任何筛选时不出现「清除」', async () => {
    const w = await mountPage(oneCard)
    expect(clearBtn(w), '没有筛选却摆着一个「清除」—— 用户点了什么都不会变').toBeFalsy()
  })

  test('只按标签筛选时「清除」出现，点完三种状态一起归零并重取', async () => {
    const w = await mountPage(oneCard)
    await clickChip(w, '科普')
    expect(clearBtn(w), '已经在筛选了却没有「清除」入口').toBeTruthy()

    searchCommunity.mockResolvedValue(oneCard)
    await clearBtn(w).trigger('click')
    await flushPromises()

    expect(chipNamed(w, '科普').attributes('aria-pressed'), '清除后 chip 还按着').toBe('false')
    expect(clearBtn(w), '清除之后按钮自己不该还留着').toBeFalsy()
    // 三种筛选状态归零之后的那次请求：既不带 q 也不带 tag
    const last = callArg(searchCommunity.mock.calls.length - 1)
    expect(last.q, '清除之后请求里还带着关键词').toBe('')
    expect(last.tag, '清除之后请求里还带着标签').toBe('')
  })

  test('按关键词搜索后「清除」也归零输入框', async () => {
    const w = await mountPage(oneCard)
    const input = w.find('input[type="search"]')
    await input.setValue('机器学习')
    await input.trigger('submit')
    await flushPromises()
    expect(clearBtn(w), '搜过了却没有「清除」入口').toBeTruthy()

    searchCommunity.mockResolvedValue(oneCard)
    await clearBtn(w).trigger('click')
    await flushPromises()

    expect(w.find('input[type="search"]').element.value, '输入框里还留着上次的关键词')
      .toBe('')
  })
})

describe('未登录时搜索被挡、列表保留', () => {
  test('searchCommunity 回 needLogin：显示登录提示，且仍能看到公开列表', async () => {
    searchCommunity.mockResolvedValue({ needLogin: true })
    fetchCommunityVideos.mockResolvedValue(page({
      items: [card()], total: 1, page: 1, total_pages: 1,
    }))
    const w = mount(CommunityPage)
    await flushPromises()

    expect(w.text(), '访客搜索失败后界面上什么都没说').toContain('搜索与视频详情需要登录后使用')
    // 关键：搜索被挡不等于整个页面不能用 —— 公开列表必须还在。
    expect(w.text(), '未登录把整个社区页也弄空了 —— 访客什么都看不到')
      .toContain('机器学习入门')
    expect(w.text()).not.toContain('社区列表加载失败')
    expect(w.text(), '被挡之后不该还渲染空状态').not.toContain('社区还是空的')
  })

  test('未登录时点提示里的按钮会 emit need-login', async () => {
    searchCommunity.mockResolvedValue({ needLogin: true })
    fetchCommunityVideos.mockResolvedValue(page({
      items: [card()], total: 1, page: 1, total_pages: 1,
    }))
    const w = mount(CommunityPage)
    await flushPromises()

    const btn = w.findAll('button').find((b) => b.text().includes('登录'))
    expect(btn, '提示里没有可点的登录入口 —— 用户只能自己去顶栏找').toBeTruthy()
    await btn.trigger('click')
    expect(w.emitted('need-login'), '点了却没 emit need-login —— 登录框不会开').toBeTruthy()
  })

  test('被挡时自动退回公开列表，并清掉已经填进去的关键词', async () => {
    searchCommunity.mockResolvedValue({ needLogin: true })
    fetchCommunityVideos.mockResolvedValue(page({
      items: [card()], total: 1, page: 1, total_pages: 1,
    }))
    const w = mount(CommunityPage)
    await flushPromises()
    expect(fetchCommunityVideos, '被挡之后没有退回公开列表').toHaveBeenCalled()

    // ⚠️ 必须先把关键词填进去，再触发一次 load。
    // 这一条第一版写错了：mount 之后用户从没填过任何东西，于是输入框本来就是空的，
    // needLogin 分支清不清它都一样 —— 变异删掉那行 `keyword.value = ''` 照样绿，
    // 断言恒真（实测 SURVIVED）。判据必须跑在**能区分两种实现的状态**上。
    const input = w.find('input[type="search"]')
    await input.setValue('机器学习')
    expect(input.element.value, '前提不成立：关键词没填进输入框，后面那条就成了恒真')
      .toBe('机器学习')

    // ⚠️ 必须对 `<form>` 触发 submit，不能点那个 `type="submit"` 的按钮。
    // jsdom 里 click 不会走 submit 的默认行为，于是 `doSearch()` 压根没被调用
    // （探针实测：点完按钮后 `searchCommunity` 的调用次数仍是 1，而断言看的是
    // 「关键词还在」——看起来像组件没清，实际是这次交互根本没发生）。
    // 这类形状的特征是：**断言的红是真的，但原因在测试自己身上**。
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(searchCommunity.mock.calls.length, '触发了 submit 却没发出第二次搜索').toBe(2)

    expect(w.find('input[type="search"]').element.value,
      '关键词还挂在输入框里 —— 用户会反复点搜索、再被挡一次').toBe('')
  })
})

// ── 列表渲染的条数、顺序与归属 ─────────────────────────────────
//
// 变异探针（`.scratch/probe-card-click.mjs`，跑完即删、不入库）给出的结论与
// 票面写的**相反**：票面第 3 项写「点社区卡片没有行为覆盖」，实测 7 条变异里
// **6 条被既有文本断言守住** ——
//   M1 点任意一张都开第一条（`openDetail(items[0])`）   KILLED
//   M2 交出去的是重新拼的对象（只剩 video_url）           KILLED
//   M3 把 $event 当视频发出去                              KILLED
//   M6 事件名改成 openVideo，App.vue 监听器接空           KILLED
//   M7 卡片丢了 type=button                                KILLED
//   M5 退回 div（这一条是假 HARNESS_ERROR：`<button>`→`<div>` 破坏 SFC 括号
//      配对，测出的是语法错；它另有 `mutation-ui-fixes.mjs` M1 守着）
// 所以本组**刻意不补点击**——那只会把更强的文本判据抄一份。
//
// 唯一存活的是 **M4「只渲染第一条卡片」**（`items.slice(0, 1)`，零条变红）。
// 顺着查下去发现问题不止于点击：整个社区列表**渲染出来的条数与归属**，
// 全前端测试树里一条断言都没有——
//   · `findAll('li img')` 只问「有没有 img」（缩略图那三条）
//   · `findAll('li span')` 只问「某个标签有没有出现过」
//   · 没有任何一条问「服务端给了 3 条，该不该渲染出 3 张卡」
// 后果不是测试难看，是**用户看到列表少了一半而界面上没有任何东西变红**。

describe('列表渲染的条数、顺序与归属', () => {
  /** 三条各带互不相同的标题 / 链接 / 封面，才能分辨「哪条渲染成了哪条」。 */
  const three = page({
    items: [
      card({ id: 1, video_title: '甲', video_url: 'https://v.example/a',
        cover_url: '/u/a.jpg', tags: ['科普'] }),
      card({ id: 2, video_title: '乙', video_url: 'https://v.example/b',
        cover_url: '/u/b.jpg', tags: [] }),
      card({ id: 3, video_title: '丙', video_url: 'https://v.example/c',
        cover_url: '/u/c.jpg', tags: ['教程', '编程'] }),
    ],
    total: 3,
    page: 1,
    total_pages: 1,
  })

  // `li > button`：卡片是 li 的直接子 button。标签 chip 在 TagFilterRow 自己的
  // div 里、翻页按钮在一个裸 div 里，两者都不在 li 内，所以这个选择器只命中卡片。
  const cardBtns = (w) => w.findAll('li > button')

  test('服务端给几条就渲染几张卡片', async () => {
    const w = await mountPage(three)
    expect(cardBtns(w).length,
      '列表少渲染了 —— 用户看到的是社区不完整，而界面上没有任何东西会变红')
      .toBe(3)
  })

  test('顺序与服务端给的顺序一致', async () => {
    // 与上一条是**不同的坏法**：条数对、顺序错。后端按 created_at DESC 排序，
    // 顺序本身是产品语义；用 toEqual 而不是「三条都在」——后者对
    // 「渲染了甲乙甲」这种照样绿。
    const w = await mountPage(three)
    const titles = cardBtns(w).map((b) => b.find('h3').text().trim())
    expect(titles).toEqual(['甲', '乙', '丙'])
  })

  test('每张卡片上的链接与封面都是它自己那一条的', async () => {
    // 闭合在**读方向**的同款：v-for 里读错变量（`items[0]` 或某个被提到循环外
    // 的常量），症状是所有卡片显示同一个链接。标题还在、链接全一样，
    // 用户点哪张都开到同一条视频。既有判据只查「某条链接出现过」，恒真。
    const w = await mountPage(three)
    const urls = cardBtns(w).map((b) => b.find('p').text().trim())
    expect(urls).toEqual(['https://v.example/a', 'https://v.example/b', 'https://v.example/c'])
    const srcs = cardBtns(w).map((b) => b.find('img').attributes('src'))
    expect(srcs).toEqual(['/u/a.jpg', '/u/b.jpg', '/u/c.jpg'])
  })
})

