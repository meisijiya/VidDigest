/**
 * BYOK 集中管理：一份状态、两条路径、一个出口。
 *
 * 原来自带凭据的状态在 VideoSummary 里（localStorage + 三个 ref），
 * 后果是**解析路径完全看不到它**——用户在追问框填了 key，以为解析也用上了，
 * 解析照样扣额度，而页面上没有任何地方能解释这个差别。
 * 本文件守的就是「两条路径读同一份状态」这条。
 *
 * 分三层：
 *   1. lib/byok.js —— 纯逻辑，真调用，测存储与出口纪律
 *   2. api/summarize.js —— 真调用，测凭据真的进了请求体
 *   3. .vue —— 沿用本仓约定走源码断言（渲染验证需要挂载环境）
 */
import { test, describe, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  getPublicState, getRequestCredential, save, updateConfig, clear,
  usePlatform, chooseProvider, validateBaseUrl, normalizeBaseUrl, normalizeModel, subscribe,
} from '../src/lib/byok.js'
import { summarizeVideo, chatWithVideo } from '../src/api/summarize.js'
import { MODE_PLATFORM } from '../src/lib/byok.js'
import { extractFn } from './helpers/source-slice.mjs'

const SENTINEL = 'sk-byok-central-ZZUNIQUEZZ'

/**
 * 两条**清单记录**的样本。
 *
 * 工单 #13 之后厂商清单归服务端（`api/models.js` 拉），byok.js 不再查表，
 * 所以这里必须把记录本身喂给 `chooseProvider(id, provider)`。
 * 清单本身的字段与端点归 `model-catalog.test.mjs` 守，这里只管
 * 「拿到一条记录之后 byok.js 怎么处理」。
 */
const OLLAMA = {
  id: 'ollama',
  label: 'Ollama（本地）',
  baseUrl: 'http://localhost:11434/v1',
  defaultModel: 'qwen2.5:7b',
  models: [],
  hint: '',
  isReal: 1,
}
const DEEPSEEK = {
  id: 'deepseek',
  label: 'DeepSeek',
  baseUrl: 'https://api.deepseek.com',
  defaultModel: 'deepseek-chat',
  models: [],
  hint: '',
  isReal: 1,
}

function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

function stripComments(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    // 协议里的 // 不是注释：只剥「前面不是冒号」的那种，否则
        // `https://x.com` 会被削成 `https:`，域名连同后面整行一起消失，
        // 扫源码的断言于是永远看不到它 —— 这条判据会变成死的。
    .map((l) => l.replace(/(^|[^:])\/\/.*$/, '$1'))
    .join('\n')
}

const appVue = stripComments(read('../src/App.vue'))
const headerVue = stripComments(read('../src/components/AppHeader.vue'))
const summaryVue = stripComments(read('../src/components/VideoSummary.vue'))
const dialogVue = stripComments(read('../src/components/ByokDialog.vue'))

/** 抽出一个具名函数（或 computed）的函数体，按大括号配平 */
function bodyOf(src, signature) {
  const at = src.indexOf(signature)
  if (at < 0) return null
  const open = src.indexOf('{', at)
  let depth = 0
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1
    else if (src[i] === '}') {
      depth -= 1
      if (depth === 0) return src.slice(open, i + 1)
    }
  }
  return null
}

/** 抽一次调用的实参表，按圆括号配平（不能用 [^)]*：实参里有嵌套回调） */
function callArgsOf(src, call) {
  const at = src.indexOf(call)
  if (at < 0) return null
  const open = src.indexOf('(', at)
  let depth = 0
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === '(') depth += 1
    else if (src[i] === ')') {
      depth -= 1
      if (depth === 0) return src.slice(open + 1, i)
    }
  }
  return null
}

const DONE_STREAM = ['event: done\n', 'data: [DONE]\n']

function fakeStream(chunks, { signal } = {}) {
  const enc = new TextEncoder()
  let controller
  const body = new ReadableStream({
    start(c) {
      controller = c
      for (const ch of chunks) c.enqueue(enc.encode(ch))
      c.close()
    },
  })
  if (signal) {
    if (signal.aborted) controller.error(new DOMException('Aborted', 'AbortError'))
    else signal.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')))
  }
  return new Response(body)
}

