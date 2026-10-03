/**
 * 社区标签契约（工单 #5 的前端部分）。运行：node --test tests/
 *
 * 背景：一次解析同时产出总结 / 思维导图 / 标签。标签走新的 SSE 事件 `tags`，
 * 负载是 JSON 字符串数组。后端已保证：值在固定词表内、长度 ≤ 3、数组非空、
 * 已去重且按词表声明顺序。所以前端的职责只有「原样显示」——
 * 任何过滤 / 排序 / 截断 / 去重都是前端自己引入的偏差，测出来。
 *
 * 测法与本仓既有两条一致（见 sse.test.mjs / quota.test.mjs）：
 * 路由行为用假流真跑一遍（行为断言）；.vue 部分只做源码接线检查——
 * 渲染结果验证需要挂载环境，超出本仓「node --test 零额外依赖」的约定。
 */
import { test, describe, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { summarizeVideo } from '../src/api/summarize.js'

/** 假流：行为对齐真实 fetch + ReadableStream（与 sse.test.mjs 同构） */
function fakeStream(chunks) {
  const enc = new TextEncoder()
  const body = new ReadableStream({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch))
      c.close()
    },
  })
  return new Response(body)
}

beforeEach(() => {
  globalThis.localStorage = {
    _v: { auth_token: 'test-token' },
    getItem(k) { return this._v[k] ?? null },
    setItem(k, v) { this._v[k] = String(v) },
    removeItem(k) { delete this._v[k] },
  }
})

/** 收集一次流里每个回调的触发情况；路由摘错会表现为某条事件谁都没收到 */
function recorder() {
  const log = []
  return {
    log,
    cb: {
      onSubtitle: (d) => log.push(['subtitle', d]),
      onSummary: (t) => log.push(['summary', t]),
      onMindmap: (d) => log.push(['mindmap', d.markdown]),
      onTags: (d) => log.push(['tags', d]),
      onQuota: (d) => log.push(['quota', d]),
      onAnswer: (t) => log.push(['answer', t]),
      onError: (e) => log.push(['error', e.message]),
      onCancel: () => log.push(['cancel', null]),
      onDone: () => log.push(['done', null]),
    },
  }
}

const TAGS = ['编程', '人工智能']

/** 六个既有事件 + tags + 收尾，用于回归「加了一条没动别的」 */
const FULL_STREAM = [
  'event: subtitle\n',
  'data: {"full_text":"hello","has_subtitle":true}\n',
  'event: quota\n',
  'data: {"remaining":2,"limit":3,"unlimited":false}\n',
  'event: summary\n',
  'data: "tok1"\n',
  'event: summary\n',
  'data: "tok2"\n',
  'event: mindmap\n',
  'data: {"markdown":"# x"}\n',
  'event: tags\n',
  'data: ["编程","人工智能"]\n',
  'event: done\n',
  'data: [DONE]\n',
]


