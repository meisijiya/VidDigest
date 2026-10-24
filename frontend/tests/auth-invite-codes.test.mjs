/**
 * 邀请码注册的前端接线（工单 #99）。
 *
 * ## 这份文件断的是什么
 *
 * 「注册要邀请码」「管理员要能发码」在前端各有一处接线，坏了都不会报错：
 * 输入框没了只是「少一格」，邀请码没进请求体只是「后端说无效」。
 * 所以这里把接线本身钉住。
 *
 * ## 为什么是源码断言
 *
 * 挂载层测试（vitest，tests/*.spec.mjs）能验运行时行为，但那是另一条 runner；
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

const authModalSrc = stripComments(read('../src/components/AuthModal.vue'))
const adminPageSrc = stripComments(read('../src/components/AdminPage.vue'))

/**
 * AuthModal 的模板段。
 *
 * 它没有嵌套 <template>，取第一个 </template> 就是顶层那个。
 */
const authTemplate = authModalSrc.slice(0, authModalSrc.indexOf('</template>'))

/**
 * AdminPage 的模板段。
 *
 * 模板里有 <template v-for>，所以必须取**最后**一个 </template>——
 * 取第一个会把后面的内容整段切掉，断言就在一个缩小的范围里判了。
 */
const adminTemplate = adminPageSrc.slice(0, adminPageSrc.lastIndexOf('</template>'))

/** 两个组件的 <script setup> 段 */
const authScript = authModalSrc.slice(authModalSrc.indexOf('<script setup'))
const adminScript = adminPageSrc.slice(adminPageSrc.indexOf('<script setup'))

/**
 * 注册表单里那一格邀请码。
 *
 * 边界取到「这一格 div 结束」为止 —— 断的是**这一格**，
 * 不是整个模板（整个模板里也有「邀请码」这三个字，在说明文字里）。
 */
const inviteField = sliceBetween(authTemplate, 'v-if="mode === \'register\'"', '</div>', 'AuthModal 的邀请码输入格')

/** 把邀请码塞进请求体的那段逻辑 */
const injectCode = sliceBetween(
  authScript,
  'async function registerWithInviteCode(',
  'async function handleSubmit(',
  'AuthModal 的邀请码注入',
)

/** 提交处理（含失败分支） */
const submitHandler = sliceBetween(authScript, 'async function handleSubmit(', '</script>', 'AuthModal 的提交处理')

/** 管理后台的四个动作，各自一段 */
const listInvites = sliceBetween(
  adminScript,
  'async function loadInvites(',
  'function toggleInvites(',
  'AdminPage 的邀请码列表',
)
const toggleInvites = sliceBetween(
  adminScript,
  'function toggleInvites(',
  'async function createInvite(',
  'AdminPage 的邀请码区块展开',
)
const createInvite = sliceBetween(
  adminScript,
  'async function createInvite(',
  'async function revokeInvite(',
  'AdminPage 的发码',
)
const revokeInvite = sliceBetween(
  adminScript,
  'async function revokeInvite(',
  'async function copyIssuedCode(',
  'AdminPage 的作废',
)

describe('注册表单的邀请码格', () => {
  test('注册模式有一格邀请码输入', () => {
    assert.match(authTemplate, /v-model="inviteCode"/, '注册表单里没有绑定到 inviteCode 的输入框')
    assert.match(inviteField, /邀请码/, '这一格里没有「邀请码」三个字')
  })

  test('登录模式不显示这一格', () => {
    assert.match(inviteField, /v-if="mode === 'register'"/, '邀请码格没有限定在注册模式 —— 登录时也会多出一格')
  })

  test('邀请码标着必填，但不拿它拦提交', () => {
    assert.match(inviteField, /aria-required="true"/, '邀请码格没有 aria-required')
    assert.match(inviteField, /必填/, '邀请码格没有可见的必填标记')
    // 刻意不加 HTML required：jsdom 会跑约束校验，空 required 会让
    // submit 事件根本不触发 —— 那会拦下「填了码才提交」这条真实路径。
    // 权威判据在后端（400 + 「需要邀请码」），前端只负责说清楚。
    assert.doesNotMatch(inviteField, /\srequired\b/, '邀请码格用了 HTML required 拦提交')
  })
})

describe('邀请码真的进了请求体', () => {
  test('注册走的是带邀请码的那条路', () => {
    assert.match(
      submitHandler,
      /registerWithInviteCode\(email\.value, password\.value, inviteCode\.value\.trim\(\)\)/,
      '注册分支没有把邀请码带上去',
    )
  })

  test('请求体里多出 invite_code 这一键', () => {
    assert.match(injectCode, /invite_code: code/, '请求体里没有 invite_code')
    assert.match(injectCode, /interceptors\.request\.use/, '邀请码不是靠请求拦截器注入的')
  })

  test('拦截器用完就摘掉，不漏到别的请求上', () => {
    assert.match(injectCode, /interceptors\.request\.eject/, '拦截器没有被摘掉')
    assert.match(injectCode, /finally/, '拦截器不是在 finally 里摘的 —— 请求失败时会漏出去')
  })
})

describe('注册失败把后端那句话显示出来', () => {
  test('读的是后端给的 detail', () => {
    assert.match(
      submitHandler,
      /e\.response\?\.data\?\.detail/,
      '失败提示不是取后端 detail —— 邀请码的四种失效原因就靠这句话告诉用户',
    )
    assert.match(submitHandler, /'操作失败'/, '取不到 detail 时没有回落提示')
  })
})

describe('管理后台的邀请码区块', () => {
  test('有发码入口', () => {
    assert.match(createInvite, /post\('\/api\/admin\/invite-codes'/, '发码请求打错了地址')
    assert.match(createInvite, /max_uses/, '发码请求没有带上可用次数')
    assert.match(createInvite, /expires_in_hours/, '发码请求没有带上有效期')
  })

  test('有列表入口', () => {
    assert.match(listInvites, /get\('\/api\/admin\/invite-codes'\)/, '列表请求打错了地址')
  })

  test('有作废入口', () => {
    // 整串比到收尾的反引号与右括号：只比前缀的话，地址后面多挂一截
    // （比如 /revoke-mutant）照样能匹配，判据就成了一条摆设。
    assert.match(revokeInvite, /post\(`\/api\/admin\/invite-codes\/\$\{id\}\/revoke`\)/, '作废请求打错了地址')
    assert.match(adminTemplate, /@click="revokeInvite\(row\.id\)"/, '列表里没有那一行的作废按钮')
  })

  test('列表只显示脱敏码', () => {
    assert.match(adminTemplate, /\{\{ row\.codeMasked \}\}/, '列表没有显示脱敏码')
    // 完整码只在刚生成的那一格里出现一次（明文只给一次）。
    // 列表这一段不许碰完整码字段 —— 后端在列表出口就没给。
    assert.doesNotMatch(adminTemplate, /row\.code\b/, '列表里读了完整码字段')
    assert.match(adminTemplate, /\{\{ issuedCode \}\}/, '刚生成的明文码没地方显示')
    assert.match(adminTemplate, /@click="copyIssuedCode"/, '明文码没有复制按钮')
  })

  test('展开才加载列表', () => {
    assert.match(toggleInvites, /loadInvites\(\)/, '展开区块时没有加载列表')
    const mounted = sliceBetween(adminScript, 'onMounted(', '</script>', 'AdminPage 的 onMounted')
    assert.doesNotMatch(mounted, /loadInvites/, '列表在挂载时就加载了 —— 进后台就打一次接口')
  })
})
