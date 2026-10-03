/**
 * 社区页标签筛选：多选 + 服务端完整清单 + 单行展开。
 * 运行：cd frontend && node --test tests/community-tags-ui.test.mjs
 *
 * 背景：筛选行原来从**当前页卡片**汇总标签，于是翻页或选中某个标签之后，
 * 其余标签会整片消失，用户没法叠加第二个筛选（只能先清空重来）。这个文件
 * 盯住三件事：
 *
 *  1. 选项来自服务端完整清单 /api/community/tags，取一次后常驻；
 *  2. 多选取并集，`tag=a,b` 真的发到后端；
 *  3. 选择之后 chips 数量一个不少（用户点名的要求）。
 *
 * 测法沿用本仓约定：api/*.js 用假 fetch 真跑一遍；.vue 部分把
 * `<script setup>` 里的函数抠出来、放进 `with (scope)` **真跑**——
 * 跑的是源码里那一份，不是测试里重写一遍的近似实现（后者与实现漂移
 * 时照样全绿）。
 *
 * ⚠️ 下面一律惰性化到 test() 体内。`node --test` 对 **describe 体里**抛出的
 * 异常给出的退出码是 0，runner 还会报 tests 0 / fail 0——那个 suite 的测试
 * 一条都没注册。所以 readFileSync / 抠函数都不许出现在 describe 回调体里。
 */
import { test, describe, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import axios from 'axios'
import { ref } from 'vue'

import {
  fetchCommunityTags, searchCommunity, fetchCommunityVideos,
} from '../src/api/community.js'
import { toggleTag, tagsToQuery, needsExpand, shouldShowToggle, parseTagQuery, countHiddenChips } from '../src/lib/tag-filter.js'

// ─────────────────────────────────────────────────────────
// 助手
// ─────────────────────────────────────────────────────────

/** 归一化行尾符：混着写的一批文件里按 \n 切块会切空。 */
function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

/**
 * 剥注释。断言要看的是**代码**，不是解释代码的散文——
 * 不剥掉的话，下面「选项不再从当前页汇总」那条会被模板里那段
 * 说明「之前是从当前页卡片汇总」的注释自己判成违规。
 *
 * `//` 只剥前面不是冒号的那种，否则 `https://x` 会被削成 `https:`，
 * 域名连同整行一起消失，扫源码的断言于是永远看不到它。
 */
function stripComments(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((l) => l.replace(/(^|[^:])\/\/.*$/, '$1'))
    .join('\n')
}

/** 惰性 + 记忆：读文件与剥注释都推迟到第一次真正需要时。 */
function lazyReader(...parts) {
  let cache = null
  return () => (cache === null ? (cache = stripComments(read(...parts))) : cache)
}

const pageVue = lazyReader('../src/components/CommunityPage.vue')
const tagRowVue = lazyReader('../src/components/TagFilterRow.vue')

/**
 * 抠出 `function name(...) { ... }` / `async function name(...) { ... }` 整段。
 *
 * 收尾取下一个**行首**的 `}`：本仓的缩进风格保证函数体内的收尾括号都带
 * 缩进，只有函数自己的收尾在行首。抠不出就 assert，而不是返回空串——
 * 空串会让下面所有断言恒真。
 */
function fnText(code, name) {
  // 必须连 `async ` 一起抠：从 `function` 那个词开始切会把 async 丢掉，
  // 抠出来的函数体里留着 await —— 那是一个只在抠取时才成立的假报错。
  const m = new RegExp(`(?:^|\\n)((?:async )?function ${name}\\()`).exec(code)
  assert.ok(m, `CommunityPage.vue 里没有 ${name}`)
  const at = m.index + m[0].length - m[1].length
  const end = code.indexOf('\n}\n', at)
  assert.notEqual(end, -1, `${name} 没读到收尾大括号 —— 抠取失败，下面的断言会恒真`)
  return code.slice(at, end + 3)
}

/**
 * 把若干函数声明放进 `with (scope)` 里，返回可调用的真函数。
 *
 * refs 与 api 全部由 scope 提供，于是测试能同时做到两件事：跑源码里那
 * 一份函数，又能精确控制它的依赖。代价是函数体必须自包含，一旦它开始
 * 引用没放进 scope 的东西就会 ReferenceError——那属于「这条测试不再
 * 适用」，得改测试而不是绕过它。
 */
function withScope(code, names, scope) {
  const decls = names.map((n) => fnText(code, n)).join('\n')
  const factory = new Function('scope', `with (scope) {\n${decls}\nreturn { ${names.join(', ')} }\n}`)
  return factory(scope)
}

/** load() 需要的全套作用域。overrides 用来替换其中的依赖。 */
function loadScope(overrides = {}) {
  return Object.assign({
    loading: ref(true),
    loadError: ref(''),
    tooShort: ref(null),
    needLogin: ref(false),
    keyword: ref(''),
    activeQuery: ref(''),
    activeTags: ref([]),
    tagOptions: ref([]),
    page: ref(1),
    pageSize: 20,
    items: ref([]),
    total: ref(0),
    totalPages: ref(0),
    searchCommunity: async () => ({ items: [], total: 0 }),
    fetchCommunityVideos: async () => ({ items: [], total: 0 }),
    tagsToQuery,
  }, overrides)
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { 'content-type': 'application/json' },
  })
}

