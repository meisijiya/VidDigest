/**
 * 邀请码管理页的接线（工单 #104 / ADR 0025）。
 *
 * ## 这份文件断的是什么
 *
 * 「邀请码管理页」这条链路上有五处接线，坏了都不会报错：
 * 路由没挂、页签入口没点、列表没取、发码请求打错地址、列表把脱敏码换成完整码。
 * 症状分别是「打开 404」「后台里找不到入口」「页面空着」「点了没反应」
 * 「明文到处能看」——没有一条会自己报错。
 *
 * ## 为什么是源码断言
 *
 * 挂载层测试（vitest，tests/*.spec.mjs）验运行时行为，是另一条 runner；
 * 本文件走 node --test（npm test），没有 DOM。仓库的分工就是这样：
 * 静态 / 契约判据留在 *.test.mjs，渲染行为留在 *.spec.mjs。
 *
 * ## 每条断言都有变异兜着
 *
 * 文本断言最容易的失败模式是「判据够不着它」——功能坏掉了它还绿着。
 * 所以本文件里的每一条，frontend/tests/mutation-ui-fixes.mjs 里都有一条对应的
 * 变异：破坏实现之后它必须转红。没有变异兜着的文本断言只是一句愿望。
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { sliceBetween } from './helpers/source-slice.mjs'

/** 归一化行尾符 */
function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

/**
 * 去掉注释：断言要看代码，不是解释代码的散文。
 *
 * HTML 注释与块注释都剥；行注释只剥「整行都是注释」的，
 * 免得把 `https://x` 这种串削掉一半。
 */
function stripComments(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((l) => (l.trim().startsWith('//') ? '' : l))
    .join('\n')
}

const pageSrc = stripComments(read('../src/components/InviteCodesPage.vue'))
const apiSrc = stripComments(read('../src/api/admin.ts'))
const routerSrc = stripComments(read('../src/router/index.ts'))
const appSrc = stripComments(read('../src/App.vue'))
const adminPageSrc = stripComments(read('../src/components/AdminPage.vue'))

/** 页面模板段：取最后一个 </template>，前面的整块都算模板。 */
const pageTemplate = pageSrc.slice(0, pageSrc.lastIndexOf('</template>'))
/** 页面脚本段 */
const pageScript = pageSrc.slice(pageSrc.indexOf('<script setup'))

describe('邀请码管理页的路由与入口', () => {
  test('路由表里有这条路径，且页键是它自己的', () => {
    assert.match(
      routerSrc,
      /path: '\/admin\/invite-codes'[\s\S]{0,300}meta: \{ page: 'admin-invites' \}/,
      '路由表里没有 /admin/invite-codes，或者它没有自己的 meta.page',
    )
  })

  test('这一页的「返回」回管理后台，不是回首页', () => {
    const branch = sliceBetween(appSrc, "case 'admin-invites':", 'default:', 'App.vue 的 admin-invites 接线')
    assert.match(branch, /back: openAdmin/, '这一页的返回动作没有接 openAdmin')
    assert.match(appSrc, /function openAdmin\(\)[\s\S]{0,120}router\.push\('\/admin'\)/, 'openAdmin 没有去 /admin')
  })

  test('管理后台里有一个去这一页的入口', () => {
    assert.match(adminPageSrc, /@click="emit\('open-invite-codes'\)"/, '管理后台里没有去邀请码管理页的按钮')
    assert.match(
      appSrc,
      /case 'admin':[\s\S]{0,200}'open-invite-codes': openInviteCodes/,
      "App.vue 没有把后台页的 'open-invite-codes' 事件接上",
    )
    assert.match(
      appSrc,
      /function openInviteCodes\(\)[\s\S]{0,120}router\.push\('\/admin\/invite-codes'\)/,
      'openInviteCodes 没有去 /admin/invite-codes',
    )
  })

  test('后台页自己不再留一份邀请码实现', () => {
    // 搬走之后只留入口。两处实现 = 两份真相，改了一处忘了另一处。
    assert.doesNotMatch(
      adminPageSrc,
      /api\/admin\/invite-codes/,
      '管理后台里还有邀请码的请求 —— 代码没搬干净，现在是两份实现',
    )
  })
})

