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

/**
 * 「加第三种额度」在前端是什么形状（工单 #38 的后端收口带出来的）。
 *
 * 后端已把额度种类收成一个常量（`database.QUOTA_KINDS`），但**前端没有**：
 * `describeQuota` / `quotaBadgeClass` 在 src/lib/quota.js:41-42、:77 三处
 * 硬写 `quotaOf(q, 'parse')` 与 `quotaOf(q, 'chat')`。所以后端加了第三种
 * 额度之后：
 *
 *   - 不会崩、不会渲染 undefined/NaN（实测，payload 多一个键完全无害）；
 *   - **也不会多显示一行**——第三种额度压根没被前端读。
 *
 * 这就是后端那张工单正文写错的地方：它称漏一个键会让前端走
 * `fallbackQuota` 兜底、显示旧口径数字。实测不成立——降级条件是
 * `!parse && !chat`（**两个都缺**才降级，`src/lib/quota.js:44`），少一个键
 * 只是安静地少渲染；而第三种额度的形状下，前端从头到尾不看它。
 *
 * 这组用例把「多出来的键无害」与「缺失的一半不触发兜底」两件事**真正跑出来**，
 * 而不是只断言后端契约——后端那张 Python 守卫管不到前端这条路径。
 */
describe('第三种额度在前端的形状', () => {
  const base = {
    logged_in: true,
    unlimited: false,
    parse: { remaining: 2, limit: 3 },
    chat: { remaining: 7, limit: 10 },
  }

  test('payload 多出第三种额度时文案逐字不变（前端不读它）', () => {
    const before = describeQuota(base)
    const after = describeQuota({ ...base, export: { remaining: 4, limit: 8 } })
    assert.equal(after, before,
      '前端硬写 parse/chat 两个 kind（src/lib/quota.js:41-42），第三种不会被渲染——'
      + '这是已知边界，本条钉住它，别让它悄悄变成 undefined/NaN 或崩溃')
  })

  test('payload 多出第三种额度时徽章色不变', () => {
    assert.equal(
      quotaBadgeClass({ ...base, export: { remaining: 4, limit: 8 } }),
      quotaBadgeClass(base),
      '徽章色只看 parse/chat 两个槽位（src/lib/quota.js:77）',
    )
  })

  test('只缺 chat 时不触发兜底，输出与只缺 chat 的旧形状一致', () => {
    // 降级条件是 `!parse && !chat`（src/lib/quota.js:44）——两个都缺才降级。
    // 这条把工单正文那条「少一个键就走 fallback」的错误判据钉成反面。
    const missingChat = describeQuota({
      logged_in: true, unlimited: false,
      parse: { remaining: 2, limit: 3 },
    })
    assert.doesNotMatch(missingChat, /undefined/)
    assert.doesNotMatch(missingChat, /NaN/)
    assert.match(missingChat, /解析/, '还剩 parse 可显示，不该降级成旧口径文案')
  })

  test('两个都缺才降级到旧形状（兼容期回退仍然有效）', () => {
    const text = describeQuota({
      logged_in: true, unlimited: false, remaining: 2, limit: 3,
    })
    assert.match(text, /2\s*\/\s*3/, '两个槽位都缺时才用顶层 remaining/limit 兜底')
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