const noop = { onError: () => {}, onCancel: () => {}, onDone: () => {} }

/** 收集一次请求的请求体 */
function captureBody() {
  const box = { body: null }
  globalThis.fetch = async (url, opts) => {
    box.body = JSON.parse(opts.body)
    return fakeStream(DONE_STREAM)
  }
  return box
}

beforeEach(() => {
  const store = {}
  globalThis.localStorage = {
    getItem(k) { return store[k] ?? null },
    setItem(k, v) { store[k] = String(v) },
    removeItem(k) { delete store[k] },
    _dump: () => ({ ...store }),
  }
  clear()
})

describe('lib/byok 的出口纪律', () => {
  test('公开状态的结构里根本没有 key 这个键', () => {
    save({ apiKey: SENTINEL, provider: 'deepseek', baseUrl: 'https://api.deepseek.com', model: 'm' })
    const pub = getPublicState()
    assert.deepEqual(
      Object.keys(pub).sort(),
      ['baseUrl', 'hasKey', 'mode', 'model', 'provider'],
      `公开状态多出了键：${Object.keys(pub)}`,
    )
    assert.ok(!JSON.stringify(pub).includes(SENTINEL), 'key 从公开状态里漏了出来')
  })

  test('明文只从 getRequestCredential 一条出口出去', () => {
    save({ apiKey: SENTINEL, provider: 'deepseek', baseUrl: 'https://x.test/v1', model: 'm' })
    assert.equal(getRequestCredential().apiKey, SENTINEL)
  })

  test('没有 key 时请求层拿到 null，而不是一把空 key', () => {
    // 发一把空 key 过去会换来一句「凭据无效」——用户会以为自己配错了。
    assert.equal(getRequestCredential(), null)
  })

  test('订阅者拿到的也不含 key', () => {
    save({ apiKey: SENTINEL, provider: 'ollama', baseUrl: 'http://localhost:11434/v1', model: 'q' })
    let seen = null
    const off = subscribe((s) => { seen = s })
    updateConfig({ provider: 'moonshot', baseUrl: '', model: 'kimi' })
    off()
    assert.equal(seen.provider, 'moonshot')
    assert.ok(!JSON.stringify(seen).includes(SENTINEL))
  })

  test('取消订阅后不再被叫到', () => {
    let n = 0
    const off = subscribe(() => { n += 1 })
    updateConfig({ provider: 'openai', baseUrl: '', model: '' })
    const after = n
    off()
    updateConfig({ provider: 'platform', baseUrl: '', model: '' })
    assert.equal(n, after, '退订之后还在被通知')
  })

  test('chooseProvider 的返回值与 getPublicState 同形', () => {
    // 曾经的 bug：chooseProvider 返回的是厂商定义（id/label/hint），
    // 组件拿它当 state 存下，state.provider 就成了 undefined——
    // 而 provider 正是「用平台还是用自带」那个开关。它一丢，
    // 用户选好厂商填好 key 点保存，却静默地存成了平台模式。
    const returned = chooseProvider('ollama', OLLAMA)
    assert.deepEqual(
      Object.keys(returned).sort(),
      Object.keys(getPublicState()).sort(),
      'chooseProvider 返回的形状与公开状态对不上',
    )
    assert.equal(returned.provider, 'ollama')
    assert.equal(returned.baseUrl, 'http://localhost:11434/v1')
  })

  test('记录缺了也不静默换成别的厂商', () => {
    // 以前这里查不到就回落成 PROVIDERS[0]（平台）。现在没有表可查了：
    // 换成静默回落，用户以为选中了 ollama，存下去的却是平台模式——
    // 而 provider 正是「用谁的额度」那个开关。
    const s = chooseProvider('ollama')
    assert.equal(s.provider, 'ollama', '查不到记录就把用户的厂商选择改掉了')
    assert.equal(s.baseUrl, '', '该留空的没留空')
    assert.equal(s.model, '')
  })

  test('选厂商之后保存，provider 不会退回平台', () => {
    // 端到端一遍弹窗的调用序列：选厂商 → 填端点 → 存。
    chooseProvider('ollama', OLLAMA)
    const s = chooseProvider('ollama', OLLAMA)
    save({ apiKey: SENTINEL, provider: s.provider, baseUrl: s.baseUrl, model: s.model })
    const after = getPublicState()
    assert.equal(after.provider, 'ollama')
    assert.equal(after.mode, 'byok')
    assert.equal(after.hasKey, true)
  })
})