describe('summarizeVideo · tags 事件路由', () => {
  test('tags 事件路由到 onTags 回调', async () => {
    globalThis.fetch = async () => fakeStream([
      'event: tags\n', `data: ${JSON.stringify(TAGS)}\n`,
      'event: done\n', 'data: [DONE]\n',
    ])
    const r = recorder()
    await summarizeVideo('u', 'zh', r.cb).done
    assert.deepEqual(r.log[0], ['tags', TAGS], 'tags 必须落到 onTags，而不是被丢掉')
  })

  test('事件名与 data 落在不同 chunk 时仍认得出 tags', async () => {
    // SSE 的 event:/data: 常被拆到不同网络分片；认错就会把标签当成无名负载丢掉
    const enc = new TextEncoder()
    globalThis.fetch = async () => new Response(new ReadableStream({
      start(c) {
        c.enqueue(enc.encode('event: ta'))
        c.enqueue(enc.encode('gs\n'))
        c.enqueue(enc.encode(`data: ${JSON.stringify(TAGS)}\n`))
        c.enqueue(enc.encode('event: done\ndata: [DONE]\n'))
        c.close()
      },
    }))
    const r = recorder()
    await summarizeVideo('u', 'zh', r.cb).done
    assert.deepEqual(r.log[0], ['tags', TAGS])
  })

  test('标签数组原样到达：数量、顺序、内容一字不改', async () => {
    // 故意用「非词表声明顺序」的载荷：前端若自行排序 / 去重 / 截断，这里就会红
    const payload = ['其他', '编程', '人工智能']
    globalThis.fetch = async () => fakeStream([
      'event: tags\n', `data: ${JSON.stringify(payload)}\n`,
      'event: done\n', 'data: [DONE]\n',
    ])
    const r = recorder()
    await summarizeVideo('u', 'zh', r.cb).done
    const tagEvents = r.log.filter((e) => e[0] === 'tags')
    assert.equal(tagEvents.length, 1, 'tags 事件应只触发一次')
    assert.deepEqual(tagEvents[0][1], payload, '前端不得对后端已规范化的标签二次加工')
    assert.ok(tagEvents[0][1].every((t) => typeof t === 'string'), '标签应保持字符串形态')
  })

  test('回归：六个既有事件与 tags 混流时各自落到正确回调', async () => {
    globalThis.fetch = async () => fakeStream(FULL_STREAM)
    const r = recorder()
    await summarizeVideo('u', 'zh', r.cb).done
    assert.deepEqual(
      r.log.map((e) => e[0]),
      ['subtitle', 'quota', 'summary', 'summary', 'mindmap', 'tags', 'done'],
      '事件顺序或回调归属被改动了',
    )
    assert.deepEqual(r.log[1][1], { remaining: 2, limit: 3, unlimited: false })
    assert.deepEqual(r.log[2][1], 'tok1')
    assert.deepEqual(r.log[4][1], '# x')
    assert.deepEqual(r.log[5][1], TAGS)
  })

  test('回归：error 事件仍落到 onError，而不是被当成 tags 吞掉', async () => {
    globalThis.fetch = async () => fakeStream([
      'event: error\n', 'data: {"message":"boom"}\n',
      'event: done\n', 'data: [DONE]\n',
    ])
    const r = recorder()
    await summarizeVideo('u', 'zh', r.cb).done
    assert.deepEqual(r.log[0], ['error', 'boom'])
    assert.equal(r.log.filter((e) => e[0] === 'tags').length, 0)
  })
})


/**
 * 路由表静态检查：工单 #5 只允许「加一条」。
 * 行为测试能覆盖「收到就转发」，但覆盖不了「有人顺手改了别人的映射名」。
 */
describe('路由表', () => {
  // 归一化行尾符：本仓库是 CRLF，直接按 \n 切块会切空。
  const api = readFileSync(
    new URL('../src/api/summarize.js', import.meta.url),
    'utf8',
  ).replace(/\r\n/g, '\n')

  /** 读出 `const X = { k: 'v', ... }` 的键值对 */
  function readRoutes(name) {
    const start = api.indexOf(`const ${name} = {`)
    assert.notEqual(start, -1, `找不到 ${name}`)
    const end = api.indexOf('\n}', start)
    assert.ok(end > start, `${name} 没读到收尾大括号`)
    const body = api.slice(api.indexOf('{', start) + 1, end)
    return Object.fromEntries(
      body.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
        // 去掉行尾逗号与引号，否则值会读成 "onAnswer',"
        const [k, v] = l.split(':').map((s) => s.trim().replace(/,$/, '').replace(/^'|'$/g, ''))
        return [k, v]
      }),
    )
  }

  test('SUMMARY_ROUTES：六个既有映射未改，只多出 tags 与 ownership 两条', () => {
    // ownership 是 ADR 0007 加的：复用回放会先发它，告诉前端这份
    // 是不是当前用户自己解析的（「重新解析」按钮能不能点）。
    assert.deepEqual(readRoutes('SUMMARY_ROUTES'), {
      ownership: 'onOwnership',
      subtitle: 'onSubtitle',
      summary: 'onSummary',
      mindmap: 'onMindmap',
      tags: 'onTags',
      quota: 'onQuota',
      error: 'onError',
    })
  })

  test('CHAT_ROUTES 不受本次改动影响', () => {
    assert.deepEqual(readRoutes('CHAT_ROUTES'), {
      answer: 'onAnswer',
      quota: 'onQuota',
      error: 'onError',
    })
  })
})


