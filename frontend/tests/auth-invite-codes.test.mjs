/**
 * 邀请码注册的前端接线（工单 #99）。
 *
 * ## 这份文件断的是什么
 *
 * 「注册要邀请码」在注册表单里有一处接线，坏了不会报错：
 * 输入框没了只是「少一格」，邀请码没进请求体只是「后端说无效」。
 * 所以这里把接线本身钉住。
 *
 * 管理侧（发码、列表、作废、脱敏码）在工单 #104 里搬去了独立路由
 * `/admin/invite-codes`，那些判据在 tests/invite-codes-page.test.mjs
 * （静态接线）与 tests/invite-codes-page.mount.spec.mjs（真挂载）里。
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

/**
 * AuthModal 的模板段。
 *
 * 它没有嵌套 <template>，取第一个 </template> 就是顶层那个。
 */
const authTemplate = authModalSrc.slice(0, authModalSrc.indexOf('</template>'))

/** AuthModal 的 <script setup> 段 */
const authScript = authModalSrc.slice(authModalSrc.indexOf('<script setup'))

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

// 管理侧（发码、列表、作废、脱敏码）的判据搬到
// tests/invite-codes-page.test.mjs 与 tests/invite-codes-page.mount.spec.mjs：
// 邀请码管理在工单 #104 里从管理后台的折叠区块搬成了独立路由
// `/admin/invite-codes`，那一段的接线与渲染都不在 AdminPage 里了。