/**
 * 记下每次请求打到哪。两种入参形状都要吃：手写 fetch 是 (url, init)，
 * 而 axios 的 fetch adapter 直接传一个 **Request 对象**。
 */
function spyFetch(responder) {
  const calls = []
  globalThis.fetch = async (input, init = {}) => {
    const asRequest = input instanceof Request
    calls.push({ url: asRequest ? input.url : String(input) })
    return responder(asRequest ? input.url : String(input), init)
  }
  return calls
}

beforeEach(() => {
  // axios 在 Node 里默认走 http adapter，会真的去解析域名（ENOTFOUND）。
  // 两件事必须钉住，否则这些用例测的是网络而不是接线：
  //   baseURL —— 相对路径 '/api/...' 在 Node 里是 ERR_INVALID_URL
  //   adapter —— 换成 fetch，下面的 globalThis.fetch 假实现才拦得住
  axios.defaults.baseURL = 'http://community.test'
  axios.defaults.adapter = 'fetch'
  globalThis.localStorage = {
    _v: { auth_token: 'test-token' },
    getItem(k) { return this._v[k] ?? null },
    setItem(k, v) { this._v[k] = String(v) },
    removeItem(k) { delete this._v[k] },
  }
})

/** 社区里一共存在的标签（服务端那份清单的形状：{tag, count}） */
const SERVER_TAGS = [
  { tag: '编程', count: 20 },
  { tag: '人工智能', count: 17 },
  { tag: '前端开发', count: 11 },
  { tag: '其他', count: 9 },
  { tag: '测试', count: 6 },
  { tag: '运维', count: 4 },
  { tag: '摄影', count: 3 },
  { tag: '音乐', count: 2 },
]

// ─────────────────────────────────────────────────────────
// 一 · 选项来自服务端完整清单
// ─────────────────────────────────────────────────────────