describe('一条状态，两个使用方式', () => {
  test('选了自带厂商但没填 key —— 这是两件事，不能混', () => {
    chooseProvider('deepseek', DEEPSEEK)
    const s = getPublicState()
    assert.equal(s.mode, 'byok', '用户明明选了自带，界面却说在用平台')
    assert.equal(s.hasKey, false)
    assert.equal(getRequestCredential(), null, '没 key 却仍然被当成有凭据')
  })

  test('填了 key 之后才真的可用', () => {
    chooseProvider('deepseek', DEEPSEEK)
    save({ apiKey: SENTINEL, provider: 'deepseek', baseUrl: 'https://api.deepseek.com', model: 'deepseek-chat' })
    assert.equal(getPublicState().hasKey, true)
    assert.equal(getRequestCredential().apiKey, SENTINEL)
  })

  test('切回平台保留已存的 key（否则每次切换都要重填）', () => {
    save({ apiKey: SENTINEL, provider: 'deepseek', baseUrl: 'https://api.deepseek.com', model: 'm' })
    usePlatform()
    assert.equal(getPublicState().mode, 'platform')
    assert.equal(getRequestCredential().apiKey, SENTINEL, '切一次就把用户的 key 扔了')
  })

  test('切回平台保留端点设置', () => {
    chooseProvider('ollama', OLLAMA)
    save({ apiKey: SENTINEL, provider: 'ollama', baseUrl: 'http://localhost:11434/v1', model: 'q' })
    usePlatform()
    const s = getPublicState()
    assert.equal(s.baseUrl, 'http://localhost:11434/v1')
    assert.equal(s.model, 'q')
  })

  test('改模型名不会顺手把 key 清掉', () => {
    save({ apiKey: SENTINEL, provider: 'deepseek', baseUrl: 'https://api.deepseek.com', model: 'm1' })
    updateConfig({ provider: 'deepseek', baseUrl: 'https://api.deepseek.com', model: 'm2' })
    assert.equal(getRequestCredential().apiKey, SENTINEL,
      '只改模型名就把 key 静默清空了——弹窗里「留空则沿用」是这么承诺的')
    assert.equal(getRequestCredential().model, 'm2')
  })

  test('清除是真的清除', () => {
    save({ apiKey: SENTINEL, provider: 'deepseek', baseUrl: 'https://x.test', model: 'm' })
    clear()
    assert.equal(getRequestCredential(), null)
    assert.equal(getPublicState().mode, 'platform')
  })

  test('切到平台模式会**保留**已存的 key（切回来不用重填一遍）', () => {
    save({ apiKey: SENTINEL, provider: 'deepseek', baseUrl: 'https://x.test', model: 'm' })
    usePlatform()
    assert.equal(getPublicState().mode, MODE_PLATFORM, '模式没切到平台')
    assert.equal(getRequestCredential().apiKey, SENTINEL,
      'usePlatform 把 key 一起清了 —— 切回来还得重填，是纯粹的折磨')
  })

  test('【已知缺口】切到平台模式后，请求**仍然**带着用户自己的 key', () => {
    // 这条断言的是**当前的真实行为**，不是期望行为。
    //
    // 它原来叫「切到平台模式时请求层拿不到凭据」，而断言写的是
    // `user_api_key === SENTINEL`（凭据照发）—— 名与断言相反，而且一直绿。
    // 查证结果：**不是测试写错，是实现从未实现用例名所描述的语义**：
    //   - getRequestCredential()（byok.js:141）只判 `state.apiKey`，不看 mode
    //   - api/summarize.js:157 只判 `options.credential` 真值，也不看 mode
    //   - VideoSummary.vue:592/:651 直接把凭据塞进去，全链路没有一处拿 mode 决定发不发
    // 而 UI 那侧 ByokDialog 会把 mode 渲染成「用平台额度」——
    // 用户选了 A、系统默默做了 B，界面上还看不出差别。
    //
    // 两种改法都说得通（凭据出口判 mode ／ UI 改口径），牵涉真实扣费，
    // 是产品语义决策，已开单跟踪，不在这里顺手改。这里只把现状钉死：
    // 谁动了这条链路，测试会响。
    save({ apiKey: SENTINEL, provider: 'deepseek', baseUrl: 'https://x.test', model: 'm' })
    usePlatform()
    const sent = []
    globalThis.fetch = async (u, o) => { sent.push(JSON.parse(o.body)); return fakeStream(DONE_STREAM) }
    return summarizeVideo('u', 'zh', noop, { credential: getRequestCredential() }).done.then(() => {
      assert.equal(sent[0].user_api_key, SENTINEL)
    })
  })
})

