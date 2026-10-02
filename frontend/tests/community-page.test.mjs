/**
 * 社区页接线（工单 #7 的前端部分）。运行：node --test tests/
 *
 * 测法与本仓既有的 community-tags.test.mjs 一致：.js 里的路由/行为用假 fetch
 * 真跑一遍，.vue 部分只做源码接线断言——渲染结果验证需要挂载环境，
 * 超出本仓「node --test 零额外依赖」的约定。
 *
 * 这个文件真正要守住的是一条**接线**约定：
 * 详情内容只能来自 /api/summarize（社区视频表），不能再有一条
 * 「从个人历史回填 AI 结果」的路。那条路一旦回来，同一链接就会在
 * 两个人屏幕上呈现两份不同总结——所以下面每个断言都在盯它没回来。
 */
import { test, describe, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import axios from 'axios'

import { fetchCommunityVideos, searchCommunity } from '../src/api/community.js'

/** 归一化行尾符：本仓库是 CRLF，直接按 \n 切块会切空。 */
function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

/**
 * 去掉注释。
 *
 * 断言要看的是**代码**，不是解释代码的散文。这段代码里有大段注释在
 * 描述「以前这里是拿 initialHistory 回填 summary_md」，不剥掉注释的话
 * 那些说明文字会把自己判成违规——断言被自己的注释推翻是可笑的。
 */
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((l) => l.replace(/\/\/.*$/, ''))
    .join('\n')
}

const communityApi = read('../src/api/community.js')
const appVue = read('../src/App.vue')
const summaryVue = read('../src/components/VideoSummary.vue')
const pageVue = read('../src/components/CommunityPage.vue')

function jsonResponse(body) {
  return new Response(JSON.stringify(body), {
    headers: { 'content-type': 'application/json' },
  })
}