describe('标签选项 · 来自服务端完整清单，不是当前页汇总', () => {
  test('汇总当前页卡片的那段写法已经不在了', () => {
    const code = pageVue()
    assert.doesNotMatch(code, /for \(const item of items\.value\)/,
      '筛选行又变回从当前页卡片汇总了 —— 翻页或选中标签后其余标签会消失')
    assert.doesNotMatch(code, /const tagOptions = computed\(/,
      'tagOptions 不该是随结果集一起变的 computed')
    assert.match(code, /const tagOptions = ref\(/,
      '标签清单应当是取一次就常驻的 ref')
  })

  test('标签清单真的打 /api/community/tags，原样返回带条数的 items', async () => {
    const calls = spyFetch(() => jsonResponse({ items: SERVER_TAGS }))
    const got = await fetchCommunityTags()
    assert.equal(new URL(calls[0].url).pathname, '/api/community/tags', '打错了端点')
    assert.deepEqual(got, SERVER_TAGS, '清单被前端二次加工过 —— 条数或顺序不该动')
    assert.deepEqual(got.map((t) => t.tag), SERVER_TAGS.map((t) => t.tag),
      '标签顺序被前端改过（服务端词表顺序就是展示顺序）')
  })

  test('未登录也能取清单（这个端点是公开的）', async () => {
    globalThis.localStorage.removeItem('auth_token')
    const calls = spyFetch(() => jsonResponse({ items: [] }))
    await fetchCommunityTags()
    assert.equal(calls.length, 1, '未登录时根本没发起清单请求')
  })

  test('清单只在 loadTags 里被写：翻页与选标签都不再动它', () => {
    const code = pageVue()
    const start = code.indexOf('async function loadTags(')
    const end = code.indexOf('\n}\n', start) + 2
    assert.ok(start >= 0 && end > start, '找不到 loadTags —— 判据前提不成立')

    const outside = []
    for (let i = code.indexOf('tagOptions.value ='); i !== -1; i = code.indexOf('tagOptions.value =', i + 1)) {
      if (i < start || i >= end) outside.push(code.slice(i, code.indexOf('\n', i)).trim())
    }
    assert.deepEqual(outside, [],
      `这些地方在 loadTags 之外改了标签清单：${outside.join(' | ')}\n`
      + '  —— 只要有一处跟着结果集重算标签，「选中后 chips 不减少」就不成立')
  })

  test('挂载时取一次清单', () => {
    const m = pageVue().match(/onMounted\(\(\)\s*=>\s*\{([\s\S]*?)\n\}\)/)
    assert.ok(m, 'onMounted 不是同时起两条取数路径')
    assert.match(m[1], /loadTags\(\)/, '挂载时没有取标签清单')
    assert.match(m[1], /load\(\)/, '挂载时没有取社区列表')
  })

  test('清单加载失败要说出来，而不是静默变成「社区没有标签」', async () => {
    const code = pageVue()
    const tagOptions = ref(SERVER_TAGS)
    const tagLoadError = ref('')
    const { loadTags } = withScope(code, ['loadTags'], {
      tagOptions, tagLoadError,
      fetchCommunityTags: async () => { throw new Error('500 Internal Server Error') },
    })
    await loadTags()
    assert.match(tagLoadError.value, /500/,
      '失败原因没有被记下来 —— 筛选行空着，用户会以为是自己筛出来的')
    assert.deepEqual(tagOptions.value, [], '失败后还留着上一次的清单，用户会以为那是全部')
    assert.match(code, /v-if="tagLoadError"/, '模板里没有失败提示分支')
    assert.match(code, /标签列表加载失败/, '失败提示没有说清是标签清单挂了')
  })
})


// ─────────────────────────────────────────────────────────
// 二 · 多选取并集
// ─────────────────────────────────────────────────────────

describe('多选 · 累计而不互相顶掉', () => {
  test('纯函数层面：选第二个不会取消第一个', () => {
    assert.deepEqual(toggleTag([], '编程', true), ['编程'])
    assert.deepEqual(toggleTag(['编程'], '人工智能', true), ['编程', '人工智能'])
    assert.deepEqual(toggleTag(['编程', '人工智能'], '编程', true), ['人工智能'])
  })

  test('组件状态层面：两次点标签后 activeTags 真的有两个', () => {
    const code = pageVue()
    const activeTags = ref([])
    const loads = []
    const { onTagsChange } = withScope(code, ['onTagsChange'], {
      activeTags, page: ref(1), load: () => loads.push(1),
    })
    // 两次事件，形状与 TagFilterRow 发出的完全一致
    onTagsChange(toggleTag([], '编程', true))
    onTagsChange(toggleTag(['编程'], '人工智能', true))
    assert.deepEqual(activeTags.value, ['编程', '人工智能'],
      '第二个标签把第一个顶掉了 —— 这不是多选')
    assert.deepEqual(loads, [1, 1], '每次选择都应重新取一次列表')
  })

  test('并集真的发到后端：两个标签都在请求参数里', async () => {
    const code = pageVue()
    const activeTags = ref([])
    const { onTagsChange } = withScope(code, ['onTagsChange'], {
      activeTags, page: ref(1), load: () => {},
    })
    onTagsChange(toggleTag([], '编程', true))
    onTagsChange(toggleTag(['编程'], '人工智能', true))

    const calls = spyFetch(() => jsonResponse({ items: [], total: 0 }))
    await searchCommunity({ tag: tagsToQuery(activeTags.value) })
    const url = new URL(calls[0].url)
    assert.equal(url.searchParams.get('tag'), '编程,人工智能',
      '并集查询串没发出去（只取第一个的话这里会是「编程」）')
    assert.deepEqual(parseTagQuery(url.searchParams.get('tag')), ['编程', '人工智能'])
  })

  test('未选中任何标签时不传 tag（退回公开浏览）', async () => {
    const calls = spyFetch(() => jsonResponse({ items: [], total: 0 }))
    await searchCommunity({ q: '', tag: tagsToQuery([]) })
    assert.equal(new URL(calls[0].url).searchParams.has('tag'), false,
      '没选任何标签却发了一个空 tag 过去')
    await searchCommunity({ q: '', tag: tagsToQuery(['编程']) })
    assert.equal(new URL(calls[1].url).searchParams.get('tag'), '编程')
  })

  test('load 真发出去的 tag 是并集：两个选中标签都在 URL 里', async () => {
    // 这里不打桩 searchCommunity：打桩了就变成在测桩，而不是在测
    // 「组件怎么把 activeTags 组装成查询参数」。
    const code = pageVue()
    const calls = spyFetch(() => jsonResponse({ items: [], total: 0 }))
    const scope = loadScope({ activeTags: ref(['编程', '人工智能']) })
    scope.searchCommunity = searchCommunity
    scope.fetchCommunityVideos = fetchCommunityVideos
    const { load, applyList } = withScope(code, ['load', 'applyList'], scope)
    await load()
    assert.equal(scope.loadError.value, '', `load 失败了：${scope.loadError.value}`)
    assert.equal(new URL(calls[0].url).pathname, '/api/community/search')
    assert.equal(new URL(calls[0].url).searchParams.get('tag'), '编程,人工智能',
      'load 没有把两个标签都发出去 —— 只取第一个的话这里会是「编程」')

    // 反过来：什么都没选时不得带 tag
    scope.activeTags.value = []
    await load()
    assert.equal(new URL(calls[1].url).searchParams.has('tag'), false,
      '没选任何标签，load 却发了一个 tag 过去')
  })

  test('界面上写明多选取并集，别让用户猜', () => {
    const code = pageVue()
    assert.match(code, /多选/, '没有告诉用户可以多选')
    assert.match(code, /并集/, '多选是并集还是交集，界面上没说 —— 用户只能靠试')
  })
})


// ─────────────────────────────────────────────────────────
// 三 · 选中标签不减少 chips 数量
// ─────────────────────────────────────────────────────────

describe('选择之后 chips 数量不变（用户点名的要求）', () => {
  test('选中两个标签、并按新条件重取列表之后，清单原封不动', async () => {
    const code = pageVue()
    // 一个 ref 就是真实组件里那一个：三条路径共用它，才测得出「谁动了它」
    const tagOptions = ref([])
    const activeTags = ref([])

    const { loadTags } = withScope(code, ['loadTags'], {
      tagOptions, tagLoadError: ref(''), fetchCommunityTags: async () => SERVER_TAGS,
    })
    await loadTags()
    assert.equal(tagOptions.value.length, SERVER_TAGS.length, '清单没取回来')

    const { onTagsChange } = withScope(code, ['onTagsChange'], {
      activeTags, tagOptions, page: ref(1), load: () => {},
    })
    onTagsChange(toggleTag([], '编程', true))
    onTagsChange(toggleTag(['编程'], '人工智能', true))
    assert.equal(activeTags.value.length, 2)
    assert.deepEqual(tagOptions.value, SERVER_TAGS, '只是选中标签，chips 就变了')

    // 列表真的按并集重取，且结果确实变了（否则上面两条是恒真的）
    const items = ref([])
    const scope = loadScope({
      tagOptions, activeTags, items,
      searchCommunity: async () => ({ items: [{ id: 1, tags: ['编程'] }], total: 1 }),
    })
    const { load, applyList } = withScope(code, ['load', 'applyList'], scope)
    await load()
    assert.equal(items.value.length, 1, '列表没按新条件重取')
    assert.equal(scope.loadError.value, '', `重取失败：${scope.loadError.value}`)
    assert.deepEqual(tagOptions.value, SERVER_TAGS,
      '重取结果之后 chips 变了 —— 标签清单被结果集带跑了')
  })

  test('翻页也不动清单', () => {
    const code = pageVue()
    const tagOptions = ref(SERVER_TAGS)
    const loads = []
    const { goPage } = withScope(code, ['goPage'], {
      page: ref(1), totalPages: ref(5), tagOptions, load: () => loads.push(1),
    })
    goPage(2)
    assert.deepEqual(tagOptions.value, SERVER_TAGS, '翻页之后 chips 变了')
    assert.deepEqual(loads, [1], '翻页没有触发重取')
  })
})


// ─────────────────────────────────────────────────────────
// 四 · 单行 + 展开键
// ─────────────────────────────────────────────────────────

describe('标签行 · 只占一行，溢出交给展开键', () => {
  test('筛选行用共享组件，页面不再自己画标签按钮', () => {
    const code = pageVue()
    const tpl = code.slice(0, code.indexOf('</script>'))
    assert.match(tpl, /<TagFilterRow\b/, '标签行没有用共享组件')
    assert.match(tpl, /@update:selected="onTagsChange"/, '选择事件没接上')
    assert.match(tpl, /:tags="tagOptions"/, '传给组件的不是服务端那份清单')
    assert.match(tpl, /:selected="activeTags"/, '选中集合没传进去')
    assert.doesNotMatch(tpl, /v-for="t in tagOptions"/, '页面还在自己画标签按钮')
    assert.doesNotMatch(tpl, /flex-wrap items-center gap-2 mb-5/,
      '退回自己那套会换行的标签行了 —— 标签一多就要占好几行')
  })

  test('共享组件：收起态单行裁剪 + 展开键带 aria-expanded', () => {
    const row = tagRowVue()
    assert.match(row, /flex-nowrap overflow-hidden/, '收起态不是单行裁剪')
    assert.match(row, /展开其余/, '没有「展开其余 N 个标签」按钮')
    assert.match(row, /收起标签/, '没有收起按钮')
    assert.match(row, /aria-expanded/, '展开键没有 aria-expanded')
    assert.match(row, /aria-pressed/, '标签按钮没有 aria-pressed（多选必须能说清当前选中态）')
  })

  test('展开键按真实溢出出现：needsExpand 真跑一遍', () => {
    assert.equal(needsExpand(300, 200), true, '明明溢出了却不给展开键')
    assert.equal(needsExpand(200, 200), false, '放得下却冒出展开键')
    assert.equal(needsExpand(201, 200), false, '亚像素差被当成溢出了')

    // 断的是**接线**，不是字面量。写 `scrollWidth > el.clientWidth` 看着稳，
    // 其实两头都会错：组件改成调被测的纯函数就假红，组件里留一份自己的
    // 副本又照样绿 —— 测的根本不是上线那份代码。
    const row = tagRowVue()
    assert.match(row, /import\s*\{[^}]*\bneedsExpand\b[^}]*\}\s*from\s*'\.\.\/lib\/tag-filter\.js'/,
      '组件没从 lib/tag-filter.js 引入 needsExpand')
    assert.match(row, /needsExpand\(\s*el\.scrollWidth\s*,\s*el\.clientWidth\s*\)/,
      '溢出判断没有拿真实的 scrollWidth/clientWidth 去调 needsExpand')
  })

  test('展开态必须留着「收起」入口：真浏览器里点一次就复现过的 bug', () => {
    // 组件曾经写成 `overflowing.value = needsExpand(..., expanded.value)`：
    // 展开态下 needsExpand 恒为 false，overflowing 被抹掉，按钮随之消失 ——
    // 行已经换行了，用户却再也收不回去。判据必须按在「按钮还在」上，
    // 而不是按在「overflowing 等于什么」上。
    assert.equal(shouldShowToggle(4, false, true), true, '展开态下收起键不见了')
    assert.equal(shouldShowToggle(4, true, false), true)
    assert.equal(shouldShowToggle(4, false, false), false)
    assert.equal(shouldShowToggle(1, true, true), false)

    const row = tagRowVue()
    assert.match(row, /shouldShowToggle\(/, '组件没有用 shouldShowToggle 决定显隐')
    // 旧写法：只靠 overflowing 决定，于是展开态下按钮会消失
    assert.doesNotMatch(row, /const showToggle = computed\(\(\) => props\.tags\.length > 1 && overflowing\.value\)/,
      'showToggle 又退回「只看 overflowing」了 —— 展开后按钮会消失')
  })

  test('展开键上的 N 是真被裁掉的个数，不是标签总数', () => {
    // 5 个 chip，只有后 2 个越过右边缘 → 只能是 2。
    // 用 `tags.length - 1` 那种「标签总数减一」的实现在这里会给出 4，
    // 两种实现被同一组数据分开。
    const rights = [100, 150, 200, 260, 300]
    assert.equal(countHiddenChips(rights, 250), 2, '数出来的隐藏个数不对')
    assert.equal(countHiddenChips([100, 150], 250), 0, '没溢出却数出隐藏标签')
    assert.equal(countHiddenChips([100, 300], 250), 1, '只溢出最后一个却没数出来')
    // 非数组不该让组件崩（测量前 children 为空时就是这个形状）
    assert.equal(countHiddenChips(null, 250), 0)
    assert.equal(countHiddenChips([], 250), 0)

    // 组件必须真的调它，而不是自己 filter 出一份 —— 断的是接线。
    const row = tagRowVue()
    assert.match(row, /import\s*\{[^}]*\bcountHiddenChips\b[^}]*\}\s*from\s*'\.\.\/lib\/tag-filter\.js'/,
      '组件没从 lib/tag-filter.js 引入 countHiddenChips')
    assert.match(row, /countHiddenChips\(/, '组件没有调用 countHiddenChips')
    assert.doesNotMatch(row, /tags\.length\s*-\s*1/,
      '展开键上的 N 又退回「标签总数减一」了 —— 那是编的，不是量的')
  })
})