describe('存储与降级', () => {
  test('换一次页面后仍然读得回来', () => {
    save({ apiKey: SENTINEL, provider: 'moonshot', baseUrl: 'https://api.moonshot.cn/v1', model: 'kimi' })
    // 模块内 state 已经写好；这里直接验证两个键都落到了 localStorage，
    // 因为「读得回来」完全依赖它们。
    const dump = globalThis.localStorage._dump()
    assert.equal(dump.viddigest_user_api_key, SENTINEL)
    assert.deepEqual(JSON.parse(dump.viddigest_byok_config), {
      provider: 'moonshot', baseUrl: 'https://api.moonshot.cn/v1', model: 'kimi',
    })
  })

  test('沿用旧键名，老用户存过的 key 不会凭空消失', () => {
    // viddigest_user_api_key 是工单 #9 就在用的键。换名等于让所有
    // 已保存凭据的用户静默回到「没填」。
    assert.ok(stripComments(read('../src/lib/byok.js')).includes('viddigest_user_api_key'))
  })

  test('存不进去时退化成「只本次有效」而不是崩掉', () => {
    globalThis.localStorage = {
      getItem() { return null },
      setItem() { throw new Error('QuotaExceededError') },
      removeItem() { throw new Error('QuotaExceededError') },
    }
    assert.doesNotThrow(() => save({ apiKey: SENTINEL, provider: 'openai', baseUrl: '', model: 'm' }))
    assert.equal(getRequestCredential().apiKey, SENTINEL, '存不进去也不该让这次会话失效')
  })

  test('配置是坏 JSON 时当作没配过（真跑一次 read，不靠模块加载时机）', () => {
    // 原来的写法是「换掉 globalThis.localStorage，再断 getPublicState().mode 是字符串」，
    // 那是**恒真**的：byok.js:86 的 `let state = read()` 只在模块加载时执行一次，
    // 而本文件顶部是静态 import —— 模块早在 beforeEach 之前就加载完了。
    // 于是这里摆的坏 JSON 从来没被解析过，断言由 byok.js:129 的三元式恒保证。
    // 把整个坏 JSON 夹具删掉，这一条照样绿。
    //
    // 改成把 read() 抠出来真跑 —— 这是「解析不出时回落默认值」唯一能红的形状。
    const byokSrc = stripComments(read('../src/lib/byok.js'))
    const readFn = extractFn(byokSrc, 'read')
    // 键名从源码里抠，不在测试里重写一份：重写就等于允许它漂移。
    const [, keyStore] = /const KEY_STORE = '([^']+)'/.exec(byokSrc) ?? []
    const [, configStore] = /const CONFIG_STORE = '([^']+)'/.exec(byokSrc) ?? []
    assert.ok(keyStore && configStore, 'byok.js 里抠不出两个存储键常量')

    const runRead = new Function(
      'localStorage', 'KEY_STORE', 'CONFIG_STORE', 'normalizeBaseUrl', 'normalizeModel',
      `${readFn}; return read()`)
    const store = { [keyStore]: SENTINEL, [configStore]: '{坏掉的' }
    const ls = { getItem: (k) => store[k] ?? null }

    let out = null
    assert.doesNotThrow(() => {
      out = runRead(ls, keyStore, configStore, normalizeBaseUrl, normalizeModel)
    }, '坏 JSON 竟然抛了 —— 用户存一次坏数据，整个面板就白屏')
    assert.equal(out.apiKey, SENTINEL,
      '坏的是 config，key 是单独一个键，不该被连坐清掉')
    assert.deepEqual(out.config, { provider: 'platform', baseUrl: '', model: '' },
      '坏 JSON 没有回落到默认值 —— mode 会变成 undefined，UI 跟着崩')
  })
})