/** 社区卡片：后端白名单投影里就这几个键 */
const CARD = {
  id: 7,
  video_url: 'https://youtu.be/dQw4w9WgXcQ',
  cover_url: 'https://img.example/c.jpg',
  video_title: '机器学习入门',
  tags: ['人工智能'],
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


/**
 * 记下请求打到哪、带没带 Authorization —— 接线断言靠这个而不是靠实现细节。
 *
 * 两种入参形状都要吃：手写 fetch 是 (url, init)，而 axios 的 fetch adapter
 * 直接传一个 **Request 对象**。只认前者的话，url 会变成 "[object Request]"，
 * headers 恒为 null——断言会以「参数没传出去」的形式假红。
 */
function spyFetch(responder) {
  const calls = []
  globalThis.fetch = async (input, init = {}) => {
    const asRequest = input instanceof Request
    const url = asRequest ? input.url : String(input)
    const auth = asRequest
      ? input.headers.get('authorization')
      : (init.headers?.Authorization ?? null)
    calls.push({ url, auth })
    return responder(url, auth)
  }
  return calls
}


describe('社区列表 · 未登录也能取', () => {
  test('不因缺 token 就跳过请求：列表是公开的', async () => {
    globalThis.localStorage.removeItem('auth_token')
    const calls = spyFetch(() => jsonResponse({ items: [CARD], total: 1, page: 1, page_size: 20, total_pages: 1 }))
    const res = await fetchCommunityVideos()
    assert.equal(calls.length, 1, '未登录时根本没有发起列表请求')
    assert.equal(res.items[0].video_title, '机器学习入门')
  })

  test('分页与标签筛选作为查询参数传出去', async () => {
    const calls = spyFetch(() => jsonResponse({ items: [], total: 0 }))
    await fetchCommunityVideos({ page: 3, pageSize: 10, tag: '人工智能' })
    const q = calls[0].url
    assert.match(q, /page=3/, '页码没传出去')
    assert.match(q, /page_size=10/, '页长没传出去')
    assert.match(q, /tag=/, '标签筛选没传出去')
  })

  test('已登录时带上 Bearer 头', async () => {
    const calls = spyFetch(() => jsonResponse({ items: [CARD], total: 1 }))
    await fetchCommunityVideos()
    assert.equal(calls[0].auth, 'Bearer test-token')
  })
})


describe('搜索 · 401 被翻译成 needLogin，而不是抛异常', () => {
  test('未登录搜索返回 needLogin 而不是把 401 变成报错', async () => {
    spyFetch(() => new Response(JSON.stringify({ detail: '请先登录' }), { status: 401 }))
    const res = await searchCommunity({ q: '机器学习' })
    assert.equal(res.needLogin, true, '401 必须被翻译成 needLogin')
    assert.deepEqual(res.items, [], '未登录时不该回填任何条目')
  })

  test('已登录搜索正常返回结果', async () => {
    spyFetch(() => jsonResponse({ items: [CARD], total: 1, needLogin: false }))
    const res = await searchCommunity({ q: '机器学习' })
    assert.equal(res.needLogin, false)
    assert.equal(res.items.length, 1)
  })

  test('非 401 的失败照常抛出：别把真故障一起吞成「请登录」', async () => {
    spyFetch(() => new Response('boom', { status: 500 }))
    await assert.rejects(() => searchCommunity({ q: 'x' }),
      '服务端故障被吞成了 needLogin')
  })

  test('关键词与标签都作为查询参数传出去', async () => {
    const calls = spyFetch(() => jsonResponse({ items: [], total: 0 }))
    await searchCommunity({ q: '异步', tag: '编程', page: 2 })
    assert.match(calls[0].url, /q=/)
    assert.match(calls[0].url, /tag=/)
    assert.match(calls[0].url, /page=2/)
  })
})


/**
 * 社区页组件：列表只渲染封面 / 标题 / 标签。
 *
 * 组件**不去读**内容字段——后端就算哪天放宽了白名单，这里也不会顺手显示出来。
 */
describe('CommunityPage 列表只渲染卡片字段', () => {
  const pageCode = stripComments(pageVue)
  const cardBlock = pageCode.slice(
    pageCode.indexOf('<ul v-else class="grid gap-4'),
    pageCode.indexOf('<!-- 翻页 -->'),
  )

  test('列表区渲染封面、标题、标签', () => {
    assert.notEqual(cardBlock.length, 0, '没找到卡片列表区')
    assert.match(cardBlock, /item\.cover_url/, '封面没渲染')
    assert.match(cardBlock, /\{\{ item\.video_title/)
    assert.match(cardBlock, /v-for="tag in item\.tags"/)
  })

  test('列表区不碰总结 / 字幕 / 思维导图', () => {
    for (const field of ['summary', 'mindmap', 'subtitle']) {
      assert.doesNotMatch(
        cardBlock, new RegExp(`item\\.${field}`),
        `列表区读了 item.${field}：内容字段不该出现在公开列表的渲染里`,
      )
    }
  })

  test('翻页与标签筛选都接到了 load 上', () => {
    assert.match(pageCode, /function goPage\(n\)/)
    assert.match(pageCode, /@click="goPage\(page \+ 1\)"/, '下一页按钮没接上')
    assert.match(pageCode, /function selectTag\(tag\)/)
    assert.match(pageCode, /@click="selectTag\(t\)"/, '标签按钮没接上')
  })

  test('未登录提示给出去登录入口，而不是显示空结果', () => {
    assert.match(pageCode, /v-if="needLogin"/, '没有未登录提示区')
    assert.match(pageCode, /\$emit\('need-login'\)/, '未登录提示没有去登录入口')
  })
})


/**
 * 最关键的一条：详情内容不得再从个人历史回填。
 *
 * 组件里仍然有 initialHistory 这个 prop，但它**只**能读 chat_history。
 * 下面两条分别盯住「不许读内容」与「不许把内容灌进那几个 ref」。
 */
describe('VideoSummary 详情只走 /api/summarize', () => {
  const setup = stripComments(summaryVue.slice(summaryVue.indexOf('const props = defineProps')))

  test('initialHistory 不再驱动总结 / 思维导图 / 字幕', () => {
    // props 声明本身不算「使用」——它只是把 prop 接进来。
    const uses = (setup.match(/initialHistory[^\n]*/g) || [])
      .map((l) => l.trim())
      .filter((l) => !/^initialHistory:\s*Object/.test(l))
    assert.ok(uses.length > 0, '前提不成立：initialHistory 一次也没被用到，断言无意义')
    for (const line of uses) {
      assert.match(line, /chat_history/,
        `initialHistory 被用在 chat_history 以外的地方：${line}`)
    }
  })

  test('总结内容只由总结流写入，不从 props 拷贝', () => {
    const writes = stripComments(summaryVue).match(/summaryMd\.value\s*=[^\n]*/g) || []
    const fromProps = writes.filter((l) => /props\.|initialHistory/.test(l))
    assert.deepEqual(fromProps, [], `总结被从 props 直接赋值：${JSON.stringify(fromProps)}`)
  })

  test('思维导图内容只由思维导图事件写入', () => {
    const writes = stripComments(summaryVue).match(/mindmapMd\.value\s*=[^\n]*/g) || []
    const fromProps = writes.filter((l) => /props\.|initialHistory/.test(l))
    assert.deepEqual(fromProps, [], `思维导图被从 props 直接赋值：${JSON.stringify(fromProps)}`)
  })

  test('聊天记录仍按原样回填（个人数据，不属社区内容）', () => {
    assert.match(setup, /chatHistoryList\.value = props\.initialHistory\.chat_history/,
      '个人问答历史没被回填，问答抽屉会变空')
  })
})


describe('App.vue 接线', () => {
  const appCode = stripComments(appVue)
  const handleParse = appCode.slice(
    appCode.indexOf('async function handleParse('),
    appCode.indexOf('function publishCard('),
  )

  test('by-url 命中不再让解析提前返回', () => {
    // 原来这里是 `const cached = await fetchHistoryByUrl(key)`，命中就把
    // cached.video_data 直接塞进 videoData 并 return。只要 `cached` 这个
    // 变量还在，就说明那条「拿到别人的内容就自己渲染」的路没走干净。
    assert.doesNotMatch(handleParse, /\bcached\b/,
      'by-url 的返回值仍被当成内容用：详情会绕过 /api/summarize 与社区视频表')
  })

  test('by-url 只被用来置提示标志', () => {
    assert.match(handleParse, /fetchHistoryByUrl\(key\)/)
    assert.match(handleParse, /fromCache\.value\s*=/)
  })

  test('详情渲染仍由 VideoSummary 承担（没把组件删掉绕过去）', () => {
    assert.match(appCode, /<VideoSummary/)
    assert.match(appCode, /:videoUrl="currentUrl"/)
  })

  test('社区页已挂上，并接了返回 / 登录 / 点开视频', () => {
    assert.match(appCode, /<CommunityPage/)
    assert.match(appCode, /@back="currentPage = 'home'"/)
    assert.match(appCode, /@need-login="showAuthModal\('login'\)"/)
    assert.match(appCode, /@open-video="openCommunityVideo"/)
  })

  test('点开社区视频先判登录，未登录走登录框', () => {
    const open = appCode.slice(
      appCode.indexOf('function openCommunityVideo('),
      appCode.indexOf('function reparse('),
    )
    assert.match(open, /isLoggedIn\(\)/)
    assert.match(open, /showAuthModal\('login'\)/)
    assert.match(open, /handleParse\(item\.video_url\)/)
  })

  test('解析成功后回填社区卡片的标题与封面', () => {
    assert.match(appCode, /publishCommunityCard\(\{/)
    assert.match(appCode, /video_title: data\?\.title \|\| ''/)
    assert.match(appCode, /cover_url: data\?\.thumbnail \|\| ''/)
  })

  test('卡片回填失败不打断主流程', () => {
    const publish = appCode.slice(
      appCode.indexOf('function publishCard('),
      appCode.indexOf('function reparse('),
    )
    assert.match(publish, /\.catch\(\(\) => \{\}\)/, '回填失败会打断解析流程')
  })
})