/**
 * measure() 真跑一遍。
 *
 * 「标签一多就该出现展开键」是用户要的那条能力，而它的全部风险都在接线：
 * 测了、算对了，却没写回 overflowing，界面上就是永远没有展开键。
 * 断「源码里出现过 measure」看不出这个，所以直接拿假 DOM 把它跑起来。
 */
describe('TagFilterRow · measure 真跑', () => {
  function chip(right) {
    return {
      getBoundingClientRect: () => ({ right }),
      hasAttribute: (name) => name === 'data-tag-chip',
    };
  }

  /**
   * 造一行标签，只给测试需要的三个读数：整行溢出量、可视宽度、每个 chip 的右边缘。
   *
   * **三个读数必须同一套坐标系。** 早先 right 默认给 1000、chip 给 100~300，
   * 结果没有一片越界，测出来 hidden = 0 —— 代码是对的，夹具在说谎。
   * 这里把行容器的右边缘默认放在 250：行宽 200 + 左偏移 50，于是
   * chip 的 right 落在 100~300 就是「前几个在行内、后几个被裁掉」。
   *
   * `allLabelRight` 模拟最前面那个「全部」按钮 —— 它不是标签，不该被算进
   * 「展开其余 N 个标签」。
   */
  function fakeRow({ scrollWidth, clientWidth, rights, right = 250, allLabelRight = null }) {
    const children = rights.map(chip);
    if (allLabelRight !== null) {
      children.unshift({ ...chip(allLabelRight), hasAttribute: () => false });
    }
    return {
      scrollWidth,
      clientWidth,
      getBoundingClientRect: () => ({ right }),
      children,
    };
  }

  function runMeasure(row, { expanded = false, hidden = 0, overflowing = false } = {}) {
    const scope = {
      rowEl: ref(row),
      expanded: ref(expanded),
      overflowing: ref(overflowing),
      hiddenCount: ref(hidden),
      needsExpand,
      countHiddenChips,
    }
    withScope(tagRowVue(), ['measure'], scope).measure()
    return { overflowing: scope.overflowing.value, hidden: scope.hiddenCount.value }
  }

  test('溢出：给出展开键，N 是真被裁掉的个数（5 个标签只藏 2 个）', () => {
    const out = runMeasure(fakeRow({
      scrollWidth: 320, clientWidth: 200, rights: [100, 150, 200, 260, 300],
    }))
    assert.equal(out.overflowing, true, '明明溢出了却不给展开键')
    assert.equal(out.hidden, 2, 'N 不是真被裁掉的个数')
  })

  test('放得下：没有展开键，且把上一次的 N 归零', () => {
    const out = runMeasure(
      fakeRow({ scrollWidth: 200, clientWidth: 200, rights: [100, 150] }),
      { hidden: 3 },
    )
    assert.equal(out.overflowing, false, '放得下却冒出展开键')
    assert.equal(out.hidden, 0, '不溢出却留着旧的 N')
  })

  test('展开态：measure 一个状态都不碰，保留上一次的判断与 N', () => {
    // 早先这里断言的是 `overflowing === false` —— 把 bug 写成了期望值。
    // 展开态下 measure 必须原样保留：overflowing 决定「收起」入口还在不在，
    // N 是收起键上那个数字，两者被抹掉都会让用户卡在展开态收不回去。
    //
    // **宽度必须用换行后的形状（scrollWidth == clientWidth）。** 展开态下
    // 那一行本来就 wrap 了，不再溢出；只有拿这个形状去测，去掉 early return
    // 才会让 overflowing 真的变 false。早先误用了「不换行但溢出」的宽度，
    // 于是 needsExpand 照样返回 true，两种实现结果一模一样 —— 变异存活，
    // 判据量不到它想量的东西。
    const out = runMeasure(
      fakeRow({ scrollWidth: 190, clientWidth: 190, rights: [65, 130, 218, 72] }),
      { expanded: true, hidden: 2, overflowing: true },
    )
    assert.equal(out.overflowing, true,
      '展开态把 overflowing 抹成了 false —— 收起键会跟着消失')
    assert.equal(out.hidden, 2, '展开态把 N 清掉了 —— 收起的一瞬间展开键会闪一下 0')
  })

  test('宽度真的超过可视宽度才给展开键（差 1px 的亚像素不算）', () => {
    const near = runMeasure(fakeRow({
      scrollWidth: 201, clientWidth: 200, rights: [100, 150, 201],
    }))
    assert.equal(near.overflowing, false, '亚像素差被当成了溢出')
  })

  test('测量的结果确实写回状态：溢出判定没接到界面上是最难查的一种坏', () => {
    // overflowing 初始为 false，只有 measure 真把结论写进去才会变 true。
    // 把 `overflowing.value = ...` 删掉，这一条立刻转红。
    const out = runMeasure(fakeRow({ scrollWidth: 900, clientWidth: 200, rights: [300] }))
    assert.equal(out.overflowing, true, 'measure 没有把测量结果写回 overflowing')
  })

  test('N 只数标签，不数最前面那个「全部」按钮', () => {
    // 按钮上写的是「展开其余 N 个标签」。若把「全部」也算进去，N 与文案就对不上
    // —— 今天不产生偏差（最左的 chip 永远不是被裁的那个），但那是碰巧。
    const out = runMeasure(fakeRow({
      scrollWidth: 400, clientWidth: 200, right: 250,
      rights: [100, 150, 300], allLabelRight: 260,
    }))
    assert.equal(out.overflowing, true)
    assert.equal(out.hidden, 1, '把「全部」按钮也算进「展开其余 N 个标签」了')
  })
})