describe('端点归一与预检', () => {
  test('尾斜杠被去掉', () => {
    assert.equal(normalizeBaseUrl('https://api.example.com/v1/'), 'https://api.example.com/v1')
    assert.equal(normalizeBaseUrl('  https://api.example.com/v1///  '), 'https://api.example.com/v1')
  })

  test('http 被放行（自建推理服务只监听本机 http）', () => {
    assert.equal(validateBaseUrl('http://localhost:11434/v1'), null)
  })

  test('这些必须被拒', () => {
    for (const bad of [
      'file:///etc/passwd',
      'ftp://x.test/v1',
      'https://user:pass@api.example.com/v1',
      '不是一个地址',
    ]) {
      assert.ok(validateBaseUrl(bad), `${bad} 竟然通过了前端预检`)
    }
  })

  test('「空主机」由服务端兜住，不是前端能拒的形状', () => {
    // WHATWG 的 URL 解析器会把 https:///v1 规范化成 https://v1/，
    // 前端拿到的已经是规范化之后的结果，看不到「netloc 为空」这个形状。
    // 所以这条**不**算前端失职——真正承重的是服务端那道校验。
    // 这里记下来，是为了别让下一个人以为前端预检是完整的。
    assert.equal(validateBaseUrl('https:///v1'), null)
    // 真正的判据在服务端：它拿的是 urlsplit，netloc 为空即拒绝。
    assert.ok(
      read('../src/lib/byok.js').includes('端点地址只支持 http 或 https'),
      '前端与后端的口径已经漂移',
    )
  })

  test('空端点不报错（留空 = 用服务端默认）', () => {
    assert.equal(validateBaseUrl(''), null)
    assert.equal(validateBaseUrl(null), null)
  })
})

