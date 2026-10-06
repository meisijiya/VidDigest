/**
 * 写历史失败要看得见（工单 #17 第 6 项）。运行：node --test tests/
 *
 * 个人历史是**由前端驱动**的：`/api/summarize` 从不写 parse_history，
 * 唯一的写入途径是浏览器发的那两次 `POST /api/history/save`
 * （App.vue 的 persistParseRecord 与 VideoSummary.vue 的 persistHistory）。
 *
 * 原来的 `saveHistory` 是 `catch { }`——一个空 catch。于是这条请求失败时：
 * 用户已经拿到总结了，界面完全正常，刷新一次页面那次解析就不见了，
 * 而症状与「根本没解析成功」在界面上同形，不留痕迹就永远查不出来。
 *
 * 测法与本仓既有几条一致：.js 里的行为用假 fetch 真跑一遍（真调 saveHistory），
 * .vue 部分只做源码接线断言——渲染验证需要挂载环境（工单 #18）。
 */
import { test, describe, beforeEach, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import axios from 'axios'

import { saveHistory } from '../src/api/history.js'

/** 归一化行尾符：本仓库是 CRLF，直接按 \n 切块会切空。 */
function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

const apiCode = read('../src/api/history.js')

/** 记下 warn 痕迹，并且不动用户之外的任何全局状态。 */
let warnings = []
const realWarn = console.warn

beforeEach(() => {
  warnings = []
  console.warn = (...args) => { warnings.push(args) }
  // axios 在 Node 里默认走 http adapter，会真的去解析域名（ENOTFOUND）。
  // baseURL：相对路径 '/api/...' 在 Node 里是 ERR_INVALID_URL。
  // adapter：换成 fetch，下面的 globalThis.fetch 假实现才拦得住。
  axios.defaults.baseURL = 'http://history.test'
  axios.defaults.adapter = 'fetch'
  globalThis.localStorage = {
    _v: { auth_token: 'test-token' },
    getItem(k) { return this._v[k] ?? null },
    setItem(k, v) { this._v[k] = String(v) },
    removeItem(k) { delete this._v[k] },
  }
})

afterEach(() => {
  console.warn = realWarn
})

/** 让下一次 axios 请求成功或失败，返回记下的请求列表。 */
function stubResponse(status) {
  const calls = []
  globalThis.fetch = async (input, init = {}) => {
    const asRequest = input instanceof Request
    const url = asRequest ? input.url : String(input)
    calls.push({ url, method: (asRequest ? input.method : init.method) || 'GET' })
    const body = status < 400
      ? new Response(JSON.stringify({ success: true }), { status })
      : new Response('boom', { status })
    return body
  }
  return calls
}

const PAYLOAD = { url: 'https://example.com/v', video_title: '标题' }

describe('saveHistory · 失败静默', () => {
  test('写失败时留下一条可查的痕迹', async () => {
    stubResponse(500)

    await saveHistory(PAYLOAD)

    assert.equal(warnings.length, 1, `失败没有留下任何痕迹：${JSON.stringify(warnings)}`)
    const [tag, err] = warnings[0]
    assert.match(String(tag), /history/, '痕迹没有标明是历史写入')
    assert.ok(err, '痕迹里没有带上原始错误：看到「写历史失败」也不知道是 500 还是断网')
  })

  test('写失败时不打断主流程（不抛给调用方）', async () => {
    stubResponse(500)

    await assert.doesNotReject(() => saveHistory(PAYLOAD),
      '失败被抛出去了：用户已经拿到总结，却因为一条历史记录看到未捕获异常')
  })

  test('写成功时不留痕迹', async () => {
    stubResponse(200)

    await saveHistory(PAYLOAD)

    assert.equal(warnings.length, 0,
      `成功也报了警告：${JSON.stringify(warnings)}——那会训练所有人忽略它`)
  })

  test('请求真的发到了 /api/history/save', async () => {
    const calls = stubResponse(200)

    await saveHistory(PAYLOAD)

    assert.equal(calls.length, 1, '保存请求根本没发出去')
    assert.match(calls[0].url, /\/api\/history\/save$/)
    assert.equal(calls[0].method, 'POST')
  })

  test('catch 体不是空的（源码形状对照）', () => {
    // 行为断言盯的是「有没有 warn」；这一条钉住字面形状，防止有人改成
    // `.catch(() => null)` ——非空但同样无声，行为断言抓不到。
    assert.doesNotMatch(apiCode, /catch\s*\{\s*(\/\/[^\n]*)?\s*\}/,
      'saveHistory 又变回空 catch 了')
  })
})

describe('两个调用点都走这一条路', () => {
  test('App.vue 与 VideoSummary.vue 都不自己吞掉失败', () => {
    for (const f of ['../src/App.vue', '../src/components/VideoSummary.vue']) {
      const src = read(f)
      assert.match(src, /saveHistory\(/, `${f} 不再走 saveHistory`)
      // 组件层不该出现「自己包一层空 catch」——那条路会让 saveHistory 的
      // 痕迹被挡在组件里，而组件往往又把 catch 整个丢掉。
      const around = src.slice(src.indexOf('function persist'), src.indexOf('function persist') + 700)
      assert.doesNotMatch(around, /\.catch\(\s*\(\s*\)\s*=>\s*\{\s*\}\s*\)/,
        `${f} 在组件层又把失败吞了`)
    }
  })
})
