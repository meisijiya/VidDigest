/**
 * api/admin.js 的**回读出口**必须自己翻好（工单 #22）。
 *
 * ## 这个文件存在的原因：收口做完之后，四个出口没有任何东西守着
 *
 * 变异实测：把 `setUserAdmin` / `createAdminUser` / `setUserQuota` /
 * `updateCommunityTags` 任意一个改回「返回服务端原文」，**两条测试关卡全绿**
 * ——文本侧（`admin-ui.test.mjs`）读的是组件源码，挂载侧
 * （`admin-page.mount.spec.mjs:34`）`vi.mock` 掉了整个 `api/admin.js`。
 *
 * 也就是说：那两个关卡都在测**组件**，从来没人测过 api 层这四个函数自己。
 * 列表出口有 `model-catalog.test.mjs` 用真 axios 跑，回读出口一个都没有。
 *
 * 收口前的危险方向是反的：照着注释给四个出口**补**转换，组件里那行
 * `res.user.is_admin` 读到 undefined → 提权成功却提示「已撤销」。收口之后
 * 方向翻转成**少**翻一次 —— 而那条更静默：所有字段变 undefined、界面不报错。
 *
 * ## 用真 axios + 假 fetch，不 mock api 模块
 *
 * `vi.mock('../src/api/admin.js')` 一上，转换函数就被替身顶掉了，测的就不再是
 * 接线而是替身。沿用 `model-catalog.test.mjs` 的做法：真 axios、adapter 换
 * fetch、只把 `globalThis.fetch` 换成假实现。这样断言的对象是**真实出口的返回值**。
 *
 * ## 判据是「键集合恰好相等」，不是「某个键在」
 *
 * 逐个断言「`isAdmin` 在」的话，出口少翻三个字段照样绿——症状只是表格里几列空着。
 * 断言键集合能一次抓住「少转」「多转」「转错命名」三种坏法。
 * 再各补一条**值**的断言，堵住「键对了但值翻错了」（`1` vs `true`、
 * `'0'` vs `0`），那正是工单 #15 实测过的 `enabled` 口径不一致。
 */