// ─────────────────────────────────────────────────────────
// 五 · 清除
// ─────────────────────────────────────────────────────────

describe('清除 · 关键词与标签一起清', () => {
  test('clearSearch 会一并清空标签选择', () => {
    const code = pageVue()
    const loads = []
    const scope = {
      keyword: ref('机器学习'),
      activeQuery: ref('机器学习'),
      activeTags: ref(['编程', '人工智能']),
      page: ref(3),
      load: () => loads.push(1),
    }
    const { clearSearch } = withScope(code, ['clearSearch'], scope)
    clearSearch()
    assert.deepEqual(scope.activeTags.value, [], '清除后标签还留着，筛选行与结果集对不上')
    assert.equal(scope.activeQuery.value, '', '关键词没清')
    assert.equal(scope.keyword.value, '', '输入框没清')
    assert.equal(scope.page.value, 1, '清完还停在原页码上')
    assert.deepEqual(loads, [1])
  })

  test('清除按钮的可见条件跟着标签数组走', () => {
    assert.match(pageVue(), /v-if="activeQuery \|\| activeTags\.length"/,
      '清除按钮的显示条件还挂在旧的单值标签上')
  })
})


// ─────────────────────────────────────────────────────────
// 六 · 三套既有行为不许被顺手删掉
// ─────────────────────────────────────────────────────────