describe('两条请求路径都带上同一份凭据', () => {
  test('总结带上 key / base_url / model', async () => {
    const box = captureBody()
    const cred = { apiKey: SENTINEL, baseUrl: 'https://api.deepseek.com', model: 'deepseek-chat' }
    await summarizeVideo('u', 'zh', noop, { credential: cred }).done
    assert.equal(box.body.user_api_key, SENTINEL)
    assert.equal(box.body.base_url, 'https://api.deepseek.com')
    assert.equal(box.body.model, 'deepseek-chat')
  })

  test('追问带上同一份', async () => {
    const box = captureBody()
    const cred = { apiKey: SENTINEL, baseUrl: 'https://api.deepseek.com', model: 'deepseek-chat' }
    await chatWithVideo('u', 'q', noop, { credential: cred }).done
    assert.equal(box.body.user_api_key, SENTINEL)
    assert.equal(box.body.base_url, 'https://api.deepseek.com')
  })

  test('没凭据时一条都不带（不是带空串）', async () => {
    const box = captureBody()
    await summarizeVideo('u', 'zh', noop, { credential: getRequestCredential() }).done
    assert.ok(!('user_api_key' in box.body), JSON.stringify(box.body))
    assert.ok(!('base_url' in box.body))
  })

  test('端点为空时不下发 base_url 字段', async () => {
    const box = captureBody()
    const cred = { apiKey: SENTINEL, baseUrl: '', model: '' }
    await summarizeVideo('u', 'zh', noop, { credential: cred }).done
    assert.equal(box.body.user_api_key, SENTINEL)
    assert.ok(!('base_url' in box.body), '空端点被当成了「清空服务端配置」')
  })

  test('请求体不展开整个 options（防止将来顺手把别的字段带上车）', () => {
    const apiJs = stripComments(read('../src/api/summarize.js'))
    assert.ok(!/streamSse\('\/api\/summarize',\s*\{\s*\.\.\.options/.test(apiJs))
    assert.ok(!/streamSse\('\/api\/chat',\s*\{\s*\.\.\.options/.test(apiJs))
  })
})

describe('页面接线', () => {
  test('额度面板里有 API Key 入口', () => {
    assert.ok(headerVue.includes("@click=\"$emit('open-byok')\""), '额度面板没有 key 入口')
    assert.ok(headerVue.includes('byokLabel'), '入口按钮没有说明当前用的是谁的额度')
  })

  test('入口的两种状态分别有说法', () => {
    const body = bodyOf(headerVue, 'const byokLabel = computed(')
    assert.ok(body.includes("mode !== 'byok'"), '没区分「用平台」与「用自带」')
  })

  test('「未填 Key」与「已就绪」是两枚不同的徽标', () => {
    assert.ok(headerVue.includes("byok.hasKey ? '不扣额度' : '未填 Key'"),
      '选了自带但没填 key 时，徽标与实际行为对不上')
  })

  test('弹窗里的输入框保存后立刻清空', () => {
    const body = bodyOf(dialogVue, 'function saveAndClose()')
    assert.ok(body, '找不到 saveAndClose')
    assert.ok(/apiKeyInput\.value = ''/.test(body),
      '明文留在输入框里：会进浏览器自动填充，也留在「检查元素」里')
  })

  test('留空走 updateConfig 而不是 save（否则「沿用」变成「清除」）', () => {
    const body = bodyOf(dialogVue, 'function saveAndClose()')
    assert.ok(body.includes('updateConfig('), '留空时没有走保留 key 的那条路')
    assert.ok(!/apiKey:\s*typed\s*\|\|/.test(body), '用 || 把空值当成了清除')
  })

  test('弹窗在选「平台 Key」时不出 key 与端点两栏', () => {
    // 平台模式下那两栏没有意义，却会让用户以为自己漏看了什么
    assert.ok(/state\.provider !== 'platform'/.test(dialogVue))
  })

  test('VideoSummary 不再自己存一份凭据', () => {
    assert.ok(!/localStorage\.getItem\(USER_API_KEY_STORE\)/.test(summaryVue),
      'VideoSummary 仍在自己读 localStorage，等于第二个真相源')
    assert.ok(!summaryVue.includes('viddigest_user_api_key'),
      'VideoSummary 仍硬编码了存储键名')
  })

  test('解析与追问都从同一个出口取凭据', () => {
    assert.ok(summaryVue.includes('getRequestCredential'), '没有引入共享状态')
    const start = summaryVue.slice(summaryVue.indexOf('function startSummarize('))
    const args = callArgsOf(start, 'summarizeVideo')
    assert.ok(/credential:\s*getRequestCredential\(\)/.test(args || ''),
      '解析路径没有带上凭据：用户在顶栏配好了，解析却仍扣额度')
    const chat = summaryVue.slice(summaryVue.indexOf('async function handleChat('))
    assert.ok(chat.includes('getRequestCredential()'), '追问路径没有带上凭据')
  })

  test('旧面板换成了状态提示，并说明当前用的是谁', () => {
    assert.ok(!summaryVue.includes('byokPanelOpen'), '旧的可折叠面板还在')
    assert.ok(summaryVue.includes('props.byok?.mode'), '卡片上没有说明当前用的是谁的额度')
    assert.ok(summaryVue.includes('open-byok'), '「还没填」时没有给出跳去填写的入口')
  })

  test('App 持有状态并订阅变化，弹窗挂在顶层', () => {
    assert.ok(appVue.includes('getPublicState()'), 'App 没有初始化 BYOK 状态')
    assert.ok(appVue.includes('subscribe as subscribeByok'), 'App 没有订阅变化')
    assert.ok(appVue.includes('<ByokDialog'), '弹窗没有挂载')
    assert.ok(appVue.includes(':byok="byok"'), '状态没有透传给组件')
  })

  test('订阅建立在挂载时，不是卸载时', () => {
    // 曾经的 bug：写成 onUnmounted(() => subscribe(fn))。那行读起来像
    // 「清理时退订」，实际是「卸载时才订阅」——顶栏于是永远停在
    // 「使用平台 Key」，而弹窗里明明已经选好了厂商。
    // 光断言「import 了 subscribe」抓不到，形状才是关键。
    assert.ok(
      /const stopByokSubscription = subscribeByok\(/.test(appVue),
      '订阅没有在 setup 期建立',
    )
    assert.ok(
      /onUnmounted\(stopByokSubscription\)/.test(appVue),
      '订阅没有在卸载时退订',
    )
    assert.ok(
      !/onUnmounted\(\s*\(\)\s*=>\s*subscribe/.test(appVue),
      '订阅被写进了 onUnmounted 的回调里——那是在卸载时才订阅',
    )
  })
})
