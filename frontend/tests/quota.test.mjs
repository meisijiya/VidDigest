/**
 * 额度展示契约。运行：node --test tests/
 *
 * 背景：额度拆成解析 / 对话两个独立计数器（工单 #4）。
 * 这组测试锁的是「用户能看到两个独立数字」——
 * 只有一个数字的话，拆分对用户就是不可见的。
 *
 * 测纯函数而不是 Vue 组件：组件要挂载环境，额度文案是纯展示逻辑，
 * 抽出来才能在没有构建工具的条件下被测。
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { describeQuota, quotaBadgeClass } from '../src/lib/quota.js'

describe('describeQuota', () => {
  test('未登录时不显示任何数字', () => {
    const text = describeQuota({ logged_in: false })
    assert.match(text, /登录/)
    assert.doesNotMatch(text, /\d+\s*\/\s*\d+/, '未登录不该显示成 "3 / 3"')
  })

  test('VIP 显示无限', () => {
    const text = describeQuota({
      logged_in: true,
      unlimited: true,
      parse: { remaining: -1, limit: 3 },
      chat: { remaining: -1, limit: 10 },
    })
    assert.match(text, /无限/)
  })

  test('同时显示解析与追问两个额度', () => {
    const text = describeQuota({
      logged_in: true,
      unlimited: false,
      parse: { remaining: 2, limit: 3 },
      chat: { remaining: 7, limit: 10 },
    })
    // 两个数字都要在，且各自的 remaining/limit 配对正确
    assert.match(text, /解析/)
    assert.match(text, /追问/)
    assert.match(text, /2\s*\/\s*3/, '解析额度应为 2 / 3')
    assert.match(text, /7\s*\/\s*10/, '追问额度应为 7 / 10')
  })

  test('两个额度数字不串位', () => {
    // 解析用满、追问充足：两个数字必须各自独立，不能都显示成同一个
    const text = describeQuota({
      logged_in: true,
      unlimited: false,
      parse: { remaining: 0, limit: 3 },
      chat: { remaining: 9, limit: 10 },
    })
    assert.match(text, /0\s*\/\s*3/)
    assert.match(text, /9\s*\/\s*10/)
  })

  test('额度用完时明确说用完', () => {
    const text = describeQuota({
      logged_in: true,
      unlimited: false,
      parse: { remaining: 0, limit: 3 },
      chat: { remaining: 4, limit: 10 },
    })
    assert.match(text, /用完/)
  })

  test('信息缺失时给出占位而不是崩溃', () => {
    assert.equal(describeQuota(null), '—')
    assert.equal(describeQuota({ logged_in: true, parse: null, chat: null }), '—')
  })

  test('后端缺 parse 字段时按旧形状回退', () => {
    // 兼容期后端可能仍只给 remaining/limit；不能因此渲染成 undefined
    const text = describeQuota({
      logged_in: true,
      unlimited: false,
      remaining: 2,
      limit: 3,
    })
    assert.doesNotMatch(text, /undefined/)
    assert.doesNotMatch(text, /NaN/)
  })
})

describe('quotaBadgeClass', () => {
  test('未登录走中性色', () => {
    const cls = quotaBadgeClass({ logged_in: false })
    assert.match(cls, /gray/)
  })

  test('VIP 走琥珀色', () => {
    const cls = quotaBadgeClass({
      logged_in: true, unlimited: true,
      parse: { remaining: -1, limit: 3 }, chat: { remaining: -1, limit: 10 },
    })
    assert.match(cls, /amber/)
  })

  test('任一额度用完即走红色', () => {
    const cls = quotaBadgeClass({
      logged_in: true, unlimited: false,
      parse: { remaining: 0, limit: 3 }, chat: { remaining: 4, limit: 10 },
    })
    assert.match(cls, /red/)
  })

  test('都充足走蓝色', () => {
    const cls = quotaBadgeClass({
      logged_in: true, unlimited: false,
      parse: { remaining: 2, limit: 3 }, chat: { remaining: 8, limit: 10 },
    })
    assert.match(cls, /blue/)
  })

  test('空数据不抛异常', () => {
    assert.doesNotThrow(() => quotaBadgeClass(null))
    assert.doesNotThrow(() => quotaBadgeClass({ logged_in: true }))
  })
})

/**
 * 组件接线守卫（静态检查，不是渲染测试）。
 *
 * 独立复审时发现：整套前端测试里对 .vue 的引用数为 0。也就是说
 * applyQuotaEvent 只要丢掉 parse / chat，界面就会无声退回单数字，
 * 而纯函数测试照样全绿。
 *
 * 这里只做源码层面的存在性检查——它证明不了渲染结果，
 * 但能挡住「有人把某条流的 onQuota 摘掉 / 把 parse、chat 丢了」这类
 * 最常见的接线回归。真正的渲染验证需要挂载环境，超出本仓库
 * 「node --test 零额外依赖」的约定。
 */
describe('VideoSummary 组件接线', () => {
  // 归一化行尾符：本仓库是 CRLF，直接按 \n 切函数体会切空。
  const source = readFileSync(
    new URL('../src/components/VideoSummary.vue', import.meta.url),
    'utf8',
  ).replace(/\r\n/g, '\n')

  test('applyQuotaEvent 原样保留事件的 parse 与 chat', () => {
    const start = source.indexOf('function applyQuotaEvent(d)')
    assert.notEqual(start, -1, '找不到 applyQuotaEvent')
    // 用紧跟其后的顶层 "}\n" 收尾——切窄了会切到内层对象字面量的括号，
    // 那样下面两条断言就成了空断言。
    const body = source.slice(start, source.indexOf('\n}\n', start) + 2)
    assert.match(body, /quotaInfo\.value\s*=/, '切出来的不是完整函数体')
    assert.match(body, /parse:\s*d\.parse/, 'applyQuotaEvent 丢掉了 parse')
    assert.match(body, /chat:\s*d\.chat/, 'applyQuotaEvent 丢掉了 chat')
  })

  test('总结流与追问流都把 quota 事件接到同一个处理器', () => {
    const hooks = source.match(/onQuota:\s*applyQuotaEvent/g) ?? []
    assert.equal(hooks.length, 2, `应有 2 条流接上 applyQuotaEvent，实际 ${hooks.length}`)
  })

  test('模板渲染的是 describeQuota 的结果', () => {
    assert.match(source, /\{\{\s*quotaLabel\s*\}\}/, '模板不再渲染 quotaLabel')
    assert.match(source, /:class="quotaBadgeClass"/, '模板不再绑定额度徽章样式')
  })
})
