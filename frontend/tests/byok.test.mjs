/**
 * 自带凭据（BYOK，工单 #9）的前端接线。运行：node --test tests/
 *
 * 测法与本仓既有的 community-page.test.mjs 一致：.js 里的行为用假 fetch 真跑一遍，
 * .vue 部分只做源码接线断言——渲染结果验证需要挂载环境，超出本仓
 * 「node --test 零额外依赖」的约定。
 *
 * 真正要守住的是三件事：
 * 1. 凭据**只**出现在 /api/chat 的请求体里——不进 URL、不进请求头、
 *    不进任何别的请求。断言方式是「把所有抓到的请求摊开，逐处搜那个哨兵」。
 * 2. 凭据提交后不回显：它不绑在任何会渲染出来的节点上，输入框提交即清空。
 * 3. 「不消耗额度」这条提示依赖 quota 事件的 byok 标记；少了它，
 *    前端会把用户看到的余额抹成 undefined。
 */
import { test, describe, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { summarizeVideo, chatWithVideo, fetchQuota } from '../src/api/summarize.js'

/** 只在这个文件里出现、任何服务商都不会签发的哨兵 */
const SENTINEL = 'sk-agent-bytok-9f3c1a7e-ZZUNIQUEZZ'
// 别叫 URL：会盖掉全局的 URL 构造函数，read() 里 new URL(...) 直接炸
const VIDEO_URL = 'https://example.com/v/byok'

/** 归一化行尾符 */
function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

/** 去掉注释：断言要看代码，不是解释代码的散文 */
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((l) => l.replace(/\/\/.*$/, ''))
    .join('\n')
}

const summaryVue = read('../src/components/VideoSummary.vue')
const template = stripComments(summaryVue.split('</template>')[0])
const script = stripComments(summaryVue.split('<script setup>')[1] ?? '')

/** 造一个能响应 AbortSignal 的假流 */
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

const CHAT_SSE = [
  'event: quota\ndata: {"byok": true, "consumed": false}\n\n',
  'event: answer\ndata: "答案片段"\n\n',
  'event: done\ndata: [DONE]\n\n',
]
const SUMMARY_SSE = ['event: done\ndata: [DONE]\n\n']

let requests
let responseTexts

beforeEach(() => {
  requests = []
  responseTexts = []
  globalThis.localStorage = {
    _v: { auth_token: 'test-token' },
    getItem(k) { return this._v[k] ?? null },
    setItem(k, v) { this._v[k] = String(v) },
    removeItem(k) { delete this._v[k] },
  }
  globalThis.fetch = async (path, init = {}) => {
    requests.push({ path, init })
    const chunks = path === '/api/chat' ? CHAT_SSE : SUMMARY_SSE
    // 记下**上线字节本身**。不能改成 res.text()：那会读完流，
    // 下面的 SSE 解析器就再也读不到了。
    responseTexts.push(chunks.join(''))
    return fakeStream(chunks, { signal: init.signal })
  }
})

/** 把每一次请求摊平成可搜索的文本：URL、请求头、请求体 */
function searchable(entries) {
  return entries.map((r) => `${r.path}\n${JSON.stringify(r.init.headers ?? {})}\n${r.init.body ?? ''}`).join('\n')
}

function quietCallbacks() {
  return { onAnswer() {}, onQuota() {}, onError() {}, onDone() {} }
}

