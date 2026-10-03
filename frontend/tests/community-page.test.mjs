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
import { fetchChatSession } from '../src/api/history.js'

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
    // 协议里的 // 不是注释：只剥「前面不是冒号」的那种，否则
        // `https://x.com` 会被削成 `https:`，域名连同后面整行一起消失，
        // 扫源码的断言于是永远看不到它 —— 这条判据会变成死的。
    .map((l) => l.replace(/(^|[^:])\/\/.*$/, '$1'))
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
 * 两条「别让用户看到假象」的行为。
 *
 * 上一组盯的是「不该显示什么」（列表不许出现内容字段）。这一组盯的是
 * 「故障和空结果不许长得一样」：一次 500 被渲染成「社区还是空的」，
 * 用户会以为社区没东西，于是反复换关键词——而真正的原因被吞掉了。
 */
describe('CommunityPage 把故障与空结果分开', () => {
  const pageCode = stripComments(pageVue)

  test('catch 记录故障原因，而不是把列表清空就完事', () => {
    const catchBlock = pageCode.slice(
      pageCode.indexOf('} catch'),
      pageCode.indexOf('} finally'),
    )
    assert.notEqual(catchBlock.length, 0, '没找到 load 的 catch 分支')
    assert.match(
      catchBlock, /loadError/,
      'catch 分支没有记录 loadError：一次 500 会被渲染成「社区还是空的」',
    )
  })

  test('故障态排在空状态之前', () => {
    const failIdx = pageCode.indexOf('v-else-if="loadError"')
    const emptyIdx = pageCode.indexOf('v-else-if="!items.length"')
    assert.notEqual(failIdx, -1, '模板里没有故障态分支')
    assert.ok(
      failIdx < emptyIdx,
      '故障态必须排在空状态之前，否则 loadError 时 items 为空，'
      + 'v-else-if 会先命中「社区还是空的」那一支',
    )
  })

  test('空状态的文案没有被故障分支借用', () => {
    const catchBlock = pageCode.slice(
      pageCode.indexOf('} catch'),
      pageCode.indexOf('} finally'),
    )
    assert.doesNotMatch(
      catchBlock, /emptyText|emptyHint/,
      'catch 里动了空状态文案：故障与「真的没有」会显示同一句话',
    )
  })
})


/**
 * 短查询的真相由后端说。
 *
 * trigram 滑窗至少 3 字符，2 字查询**必然**召回 0。后端把这条边界
 * 一起返回（q_too_short / min_chars）；前端照着显示即可。
 *
 * 前端自己判断长度是不允许的：那等于把分词器的实现细节复制一份到
 * 客户端，后端哪天换分词器，两边就各说各话了。
 */
describe('短查询提示由后端说了算', () => {
  const pageCode = stripComments(pageVue)

  test('读的是后端返回的 q_too_short / min_chars', () => {
    assert.match(pageCode, /res\.q_too_short/, '没有读后端返回的 q_too_short')
    assert.match(pageCode, /res\.min_chars/, '没有读后端返回的 min_chars')
    assert.match(pageVue, /v-if="tooShort"/, '模板里没有 tooShort 提示分支')
  })

  test('前端没有把 trigram 的下限写死', () => {
    const hardcoded = pageCode.match(/length\s*<\s*3|min_chars\s*[:=]\s*3/g) || []
    assert.deepEqual(
      hardcoded, [],
      `前端把分词器下限写死成了 3：${JSON.stringify(hardcoded)}。`
      + '换分词器时前后端会各说各话',
    )
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
    // 查的是**社区视频表**（fetchCommunityByUrl），不是个人解析历史。
    // 后者对「我解析过没有」成立，对「社区里有没有」不成立：
    // 陌生人打开社区视频时会被判成没有，于是复用提示与重新解析按钮
    // 都永远不出现。
    assert.match(handleParse, /fetchCommunityByUrl\(key\)/)
    assert.doesNotMatch(handleParse, /fetchHistoryByUrl/,
      '仍在用个人解析历史判断社区里有没有这一份')
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


describe('追问会话读出口（工单 #8 补单）', () => {
  test('按 url 取当前用户的会话，返回的正是 [{question, answer}]', async () => {
    const calls = spyFetch(() => jsonResponse({
      chat_history: [{ question: 'B 问的', answer: '答案::B 问的' }],
    }))
    const turns = await fetchChatSession('https://example.com/v/shared')

    assert.match(calls[0].url, /\/api\/history\/chat/, '打错了端点')
    assert.match(calls[0].url, /url=/, 'url 没作为查询参数传出去')
    assert.equal(calls[0].auth, 'Bearer test-token', '读自己的会话也要带鉴权头')
    assert.deepEqual(turns, [{ question: 'B 问的', answer: '答案::B 问的' }])
  })

  test('后端没给记录时返回空数组，不是 undefined', async () => {
    spyFetch(() => jsonResponse({}))
    assert.deepEqual(await fetchChatSession('u'), [])
  })

  test('读不到时把错误抛出去，不静默变成空列表', async () => {
    spyFetch(() => new Response('nope', { status: 401 }))
    await assert.rejects(() => fetchChatSession('u'))
  })

  test('VideoSummary 在解析完成与追问完成两条路上都回填', () => {
    const code = stripComments(summaryVue)
    const bodyOf = (name) => {
      const start = code.indexOf(`function ${name}(`)
      assert.ok(start >= 0, `找不到 ${name}`)
      const rest = code.slice(start)
      // 边界必须连 async function 一起算：只认 '\nfunction ' 的话，
      // 下一段 hydrateChatHistory 的**定义**会被算进上一个函数体里，
      // 于是把调用删掉这个用例照样绿（实测踩过）。
      const next = rest.slice(1).match(/\n(?:async )?function /)
      return next ? rest.slice(0, next.index + 1) : rest
    }
    assert.match(bodyOf('startSummarize'), /hydrateChatHistory\(\)/, '解析完成后没回填')
    assert.match(bodyOf('handleChat'), /hydrateChatHistory\(\)/, '追问完成后没回填')
  })

  test('回填排在 chatHistoryList 清空之后', () => {
    const code = stripComments(summaryVue)
    // 早于清空调用，填进去的记录会在同一次挂载里被抹掉
    const clear = code.indexOf('chatHistoryList.value = []')
    const hydrate = code.indexOf('hydrateChatHistory()')
    assert.ok(clear >= 0, '前提：找不到 chatHistoryList 的清空')
    assert.ok(hydrate > clear, '回填写在清空之前，会被同一次挂载抹掉')
  })

  test('组件内即时 push 保留，持久化仍然只由服务端落', () => {
    const code = stripComments(summaryVue)
    assert.match(code, /chatHistoryList\.value\.push\(/, '即时展示被去掉了，那是为了流畅性')
    assert.doesNotMatch(code, /saveChatToHistory/, '前端不该再有保存会话的调用')
  })
})