/**
 * 组件接线守卫（静态检查，不是渲染测试）。
 *
 * 只做源码层面的存在性与形状检查——它证明不了渲染结果，
 * 但能挡住最常见的接线回归：把 onTags 摘掉、模板不渲染标签、
 * 或在回调里偷偷过滤 / 排序 / 截断后端已规范化的数组。
 */
describe('VideoSummary 标签渲染接线', () => {
  const source = readFileSync(
    new URL('../src/components/VideoSummary.vue', import.meta.url),
    'utf8',
  ).replace(/\r\n/g, '\n')

  /** 标签条模板块：从容器标签本身到 Tab 内容区之前 */
  const divAt = source.indexOf('<div v-if="videoTags.length"')
  const blockEnd = source.indexOf('<!-- Tab 内容 -->', divAt)
  const block = source.slice(divAt, blockEnd)

  test('模板渲染出标签：v-for 遍历 + 插值 + 像素字体', () => {
    assert.notEqual(divAt, -1, '模板没有标签渲染出口（videoTags 从未被容器守卫消费）')
    assert.match(block, /<div v-if="videoTags\.length"/, '空数组守卫应挂在标签条容器上')
    assert.match(block, /<span v-for="tag in videoTags"/, '缺少 v-for="tag in videoTags"')
    assert.match(block, /#\{\{ tag \}\}/, '标签文本没有被渲染出来')
    assert.match(block, /font-pixel/, '标签没有沿用本仓像素等宽字体')
  })

  test('空数组不渲染出空容器：守卫对 [] 为假、有标签为真', () => {
    const expr = block.match(/v-if="([^"]*videoTags[^"]*)"/)
    assert.ok(expr, '容器上没有可执行的 v-if 守卫')
    const run = new Function('videoTags', `return (${expr[1]})`)
    assert.ok(!run([]), '空数组时容器必须消失，不能留一条空壳行')
    assert.ok(run(TAGS), '有标签时容器必须出现')
    assert.doesNotMatch(block, /v-if="[^"]*"[^>]*\sv-for/, 'v-if 不应与 v-for 同元素（Vue 里 v-for 拿不到 v-if 的 tag）')
  })

  test('标签条渲染在 Tab 内容区之外（常驻行，不随 Tab 切换消失）', () => {
    assert.ok(divAt > -1, '找不到标签条')
    assert.ok(blockEnd > divAt, '标签条应渲染在「Tab 内容」区之前，即挂在面板常驻行上')
  })

  test('onTags 原样赋值：没有过滤 / 排序 / 截断 / 原地修改', () => {
    const assigned = source.match(/videoTags\.value\s*=\s*[^\n]+/g) ?? []
    assert.deepEqual(
      assigned.sort(),
      ['videoTags.value = []', 'videoTags.value = []', 'videoTags.value = data'],
      `videoTags 只允许被整体赋值，出现意料之外的写法：${JSON.stringify(assigned)}`,
    )
    assert.doesNotMatch(
      source, /videoTags\.value\.(push|splice|sort|reverse|filter|map|slice|flat)\b/,
      '不得对后端已规范化的标签数组做二次加工',
    )
  })

  test('换视频与重新解析都会清空标签，不残留上一个视频的标签', () => {
    const wAt = source.indexOf('watch(() => props.videoUrl')
    const watcher = source.slice(wAt, source.indexOf('\n})', wAt))
    const sAt = source.indexOf('function startSummarize(')
    assert.notEqual(sAt, -1, '没找到 startSummarize')
    const startFn = source.slice(sAt, source.indexOf('\n}\n', sAt) + 2)
    assert.match(watcher, /videoTags\.value = \[\]/, '换视频未清空标签')
    assert.match(startFn, /videoTags\.value = \[\]/, '重新解析未清空标签')
    assert.match(startFn, /onTags:\s*\(data\)\s*=>\s*\{\s*videoTags\.value = data\s*\}/,
      'startSummarize 的总结流没有接上 onTags')
  })
})
