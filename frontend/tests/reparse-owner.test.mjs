/**
 * 「重新解析」真的重跑（ADR 0007）。
 *
 * 原来的按钮调的是 /api/parse —— 那条路**不调模型**，只取视频元信息。
 * 于是点完转个圈，屏幕上还是同一份总结。整套前端测试对这一点全绿：
 * 它们从来没验过「点了之后总结有没有变」。
 *
 * 这里分两层：
 *   1. summarizeVideo 的请求体 —— 真调用，断言 overwrite 真的发出去了。
 *   2. .vue 接线 —— 沿用本仓约定走源码断言（渲染验证需要挂载环境）。
 *      只挑「删掉它会静默变成另一种行为」的三处，那种地方源码断言比没有强。
 *
 * 每条都问过：把这个被测的东西整个删掉，它还会绿吗？
 */
import { test, describe, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { summarizeVideo } from '../src/api/summarize.js'

function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

/**
 * 去掉注释：断言要看代码，不是解释代码的散文。
 *
 * HTML 注释必须一起删——本文件有一条断言就是「模板里没有裸的
 * @click="startSummarize"」，而说明为什么不能裸写的那段注释里
 * 恰好就有这个字样。不删的话，断言会一直在和自己的注释较劲。
 */
function stripComments(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((l) => l.replace(/\/\/.*$/, ''))
    .join('\n')
}

const appVue = stripComments(read('../src/App.vue'))
const summaryVue = stripComments(read('../src/components/VideoSummary.vue'))
const apiJs = stripComments(read('../src/api/summarize.js'))

/** 抽出一个具名函数（或 computed）的函数体，按大括号配平 */
function bodyOf(src, signature) {
  const at = src.indexOf(signature)
  if (at < 0) return null
  const open = src.indexOf('{', at)
  if (open < 0) return null
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

/**
 * 抽出一次调用的实参表，按圆括号配平。
 * 不能用 `[^)]*`：实参里有嵌套的回调（emit('ownership', data)），
 * 非贪婪的正则会在那里提前收尾，然后报一个「参数没传」的假象。
 */
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

beforeEach(() => {
  globalThis.localStorage = {
    _v: { auth_token: 'test-token' },
    getItem(k) { return this._v[k] ?? null },
    setItem(k, v) { this._v[k] = String(v) },
    removeItem(k) { delete this._v[k] },
  }
})

describe('summarizeVideo 的覆盖参数', () => {
  test('不传 options 时 overwrite 为 false，不会误触发覆盖', async () => {
    let body = null
    globalThis.fetch = async (url, opts) => {
      body = JSON.parse(opts.body)
      return fakeStream(DONE_STREAM)
    }
    await summarizeVideo('u', 'zh', noop).done
    assert.equal(body.overwrite, false, JSON.stringify(body))
  })

  test('传 overwrite:true 时请求体里真的带上它', async () => {
    let body = null
    globalThis.fetch = async (url, opts) => {
      body = JSON.parse(opts.body)
      return fakeStream(DONE_STREAM)
    }
    await summarizeVideo('u', 'zh', noop, { overwrite: true }).done
    assert.equal(body.overwrite, true, JSON.stringify(body))
    assert.equal(body.url, 'u')
  })

  test('ownership 事件有对应的回调槽', async () => {
    const seen = []
    globalThis.fetch = async () => fakeStream([
      'event: ownership\n', 'data: {"can_regenerate":true}\n',
      ...DONE_STREAM,
    ])
    await summarizeVideo('u', 'zh', { ...noop, onOwnership: (d) => seen.push(d) }).done
    assert.deepEqual(seen, [{ can_regenerate: true }])
  })

  test('请求体不是把整个 options 灌进去（凭据类字段不许顺带上车）', () => {
    // 覆盖是唯一新增的字段。写成 {...options} 会让将来任何一个
    // 顺手加进 options 的东西（比如 api key）跟着发到 /api/summarize。
    assert.ok(
      apiJs.includes('overwrite: !!options.overwrite'),
      '请求体必须逐字段列举，不能展开整个 options',
    )
  })
})

describe('App.vue 的 reparse', () => {
  test('不再调 /api/parse —— 那条路不调模型，是原 bug 的根因', () => {
    const body = bodyOf(appVue, 'function reparse()')
    assert.ok(body, '找不到 reparse 函数体')
    assert.ok(
      !body.includes('parseVideo'),
      `reparse 里仍然调了 parseVideo：${body}`,
    )
  })

  test('它交出的是「按覆盖发起」信号 + 一次组件重建', () => {
    const body = bodyOf(appVue, 'function reparse()')
    assert.ok(body.includes('regenerateRequested.value = true'),
      'reparse 没有把覆盖信号举起来，VideoSummary 就不知道这次要带 overwrite')
    assert.ok(body.includes('summaryKey.value++'),
      'reparse 没有重建 VideoSummary，请求根本发不出去')
  })

  test('权限未知时直接返回，不发请求', () => {
    const body = bodyOf(appVue, 'function reparse()')
    assert.ok(
      body.includes('canRegenerate.value !== true'),
      'reparse 必须等服务端确认了写权限才动手，不能靠前端猜',
    )
  })

  test('横幅按钮在无写权限时是禁用的', () => {
    assert.ok(
      /:disabled="[^"]*canRegenerate !== true/.test(appVue),
      '「重新解析」按钮没有按写权限禁用，后来者仍会看到一个点不动的按钮',
    )
  })

  test('按钮文案区分「不知道」与「不是你的」', () => {
    const body = bodyOf(appVue, 'const reparseButtonText = computed(')
    assert.ok(body, '找不到 reparseButtonText')
    assert.ok(body.includes('canRegenerate.value === false'),
      '「不是你的总结」这一支不见了，后来者看到的仍是「重新解析」')
    assert.ok(body.includes('canRegenerate.value === true'))
  })

  test('换视频时把覆盖信号收回', () => {
    const body = bodyOf(appVue, 'async function handleParse(')
    assert.ok(body.includes('regenerateRequested.value = false'),
      '信号没有在换视频时收回：上一个视频的「重新解析」会让下一个视频白扣一次额度')
    assert.ok(body.includes('canRegenerate.value = null'),
      '写权限没有随视频一起重置，会串到下一个视频上')
  })

  test('横幅的判据是社区视频表，不是「我解析过」', () => {
    // 陌生人打开一条别人解析的视频时，他的个人历史里没有这一条。
    // 拿个人历史去判，「社区里已有」提示与「重新解析」按钮就都永不出现。
    const body = bodyOf(appVue, 'async function handleParse(')
    assert.ok(body.includes('fetchCommunityByUrl(key)'),
      'handleParse 没有查社区视频表的 by-url')
    assert.ok(!appVue.includes('fetchHistoryByUrl'),
      '仍在用个人解析历史判断社区里有没有这一份')
  })

  test('两个入口都用同一个判据', () => {
    // 历史页入口曾经各查各的：两处判断在不同表上，行为就会分叉。
    const body = bodyOf(appVue, 'async function handleOpenRecord(')
    assert.ok(body.includes('fetchCommunityByUrl(detail.video_url)'),
      '历史页入口没有用社区视频表的 by-url')
  })
})

describe('VideoSummary 的覆盖接线', () => {
  test('startSummarize 接收并透传 overwrite', () => {
    const sig = 'function startSummarize('
    assert.ok(summaryVue.includes(sig), '找不到 startSummarize')
    const fn = summaryVue.slice(summaryVue.indexOf(sig))
    assert.ok(/function startSummarize\(overwrite = false\)/.test(fn),
      'startSummarize 必须有一个默认 false 的 overwrite 形参')
    const args = callArgsOf(fn, 'summarizeVideo')
    assert.ok(args, '找不到 summarizeVideo 调用')
    assert.ok(/\{\s*overwrite\s*\}\s*$/.test(args.trim()),
      `overwrite 没有被传进 summarizeVideo，实参尾部是：${args.slice(-80)}`)
  })

  test('「开始 AI 解析」按钮显式加括号，不把 MouseEvent 当成 overwrite', () => {
    // 写成 @click="startSummarize" 时 Vue 会把事件对象作为第一个实参传进去，
    // 于是「开始 AI 解析」会静默地变成一次覆盖请求。这是最容易回归的一处。
    assert.ok(
      /@click="startSummarize\(\)"/.test(summaryVue),
      '模板里必须写 startSummarize()，裸引用会把 MouseEvent 传成 overwrite',
    )
    assert.ok(
      !/@click="startSummarize"/.test(summaryVue),
      '模板里仍有裸的 @click="startSummarize"',
    )
  })

  test('自动发起的请求带上这次是否覆盖', () => {
    assert.ok(
      summaryVue.includes('startSummarize(props.regenerateRequested)'),
      'watch 里必须把 regenerateRequested 传进去，否则覆盖请求退化成普通复用',
    )
  })

  test('复用与覆盖两条路都能自动发起', () => {
    const body = bodyOf(summaryVue, 'watch(() => props.videoUrl')
    assert.ok(
      body.includes('props.hasCommunityResult || props.regenerateRequested'),
      '自动发起的条件里少了重新解析这一支',
    )
  })

  test('ownership 上抛给父组件，由父组件决定按钮能不能点', () => {
    assert.ok(summaryVue.includes("emit('ownership', data)"),
      'ownership 事件没有被上抛，父组件拿不到写权限')
  })

  test('请求一开始就把「忙」告诉父组件，结束时一定收尾', () => {
    const fn = summaryVue.slice(summaryVue.indexOf('function startSummarize('))
    assert.ok(fn.includes("emit('regenerating', true)"),
      '开始时没有通知父组件，横幅上的按钮不会转圈')
    const onDone = bodyOf(fn, 'onDone: () => {')
    assert.ok(onDone && onDone.includes("emit('regenerating', false)"),
      '结束时没有通知父组件，按钮会永远卡在「重新解析中」')
  })
})
