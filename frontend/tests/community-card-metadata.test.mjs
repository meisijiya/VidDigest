/**
 * 平台元数据随解析请求一起发出去（工单 #17 第 2 项的前端部分）。运行：node --test tests/
 *
 * 背景：社区卡片的标题与封面原先靠前端解析成功后打
 * POST /api/community/cards 回填，而那条 UPDATE 要求 status='ready'。
 * 首次解析的那一刻 videos 行还不存在（它要等用户点「AI 总结」才被
 * reserve_video 建出来），于是那次回填永远匹配 0 行——首次解析者填的
 * 标题/封面填不进去，卡片实际由第二个访问者补。
 *
 * 现在改成：/api/parse 之后前端手上就有标题与缩略图，
 * 发起解析时一起带过去，服务端在占位那一刻就写进那一行。
 *
 * 测法与本仓既有几条一致（见 community-tags.test.mjs / reparse-owner.test.mjs）：
 * 请求体用假流真跑一遍（行为断言）；.vue 部分只做源码接线检查——
 * 判据的对象是**源码文本**（「实参表里有没有这个键名」），挂载后看不到
 * 模板源码，迁过去会退化成「界面上没出现那串字」。
 *
 * ⚠️ 原注释写的是「渲染验证需要挂载环境，超出本仓『node --test 零额外
 * 依赖』的约定（工单 #18）」——而工单 #18 **正是创建 npm run test:mount 的
 * 那张票**。拿 #18 当不能挂载的理由，自相矛盾，那句话当时就是错的。
 */
import { test, describe, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { summarizeVideo } from '../src/api/summarize.js'

const DONE_STREAM = ['event: done\n', 'data: [DONE]\n']

/** 假流：行为对齐真实 fetch + ReadableStream */
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

/** 真跑一次 summarizeVideo，把请求体抓下来 */
function captureBody() {
  const box = {}
  globalThis.fetch = async (url, opts) => {
    box.url = url
    box.body = JSON.parse(opts.body)
    return fakeStream(DONE_STREAM)
  }
  return box
}

const noop = { onDone: () => {} }

/** 去掉注释再读：断言要看代码，不是解释代码的散文 */
function stripComments(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((l) => l.replace(/(^|[^:])\/\/.*$/, '$1'))
    .join('\n')
}

const summaryVue = stripComments(readFileSync(
  new URL('../src/components/VideoSummary.vue', import.meta.url), 'utf8'))

/** 抽出一个具名函数的函数体，按大括号配平 */
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

/** 抽出一次调用的实参表，按圆括号配平（不能用 [^)]*，实参里有嵌套回调） */
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

describe('summarizeVideo 的平台元数据', () => {
  test('标题与封面进了请求体', async () => {
    const box = captureBody()
    await summarizeVideo('u', 'zh', noop, {
      videoTitle: '深入理解异步编程',
      coverUrl: 'https://img.example/cover.jpg',
    }).done

    assert.equal(box.body.video_title, '深入理解异步编程', JSON.stringify(box.body))
    assert.equal(box.body.cover_url, 'https://img.example/cover.jpg')
  })

  test('没给就发空串，不是 null', async () => {
    // null 不是「没带封面」，服务端 SummarizeRequest 那两个字段是 str：
    // 收到 null 会直接 422，而症状是「AI 解析整个坏了」，
    // 与真实原因离得很远。undefined 会被 JSON.stringify 悄悄丢掉，
    // 于是请求体的形状就取决于调用方写了什么——那更不该由服务端去猜。
    const box = captureBody()
    await summarizeVideo('u', 'zh', noop, { videoTitle: null, coverUrl: null }).done

    assert.equal(box.body.video_title, '', JSON.stringify(box.body))
    assert.equal(box.body.cover_url, '')
  })

  test('缺省调用时两个键都在，且是空串', async () => {
    const box = captureBody()
    await summarizeVideo('u', 'zh', noop).done

    assert.ok('video_title' in box.body, JSON.stringify(box.body))
    assert.ok('cover_url' in box.body, JSON.stringify(box.body))
    assert.equal(box.body.video_title, '')
  })
})

describe('VideoSummary 的元数据接线', () => {
  const fn = bodyOf(summaryVue, 'function startSummarize(')

  test('startSummarize 把标题传给 summarizeVideo', () => {
    assert.ok(fn, '找不到 startSummarize')
    const args = callArgsOf(fn, 'summarizeVideo')
    assert.ok(args, '找不到 summarizeVideo 调用')
    assert.match(args, /videoTitle:\s*props\.videoTitle/,
      `标题没有透传：实参尾部是 ${args.slice(-160)}`)
  })

  test('封面取自 videoData.thumbnail，不是 videoData.cover', () => {
    // /api/parse 的返回里它叫 thumbnail（downloader.py 的 info.get("thumbnail")），
    // 社区卡片那一列才叫 cover_url。两个名字指的是同一个东西，
    // 写成 videoData.cover_url 恒为 undefined，而症状是「封面永远是空的」。
    assert.ok(fn, '找不到 startSummarize')
    const args = callArgsOf(fn, 'summarizeVideo')
    assert.match(args, /coverUrl:\s*props\.videoData\?\.thumbnail/,
      `封面字段取错了：实参尾部是 ${args.slice(-160)}`)
    assert.doesNotMatch(args, /coverUrl:\s*props\.videoData\?\.cover_url/)
  })
})