describe('chatWithVideo：凭据只进 /api/chat', () => {
  test('带上 userApiKey 时凭据出现在请求体里', async () => {
    await chatWithVideo(VIDEO_URL, '问题', quietCallbacks(), { userApiKey: SENTINEL }).done

    assert.equal(requests.length, 1)
    const body = JSON.parse(requests[0].init.body)
    assert.equal(body.user_api_key, SENTINEL)
    assert.equal(body.url, VIDEO_URL)
    assert.equal(body.question, '问题')
  })

  test('不带 userApiKey 时请求体里连这个键都没有', async () => {
    await chatWithVideo(VIDEO_URL, '问题', quietCallbacks()).done

    const body = JSON.parse(requests[0].init.body)
    assert.ok(!('user_api_key' in body), '没填凭据也发了一个空字段过去')
  })

  test('空串与 undefined 都不发', async () => {
    await chatWithVideo(VIDEO_URL, '问题', quietCallbacks(), { userApiKey: '' }).done

    const body = JSON.parse(requests[0].init.body)
    assert.ok(!('user_api_key' in body))
  })

  test('凭据不进 URL、不进请求头', async () => {
    await chatWithVideo(VIDEO_URL, '问题', quietCallbacks(), { userApiKey: SENTINEL }).done

    const r = requests[0]
    assert.ok(!r.path.includes(SENTINEL), `凭据进了 URL：${r.path}`)
    assert.ok(!JSON.stringify(r.init.headers ?? {}).includes(SENTINEL), '凭据进了请求头')
  })

  test('全站只有 /api/chat 这一次请求带着它，且它不出现在任何响应里', async () => {
    // 一次会话里会发出的三类请求：解析、追问、额度查询
    await summarizeVideo(VIDEO_URL, 'zh', quietCallbacks()).done
    await chatWithVideo(VIDEO_URL, '问题', quietCallbacks(), { userApiKey: SENTINEL }).done
    await fetchQuota().catch(() => {})

    const hit = requests.filter((r) => String(r.init.body ?? '').includes(SENTINEL))
    assert.equal(hit.length, 1, '凭据出现在了一次以上的请求里')
    assert.equal(hit[0].path, '/api/chat')

    for (const body of responseTexts) {
      assert.ok(!body.includes(SENTINEL), '凭据出现在了响应里')
    }
  })
})

describe('VideoSummary.vue 接线', () => {
  test('提交时把凭据交给 chatWithVideo', () => {
    // 只断言「同一个调用里传了进去」，不绑回调块有多长——
    // 写成 chatWithVideo(...) … { userApiKey } 的距离上限，改个回调就假红。
    const call = script.slice(script.indexOf('const stream = chatWithVideo'))
    const head = call.slice(0, call.indexOf('chatStream = stream'))
    assert.match(head, /chatWithVideo\(\s*props\.videoUrl,\s*question,[\s\S]*?\{\s*userApiKey\s*\}\s*\)/)
  })

  test('输入框提交后立刻清空', () => {
    assert.match(script, /const userApiKey = typedKey \|\| savedUserApiKey\.value/)
    assert.match(script, /apiKeyInput\.value = ''/)
  })

  test('输入框是 password，不明文显示', () => {
    assert.match(template, /<input[^>]*v-model="apiKeyInput"[^>]*type="password"/)
  })

  test('凭据不绑在任何会渲染出来的节点上', () => {
    // {{ }} 插值一旦提到凭据，它就出现在 DOM 里、也进得了截图与转发
    const interpolations = template.match(/\{\{[^}]*\}\}/g) ?? []
    const leaking = interpolations.filter((s) => /userApiKey|savedUserApiKey|apiKeyInput/.test(s))
    assert.deepEqual(leaking, [], `凭据被渲染出来了：${leaking.join(' ')}`)
  })

  test('只写 localStorage 一处，且键名唯一', () => {
    const stores = script.match(/localStorage\.(setItem|removeItem|getItem)\([^)]*/g) ?? []
    const keyStores = stores.filter((s) => s.includes('USER_API_KEY_STORE'))
    assert.equal(stores.length, keyStores.length, `凭据用别的键/方式存了：${stores.join(' | ')}`)
    assert.ok(stores.length >= 2, '保存与清除都要落到 localStorage 上')
  })

  test('界面写明了隐私承诺与「不消耗额度」', () => {
    assert.match(template, /清除浏览器数据后无法恢复/)
    assert.match(template, /不消耗平台额度/)
    assert.match(template, /不写日志/)
  })

  test('byok 额度事件不清空用户看得到的余额', () => {
    // 没有这条，早期重构会把「没有数字」当成「数字是 undefined」整个替换掉
    assert.match(script, /if \(d\?\.byok\) \{[\s\S]{0,200}?byokNotice\.value = true[\s\S]{0,80}?return/)
  })

  test('由服务端决定要不要提示，不靠客户端自称', () => {
    assert.match(script, /byokNotice\.value = false[\s\S]{0,400}?const stream = chatWithVideo/)
  })
})