describe('既有行为 · needLogin / loadError / tooShort 都还在', () => {
  test('needLogin：未登录退回公开列表，标签筛选不丢', async () => {
    const code = pageVue()
    const seen = {}
    const scope = loadScope({
      activeTags: ref(['编程']),
      searchCommunity: async () => ({ items: [], total: 0, needLogin: true }),
      fetchCommunityVideos: async (args) => {
        seen.args = args
        return { items: [{ id: 3, tags: ['编程'] }], total: 1, page: 1, total_pages: 1 }
      },
    })
    scope.activeQuery.value = '机器学习'
    const { load, applyList } = withScope(code, ['load', 'applyList'], scope)
    await load()
    assert.equal(scope.needLogin.value, true, '未登录没有走 needLogin 分支')
    assert.equal(scope.activeQuery.value, '', '未登录没有退回公开浏览')
    assert.equal(scope.keyword.value, '', '未登录没有清掉输入框')
    assert.equal(seen.args.tag, '编程', '退回公开列表时标签筛选丢了')
    assert.equal(scope.items.value.length, 1)
  })

  test('loadError：故障被记下来，items 清空（与「真的没有」分开）', async () => {
    const code = pageVue()
    const scope = loadScope({
      searchCommunity: async () => { throw new Error('boom 500') },
    })
    scope.items.value = [{ id: 1, tags: ['编程'] }]
    scope.total.value = 7
    const { load, applyList } = withScope(code, ['load', 'applyList'], scope)
    await load()
    assert.equal(scope.loadError.value, 'boom 500', '故障原因没被记下来')
    assert.deepEqual(scope.items.value, [], '故障后 items 没清空')
    assert.equal(scope.total.value, 0)
    assert.equal(scope.totalPages.value, 0)
  })

  test('tooShort：短查询提示读后端给的 min_chars，不在前端写死', async () => {
    const code = pageVue()
    const scope = loadScope({
      searchCommunity: async () => ({ items: [], total: 0, q_too_short: true, min_chars: 3 }),
    })
    scope.activeQuery.value = '机器'
    const { load, applyList } = withScope(code, ['load', 'applyList'], scope)
    await load()
    assert.deepEqual(scope.tooShort.value, { q: '机器', min: 3 },
      '短查询提示没按后端给的 min_chars 显示')
    const hardcoded = stripComments(read('../src/components/CommunityPage.vue'))
      .match(/length\s*<\s*3|min_chars\s*[:=]\s*3/g) || []
    assert.deepEqual(hardcoded, [],
      `前端把分词器下限写死成了 3：${JSON.stringify(hardcoded)}`)
  })

  test('三套提示的模板分支都还在', () => {
    const code = pageVue()
    assert.match(code, /v-if="needLogin"/, '未登录提示区没了')
    assert.match(code, /v-else-if="loadError"/, '故障态分支没了')
    const failAt = code.indexOf('v-else-if="loadError"')
    const emptyAt = code.indexOf('v-else-if="!items.length"')
    assert.ok(failAt > -1 && emptyAt > -1, '故障态或空状态的分支找不到了')
    assert.ok(failAt < emptyAt, '故障态必须排在空状态之前，否则 loadError 会被渲染成「社区还是空的」')
    assert.match(code, /v-if="tooShort"/, '短查询提示区没了')
  })

  test('没有用 window.confirm 去拦清筛选', () => {
    assert.doesNotMatch(pageVue(), /\bconfirm\s*\(/,
      '别用 confirm 拦一次点击')
  })
})