describe('api 层的三个出口', () => {
  test('列表出口打对了地址，并带分页与筛选', () => {
    assert.match(apiSrc, /get\('\/api\/admin\/invite-codes', \{/, '列表请求打错了地址')
    assert.match(apiSrc, /params: \{ limit, offset/, '列表请求没有带分页参数')
    assert.match(apiSrc, /\.\.\.\(status \? \{ status \} : \{\}\)/, '列表请求没有把状态筛选传出去')
  })

  test('发码出口打对了地址，并带次数、可用次数、有效期、备注', () => {
    const seg = sliceBetween(
      apiSrc,
      'export async function createAdminInviteCodes(',
      'export async function revokeAdminInviteCode(',
      'api 层的批量发码',
    )
    assert.match(seg, /post\('\/api\/admin\/invite-codes', \{/, '发码请求打错了地址')
    assert.match(seg, /count,/, '发码请求没有带上数量')
    assert.match(seg, /max_uses: maxUses/, '发码请求没有带上可用次数')
    assert.match(seg, /expires_in_hours: expiresInHours/, '发码请求没有带上有效期')
    assert.match(seg, /note: note \|\| null/, '发码请求没有带上备注')
  })

  test('作废出口打对了地址', () => {
    // 整串比到收尾的反引号与右括号：只比前缀的话，地址后面多挂一截
    // （比如 /revoke-mutant）照样能匹配，判据就成了一条摆设。
    assert.match(apiSrc, /post\(`\/api\/admin\/invite-codes\/\$\{inviteId\}\/revoke`\)/, '作废请求打错了地址')
  })

  test('组件只从 api 层取数据，形状转换不在组件里', () => {
    const m = pageSrc.match(/import \{([^}]*)\} from '\.\.\/api\/admin'/)
    assert.ok(m, '邀请码管理页没有从 api/admin 取出口')
    const names = m[1]
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .sort()
    assert.deepEqual(
      names,
      ['createAdminInviteCodes', 'fetchAdminInviteCodes', 'revokeAdminInviteCode'],
      '邀请码管理页从 api 层取的名字变了 —— 组件不许自己拼出口',
    )
    assert.ok(!names.some((n) => /^to[A-Z]/.test(n)), '组件不许 import 形状转换函数 —— 转换只该在 api 层有一份')
  })
})

describe('页面自己的接线', () => {
  test('有发码入口', () => {
    assert.match(pageScript, /createAdminInviteCodes\(\{/, '页面没有调用发码出口')
    assert.match(pageTemplate, /type="submit"/, '发码表单没有提交按钮')
    assert.match(pageTemplate, /id="invite-count"/, '发码表单里没有「发放数量」这一格')
  })

  test('有列表入口', () => {
    assert.match(pageScript, /fetchAdminInviteCodes\(\{/, '页面没有调用列表出口')
    assert.match(pageTemplate, /<table/, '页面上没有列表')
  })

  test('有作废入口', () => {
    assert.match(pageScript, /revokeAdminInviteCode\(/, '页面没有调用作废出口')
    assert.match(pageTemplate, /@click="revoke\(row\.id\)"/, '列表里没有那一行的作废按钮')
  })

  test('列表只显示脱敏码', () => {
    assert.match(pageTemplate, /\{\{ row\.codeMasked \}\}/, '列表没有显示脱敏码')
    // 完整码只在刚发放的那一块出现（明文只给一次）。
    // 列表这一段不许碰完整码字段 —— 后端在列表出口就没给。
    assert.doesNotMatch(pageTemplate, /row\.code\b/, '列表里读了完整码字段')
  })

  test('明文只给一次', () => {
    assert.match(pageTemplate, /\{\{ item\.code \}\}/, '刚发放的明文码没地方显示')
    assert.match(pageTemplate, /@click="copyCode\(item\.code\)"/, '明文码没有复制按钮')
    assert.match(pageScript, /issuedCodes\.value = res\.items/, '发放后没有把明文码收进「刚发放」那一块')
  })

  test('挂载即加载列表', () => {
    const mounted = sliceBetween(pageScript, 'onMounted(', '</script>', '邀请码管理页的 onMounted')
    assert.match(mounted, /void loadCodes\(\)/, '页面挂载时没有加载列表')
  })

  test('筛选与分页真的传给接口', () => {
    assert.match(pageScript, /status: status\.value/, '状态筛选没有传给接口')
    assert.match(pageScript, /page: page\.value/, '页码没有传给接口')
    assert.match(pageScript, /pageSize: PAGE_SIZE/, '每页条数没有传给接口')
    assert.match(pageTemplate, /id="invite-status"/, '页面上没有状态筛选下拉框')
  })

  test('列表里有发放人这一列', () => {
    assert.match(pageTemplate, /\{\{ row\.createdByEmail \|\| '—' \}\}/, '列表里没有显示发放人')
  })
})