import { test, describe, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import axios from 'axios'

import {
  setUserQuota, createAdminUser, setUserAdmin, updateCommunityTags,
} from '../src/api/admin.js'

// ── 服务端原文（snake_case），四个出口的回读体 ──────────────
//
// 哨兵值刻意选成「翻错就看得见」的：is_admin 给 1（要翻成 true 而不是 1）、
// parse_used 给 2（要翻成数字而不是字符串）、tags 给数组（要确认没被当字符串）。
const RAW_USER = {
  id: 7,
  email: 'u@example.com',
  is_admin: 1,
  is_vip: 0,
  vip_expire_at: null,
  created_at: '2026-01-02 03:04:05',
  parse_used: 2,
  chat_used: 1,
  parse_limit: 10,
  chat_limit: 20,
  parse_limit_override: null,
  chat_limit_override: null,
  parse_limit_source: 'global',
  chat_limit_source: 'global',
}

/** `toAdminUser` 返回值的**全部**键。多一个键 = 多泄漏一列，少一个 = 功能坏了。 */
const USER_KEYS = [
  'chatLimit', 'chatLimitOverride', 'chatLimitSource', 'chatUsed',
  'createdAt', 'email', 'id', 'isAdmin', 'isVip',
  'parseLimit', 'parseLimitOverride', 'parseLimitSource', 'parseUsed',
  'vipExpireAt',
]

const RAW_ITEM = {
  id: 10,
  video_url: 'https://v.example/x',
  title: '一个标题',
  author_email: 'a@example.com',
  tags: ['科普', '教程'],
  status: 'ready',
  created_at: '2026-01-02 03:04:05',
}

const ITEM_KEYS = ['authorEmail', 'createdAt', 'id', 'status', 'tags', 'title', 'videoUrl']

let lastBody

/** 断言一个对象是纯 camelCase：既不缺键多键，也没有任何 snake_case 残留。 */
function assertShape(got, expectedKeys, what) {
  assert.deepEqual(Object.keys(got).sort(), [...expectedKeys].sort(),
    `${what} 的键集合不是「恰好等于转换函数的输出」——要么少翻了几个字段，要么多翻了一个。`
    + ` 实得：${JSON.stringify(Object.keys(got).sort())}`)
  const snake = Object.keys(got).filter((k) => k.includes('_'))
  assert.deepEqual(snake, [], `${what} 的返回值里还有 snake_case 键：${snake.join(', ')}`
    + ' —— 出口没翻，组件读到的每个字段都会是 undefined 而界面不报错')
}

beforeEach(() => {
  // axios 在 Node 里默认走 http adapter，会真的去解析域名（ENOTFOUND）。
  // baseURL —— 相对路径 '/api/...' 在 Node 里是 ERR_INVALID_URL
  // adapter —— 换成 fetch，下面的 globalThis.fetch 假实现才拦得住
  axios.defaults.baseURL = 'http://admin.test'
  axios.defaults.adapter = 'fetch'
  globalThis.localStorage = {
    _v: { auth_token: 'test-token' },
    getItem(k) { return this._v[k] ?? null },
    setItem(k, v) { this._v[k] = String(v) },
    removeItem(k) { delete this._v[k] },
  }
  globalThis.fetch = async () => new Response(JSON.stringify(lastBody), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
})

// ── 四个回读出口 ───────────────────────────────────────────

describe('api/admin.js · 回读出口自己翻好', () => {
  test('setUserQuota：回读的 user 是 camelCase', async () => {
    lastBody = { user: RAW_USER, note: null, message: '' }
    const res = await setUserQuota(7, { parseLimit: 5, chatLimit: 6 })

    assertShape(res.user, USER_KEYS, 'setUserQuota')
    assert.equal(res.user.isAdmin, true, 'is_admin=1 必须翻成布尔 true，不是数字 1')
    assert.equal(res.user.parseUsed, 2, 'parse_used 必须翻成数字 2')
    // 兄弟字段仍在，说明这不是「整体原样透传」蒙对了。
    assert.equal(res.user.email, 'u@example.com')
    // note / message 与 user 平行返回，别被转换顺手吃掉
    assert.equal(res.message, '')
  })

  test('createAdminUser：回读的 user 是 camelCase', async () => {
    // 刻意把 is_admin 压成 0：默认号不是管理员，这条顺带证明「0 翻成 false」
    // 而不是被 `||` 之类的写法吞掉——翻错成 undefined 与翻错成 true 都是静默的。
    lastBody = { user: { ...RAW_USER, id: 99, email: 'new@example.com', is_admin: 0 } }
    const res = await createAdminUser({ email: 'new@example.com', password: 'secret1' })

    assertShape(res.user, USER_KEYS, 'createAdminUser')
    assert.equal(res.user.isAdmin, false, 'is_admin=0 必须翻成布尔 false，不是 undefined')
    assert.equal(res.user.isVip, false, 'is_vip=0 同理')
  })

  test('setUserAdmin：回读的 user 是 camelCase', async () => {
    lastBody = { user: RAW_USER }
    const res = await setUserAdmin(7, true)

    assertShape(res.user, USER_KEYS, 'setUserAdmin')
    // 这一条是工单 #22 的正主：AdminPage.vue 的提权提示读的就是 `res.user.isAdmin`。
    // 出口不翻时它读到 undefined → 提权成功却提示「已撤销管理员权限」，不报错。
    assert.equal(res.user.isAdmin, true,
      '提权提示读的就是这个字段。它若不是布尔，提权成功会被显示成「已撤销」')
  })

  test('updateCommunityTags：回读的 item 是 camelCase', async () => {
    lastBody = { item: RAW_ITEM }
    const res = await updateCommunityTags(10, ['科普', '教程'])

    assertShape(res.item, ITEM_KEYS, 'updateCommunityTags')
    assert.deepEqual(res.item.tags, ['科普', '教程'], 'tags 必须仍是数组，不能被原样透传成别的形态')
    assert.equal(res.item.videoUrl, 'https://v.example/x')
  })

  // ── 对照组：形状不对必须抛，而不是悄悄返回半个对象 ────────
  //
  // 没有这条，上面的断言可能因为「出口抛异常 → 测试红」而过，
  // 而它红的原因跟「出口没翻」是同一件事——分不开就会把两件事混成一件。

  test('回读体缺 user / item 时抛错，不返回一个空壳', async () => {
    lastBody = { note: null }
    await assert.rejects(() => setUserQuota(7, {}), /响应形状不对/,
      'user 缺失时必须抛。返回 { user: undefined } 会让组件在 replaceUser 里静默早退，'
      + '界面停在旧值上且不报错')

    lastBody = { note: null }
    await assert.rejects(() => setUserAdmin(7, true), /响应形状不对/)
    lastBody = { note: null }
    await assert.rejects(() => createAdminUser({ email: 'a@b.c', password: 'secret1' }), /响应形状不对/)
    lastBody = { note: null }
    await assert.rejects(() => updateCommunityTags(10, []), /响应形状不对/)
  })
})