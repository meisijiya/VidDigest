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
import { sliceBetween } from './helpers/source-slice.mjs'

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
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    // 协议里的 // 不是注释：只剥「前面不是冒号」的那种，否则
        // `https://x.com` 会被削成 `https:`，域名连同后面整行一起消失，
        // 扫源码的断言于是永远看不到它 —— 这条判据会变成死的。
    .map((l) => l.replace(/(^|[^:])\/\/.*$/, '$1'))
    .join('\n')
}

const summaryVue = read('../src/components/VideoSummary.vue')
const template = stripComments(summaryVue.split('</template>')[0])
const summaryTemplate = template
const script = stripComments(summaryVue.split('<script setup>')[1] ?? '')

// 集中管理之后，凭据的输入处搬到了弹窗，存储搬到了 lib/byok。
// 下面三条是它们各自被断言的形态。
const dialogVue = read('../src/components/ByokDialog.vue')
const dialogTemplate = stripComments(dialogVue.split('</template>')[0])
const dialogScript = stripComments(dialogVue.split('<script setup>')[1] ?? '')
const byokLib = read('../src/lib/byok.js')

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

describe('BYOK 前端接线（集中管理后）', () => {
  // 凭据的归属地从「VideoSummary 里的三个 ref」搬到了
  // lib/byok.js（真值）+ ByokDialog.vue（唯一输入处）。
  // 下面每条断言都跟着搬到新的归属地，而不是被删掉——
  // 它们守的承诺没变，只是承载它的文件换了。

  test('提交时把凭据交给 chatWithVideo', () => {
    // 只断言「同一个调用里传了进去」，不绑回调块有多长——
    // 写成 chatWithVideo(...) … 的距离上限，改个回调就假红。
    // 原写法是 `script.slice(indexOf(x))` 再 `.slice(0, indexOf(y))`：
    // 任一 indexOf 给出 -1，另一段就一路切到文件末尾，于是脚本里**任何位置**
    // 的 chatWithVideo 调用都能让这条通过。
    const head = sliceBetween(script, 'const stream = chatWithVideo', 'chatStream = stream',
      'VideoSummary 的 chatWithVideo 调用')
    assert.match(head, /chatWithVideo\(\s*props\.videoUrl,\s*question,[\s\S]*?\{\s*credential\s*\}\s*\)/)
    // 真值必须是从共享出口取的，不是组件自己存的
    assert.match(script, /const credential = getRequestCredential\(\)/)
  })

  test('输入框提交后立刻清空', () => {
    // 现在输入框住在 ByokDialog：明文活到点「保存」为止。
    const body = sliceBetween(dialogScript, 'function saveAndClose()', 'function usePlatformMode',
      'ByokDialog 的 saveAndClose')
    assert.match(body, /apiKeyInput\.value = ''/)
  })

  test('输入框是 password，不明文显示', () => {
    assert.match(dialogTemplate, /<input[^>]*v-model="apiKeyInput"[^>]*type="password"/)
  })

  test('凭据不绑在任何会渲染出来的节点上', () => {
    // {{ }} 插值一旦提到凭据，它就出现在 DOM 里、也进得了截图与转发。
    // 两个文件都要查：凭据的输入处在弹窗，但状态提示在卡片上。
    for (const [where, src] of [['VideoSummary', template], ['ByokDialog', dialogTemplate]]) {
      const interpolations = src.match(/\{\{[^}]*\}\}/g) ?? []
      const leaking = interpolations.filter(
        (s) => /userApiKey|savedUserApiKey|apiKeyInput|getRequestCredential/.test(s),
      )
      assert.deepEqual(leaking, [], `${where} 把凭据渲染出来了：${leaking.join(' ')}`)
    }
  })

  test('localStorage 只在 lib/byok 里被写，且键名唯一', () => {
    // VideoSummary 不再碰 localStorage：它碰一下就等于多了一个真相源，
    // 而两份真相迟早会不一致——用户填的 key 解析不认，追问认。
    const stores = byokLib.match(/localStorage\.(setItem|removeItem|getItem)\([^)]*/g) ?? []
    assert.ok(stores.length >= 3, `保存 / 清除 / 读回都要落到 localStorage 上：${stores.join(' | ')}`)
    // 键名只以常量的形式出现。调用点直接写字面量的话，「只有两个键」
    // 就成了一句愿望——下一次有人手滑打错一个字，没人看得出来。
    const defined = byokLib.match(/^const (KEY_STORE|CONFIG_STORE) = '([^']+)'/gm) ?? []
    assert.deepEqual(
      defined.map((d) => d.split("'")[1]).sort(),
      ['viddigest_byok_config', 'viddigest_user_api_key'],
      `键名不止一套：${defined.join(' | ')}`,
    )
    const literals = stores.filter((s) => /'viddigest/.test(s))
    assert.deepEqual(literals, [], `调用点绕过了常量直接写字面量：${literals.join(' | ')}`)
    assert.ok(!/localStorage\./.test(script), 'VideoSummary 仍在直接读写 localStorage')
  })

  test('隐私承诺写在填 Key 的弹窗里', () => {
    // 刻意**不**把两个模板拼起来断言：`dialogTemplate + summaryTemplate` 是析取，
    // 任一文件含该串即通过。实测 VideoSummary.vue 压根没有「不写日志」——原来那条
    // 断言完全由 ByokDialog.vue 贡献，VideoSummary 那半边是虚的，
    // 看着像两处都被守着，实际其中一处从来没被守过。
    assert.match(dialogTemplate, /不写日志/)
    assert.match(dialogTemplate, /不消耗平台额度/)
  })

  test('常驻额度行写明「不消耗平台额度」', () => {
    // VideoSummary 的 byok 提示挂在**常驻**额度行上而不是某个 Tab 里：
    // 用户正在看总结时也要知道这份内容用的是谁的额度。
    assert.match(summaryTemplate, /不消耗平台额度/)
  })

  test('byok 额度事件不清空用户看得到的余额', () => {
    // 没有这条，早期重构会把「没有数字」当成「数字是 undefined」整个替换掉
    assert.match(script, /if \(d\?\.byok\) \{[\s\S]{0,200}?byokNotice\.value = true[\s\S]{0,80}?return/)
  })

  test('由服务端决定要不要提示，不靠客户端自称', () => {
    assert.match(script, /byokNotice\.value = false[\s\S]{0,400}?const stream = chatWithVideo/)
  })
})
