/**
 * 工单 #13：厂商清单只有一个来源。
 *
 * 漂移是既有的、正在发生的（2026-10-03 实测）：前端硬编码 7 个厂商、
 * 默认模型写死 `qwen-plus`，后端只认 2 个、默认 `qwen-turbo`。
 * 本文件守的是改完之后的三件事：
 *
 *   1. **字段转换只发生一次**（api/models.js），且是白名单投影。
 *      逐字段断，因为「看起来有」不成立：少转一个、或者原样透传，
 *      页面上都是「下拉能用但某个值是 undefined」。
 *   2. **两个端点各取各的**：公开的不要鉴权头，管理的要 Bearer。
 *      用真 axios + 假 fetch 跑（沿用 community-page.test.mjs 的做法）。
 *   3. **前端不再硬编码厂商清单** —— 这是本工单的核心判据，下面
 *      「再搬回来就红」那一组。
 *
 * 第 3 组是**静态断言**（读源码）。测法上唯一的纪律：每条判据独立成
 * `test`，因为弱的排在强的后面就永远不跑。工单 #7 在这上面栽过：
 * 断言写在别的正则字面量里、跨不过去，于是恒真。
 */
import { test, describe, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import axios from 'axios'

import { toItems, toModelItem, fetchPublicModelCatalog } from '../src/api/models.js'
import { toAdminModelItem, fetchAdminModelCatalog } from '../src/api/admin.js'
import { chooseProvider, getRequestCredential, save, clear, validateBaseUrl } from '../src/lib/byok.js'

/** 归一化行尾符：本仓库是 CRLF。 */
function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

/** 断言要看代码，不是解释代码的散文 */
function stripComments(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    // 协议里的 // 不是注释：只剥「前面不是冒号」的那种，否则
        // `https://x.com` 会被削成 `https:`，域名连同后面整行一起消失，
        // 扫源码的断言于是永远看不到它 —— 这条判据会变成死的。
    .map((l) => l.replace(/(^|[^:])\/\/.*$/, '$1'))
    .join('\n')
}

const SRC = fileURLToPath(new URL('../src/', import.meta.url))
/** 扫源码目录用：厂商清单要真被删干净，光查 byok.js 不够（换个文件写一样是漂移）。 */
function srcFiles(dir = SRC) {
  const out = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) out.push(...srcFiles(full))
    else if (/\.(js|vue)$/.test(name)) out.push(full)
  }
  return out
}

const byokLib = stripComments(read('../src/lib/byok.js'))
const dialogVue = stripComments(read('../src/components/ByokDialog.vue'))
const dialogScript = stripComments(read('../src/components/ByokDialog.vue').split('<script setup>')[1] ?? '')

/**
 * 曾经的硬编码表里出现过的地址。**一个都不该再出现在前端源码里。**
 *
 * 这条判据不认「变量名」也不认「哪个文件」：数组换个名字、搬去别的模块，
 * 它照样红。这正是工单 #13 的判别法（整体换回硬编码 → 转红）。
 */
const VENDOR_HOSTS = [
  'dashscope.aliyuncs.com',
  'api.deepseek.com',
  'api.moonshot.cn',
  'api.openai.com',
  'localhost:11434',
]
/** 必须插值构造，不能把点直接扔进正则：`.` 会变成「任意字符」，
 *  那样 `apiXdeepseekYcom` 也算命中，判据就废了。 */
const hostPattern = (host) => new RegExp(host.replace(/\./g, '\\.'))

/**
 * 一份**接口响应样本**（不是前端的数据源：真值在服务端库里）。
 *
 * models 故意重复一项：断有序序列要用 deepEqual，逐个 includes() 断不出
 * 「两个相同的值少了一个」——去重与漏项在它眼里一模一样。
 */
const WIRE = [
  {
    id: 'bailian',
    label: '阿里云百炼',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    default_model: 'qwen-plus',
    models: ['qwen-plus', 'qwen-plus', 'qwen-turbo'],
    hint: '阿里云百炼 OpenAI 兼容模式。',
    is_real: 1,
  },
  {
    id: 'platform',
    label: '平台 Key（默认）',
    base_url: '',
    default_model: '',
    models: [],
    hint: '用平台配置的模型服务，消耗每日免费额度。',
    is_real: 0,
  },
]

beforeEach(() => {
  // axios 在 Node 里默认走 http adapter，会真的去解析域名（ENOTFOUND）。
  // 两件事必须钉住，否则这些用例测的是网络而不是接线：
  //   baseURL —— 相对路径 '/api/...' 在 Node 里是 ERR_INVALID_URL
  //   adapter —— 换成 fetch，下面的 globalThis.fetch 假实现才拦得住
  axios.defaults.baseURL = 'http://catalog.test'
  axios.defaults.adapter = 'fetch'
  globalThis.localStorage = {
    _v: { auth_token: 'test-token' },
    getItem(k) { return this._v[k] ?? null },
    setItem(k, v) { this._v[k] = String(v) },
    removeItem(k) { delete this._v[k] },
  }
  // byok.js 的 state 是模块级的，跨用例残留会让「没填 key」的断言假红
  clear()
})

/** 记下请求打到哪、带没带 Authorization。接线断言靠这个而不是靠实现细节。 */
function spyFetch(body) {
  const calls = []
  globalThis.fetch = async (input, init = {}) => {
    const asRequest = input instanceof Request
    calls.push({
      url: asRequest ? input.url : String(input),
      // 请求头的键名在 Request 上是**小写**的。用 `'Authorization' in headers`
      // 去问，永远问不到 —— 那条断言恒真，等于没断言（工单 #7 的教训）。
      auth: asRequest
        ? input.headers.get('authorization')
        : (init.headers?.Authorization ?? null),
    })
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  }
  return calls
}

describe('snake_case → camelCase：只在这一处发生一次', () => {
  test('留下的就是那七个字段，多一个都不留', () => {
    assert.deepEqual(
      Object.keys(toModelItem(WIRE[0])).sort(),
      ['baseUrl', 'defaultModel', 'hint', 'id', 'isReal', 'label', 'models'],
      '字段集变了：要么少转了，要么把服务端的多余字段透传进来了',
    )
  })

  test('逐字段对得上，不靠「看起来有」', () => {
    const item = toModelItem(WIRE[0])
    assert.equal(item.id, 'bailian')
    assert.equal(item.label, '阿里云百炼')
    assert.equal(item.baseUrl, WIRE[0].base_url)
    assert.equal(item.defaultModel, 'qwen-plus')
    assert.equal(item.hint, '阿里云百炼 OpenAI 兼容模式。')
  })

  test('models 保留原顺序，也保留重复项', () => {
    // 逐个 includes() 断不出这一条：去重、漏项在它眼里一样绿。
    assert.deepEqual(toModelItem(WIRE[0]).models, ['qwen-plus', 'qwen-plus', 'qwen-turbo'])
  })

  test('「不是真厂商」的行由 isReal = 0 标出来，类型与库表一致', () => {
    // 数字 0/1 而不是布尔：接口与库表都是 0/1（is_real=0 / enabled=1）。
    // 这里再翻一层布尔只会多一次口径，管理表格要按同一套比大小。
    assert.equal(toModelItem(WIRE[1]).isReal, 0)
    assert.equal(toModelItem(WIRE[0]).isReal, 1)
    assert.equal(toModelItem({}).isReal, 0, '缺字段时被算成了「真厂商」')
  })

  test('接口多返回一个字段，它不会顺着透传进页面', () => {
    // 白名单投影的全部意义。形似凭据的字段（ADR 0004 / 0011：凭据不入库、
    // 不入接口）不该有「万一前端漏渲染」的路径。
    const item = toModelItem({ ...WIRE[0], api_key: 'sk-should-not-survive', secret: 'x' })
    assert.deepEqual(Object.keys(item), Object.keys(toModelItem(WIRE[0])))
    assert.ok(!JSON.stringify(item).includes('sk-should-not-survive'))
  })

  test('缺字段 / 不是对象时给空值，不抛', () => {
    const item = toModelItem({ id: 'x' })
    assert.deepEqual(
      [item.label, item.baseUrl, item.defaultModel, item.hint, item.models.length],
      ['', '', '', '', 0],
    )
    assert.doesNotThrow(() => toModelItem(null))
    assert.doesNotThrow(() => toModelItem('不是对象'))
  })

  test('响应形状不对要抛，不能静默给个空清单', () => {
    // 「清单是空的」和「接口没按约定返回」在页面上长得一模一样：
    // 都是一个不能用的下标。静默返回空数组就把两者混起来了，
    // 用户没有任何线索知道该刷新还是该报错。
    for (const bad of [null, {}, { items: null }, { items: '不是数组' }, '不是对象']) {
      assert.throws(() => toItems(bad), `${JSON.stringify(bad)} 被当成了空清单`)
    }
    assert.deepEqual(toItems({ items: [] }), [])
  })
})

describe('两个端点各取各的：公开的不用鉴权，管理的要 Bearer', () => {
  // ── 工单 #15 收口（同族项）：用户 / 社区列表的转换也在 api 层做完 ──
  // 组件曾拿到 snake_case 再自己 .map(toUser)。现在出口直接给 camelCase。
  const USER_WIRE = {
    id: 7,
    email: 'u7@example.com',
    is_admin: 1,
    is_vip: 0,
    vip_expire_at: null,
    created_at: '2026-01-02T03:04:05+00:00',
    parse_used: '2',
    chat_used: 1,
    parse_limit: -1,
    chat_limit: 10,
    parse_limit_override: null,
    chat_limit_override: 0,
    parse_limit_source: 'global',
    chat_limit_source: 'override',
  }
  const COMM_WIRE = {
    id: 3,
    video_url: 'https://example.com/v/abc',
    title: '标题',
    author_email: 'a@example.com',
    tags: ['编程', '架构设计'],
    status: 'pending',
    created_at: '2026-01-02T03:04:05+00:00',
  }

  test('用户列表出口已经转成 camelCase（组件不再自己 .map）', async () => {
    const calls = spyFetch({ items: [USER_WIRE], total: 1, limit: 20, offset: 0 })
    const { fetchAdminUsers } = await import('../src/api/admin.js')
    const data = await fetchAdminUsers({ page: 1, pageSize: 20 })
    assert.ok(calls[0].url.startsWith('http://catalog.test/api/admin/users?'),
      `用户列表端点不对：${calls[0].url}`)
    const u = data.items[0]
    assert.equal(u.isAdmin, true, 'is_admin 必须是布尔 —— 模板判真假用')
    assert.equal(u.isVip, false, '0 要翻成 false，不是 0')
    assert.equal(u.parseUsed, 2, "字符串 '2' 要翻成数字")
    assert.equal(u.parseLimit, -1, '-1 = 无限，必须保住符号')
    assert.equal(u.chatLimitOverride, 0,
      'override = 0（一条都不能用）不能被当成 falsy 丢掉 —— 它与 null 是两种状态')
    assert.equal(u.parseLimitOverride, null, 'null = 回落全局，与 0 语义不同，不能混')
    assert.equal(u.chatLimitSource, 'override')
    assert.ok(!('is_admin' in u), '出口里还留着 snake_case 键 —— 组件读 camelCase 会全读不到')
  })

  test('社区列表出口已经转成 camelCase，status 原样透传', async () => {
    const calls = spyFetch({ items: [COMM_WIRE], total: 1, limit: 20, offset: 0 })
    const { fetchAdminCommunity } = await import('../src/api/admin.js')
    const data = await fetchAdminCommunity({ page: 1, pageSize: 20 })
    assert.ok(calls[0].url.startsWith('http://catalog.test/api/admin/community?'),
      `社区列表端点不对：${calls[0].url}`)
    const c = data.items[0]
    assert.equal(c.videoUrl, COMM_WIRE.video_url)
    assert.equal(c.authorEmail, COMM_WIRE.author_email)
    assert.equal(c.status, 'pending',
      'status 必须原样透传 —— 组件靠它分「占位中」与「已就绪」，翻成布尔会把两个状态压成一个')
    assert.deepEqual(c.tags, ['编程', '架构设计'])
  })

  test('公开端点打 /api/models，不带 Authorization，返回已转好的清单', async () => {
    const calls = spyFetch({ items: WIRE })
    const data = await fetchPublicModelCatalog()
    assert.equal(calls.length, 1)
    assert.equal(calls[0].url, 'http://catalog.test/api/models')
    assert.equal(calls[0].auth, null, '公开端点不该带凭据（token 也就不必发去用不上的地方）')
    assert.deepEqual(data.items.map((x) => x.id), ['bailian', 'platform'])
    assert.equal(data.items[0].baseUrl, WIRE[0].base_url)
  })

  test('管理端点打 /api/admin/models，带 Bearer，多给 enabled / sortOrder', async () => {
    const calls = spyFetch({ items: [{ ...WIRE[1], enabled: 0, sort_order: 3 }] })
    const data = await fetchAdminModelCatalog()
    assert.equal(calls[0].url, 'http://catalog.test/api/admin/models')
    assert.equal(calls[0].auth, 'Bearer test-token')
    assert.deepEqual(
      Object.keys(data.items[0]).sort(),
      ['baseUrl', 'defaultModel', 'enabled', 'hint', 'id', 'isReal', 'label', 'models', 'sortOrder'],
    )
    assert.equal(data.items[0].enabled, 0)
    assert.equal(data.items[0].sortOrder, 3)
  })

  test('两条路的公共字段是同一个转换，不是两份各写一遍的映射', () => {
    assert.deepEqual(
      toAdminModelItem({ ...WIRE[0], enabled: 1, sort_order: 0 }),
      { ...toModelItem(WIRE[0]), enabled: 1, sortOrder: 0 },
    )
  })

  test('同端点只留一个出口：原文那份已删（工单 #15 收口）', async () => {
    // 曾经这里有个 `fetchAdminModels()`（服务端原文，不做映射）与
    // `fetchAdminModelCatalog()`（camelCase）打同一个端点，转换却散在
    // AdminPage.vue 里。同端点两个出口 = 两种形状同时存在 = 用错哪个都不报错，
    // 只表现为表格某几列是空的。
    const mod = await import('../src/api/admin.js')
    assert.equal(mod.fetchAdminModels, undefined,
      '原文出口又回来了 —— 同端点两个出口会让人在不知情的情况下拿到 snake_case')
    // 活着的那一个必须仍然打对端点、带上 Bearer。
    const calls = spyFetch({ items: [{ ...WIRE[1], enabled: 1, sort_order: 2 }] })
    const data = await fetchAdminModelCatalog()
    assert.equal(calls[0].url, 'http://catalog.test/api/admin/models')
    assert.equal(calls[0].auth, 'Bearer test-token')
    assert.equal(data.items[0].sortOrder, 2, '留存的出口必须翻成 camelCase')
  })

  test('updateAdminModel 的回读也是 camelCase（组件据此替换本地行）', async () => {
    // PATCH 的响应同样过 `toAdminModelItem`。少了这一步，
    // 组件 `replaceModel` 拿到的就是 snake_case —— 而模板读的是
    // `m.sortOrder` / `m.baseUrl`，全读到 undefined 且**不报错**，
    // 症状只是保存后表格里某几列变空。
    spyFetch({
      item: { ...WIRE[1], enabled: 1, sort_order: 2 },
      platform_default: WIRE[1].default_model,
    })
    const { updateAdminModel } = await import('../src/api/admin.js')
    const out = await updateAdminModel('bailian', { label: '改名' })
    assert.equal(out.item.sortOrder, 2,
      'PATCH 回读没有翻成 camelCase —— 组件 replaceModel 会拿到 snake_case，'
      + '而模板读 camelCase，于是几列静默变空')
    assert.equal(out.item.baseUrl, WIRE[1].base_url)
    assert.equal(out.item.isReal, WIRE[1].is_real)
    assert.ok(!('sort_order' in out.item), '回读里还留着 snake_case 键')
  })

  test('没登录时不发一个空的 Bearer', async () => {
    globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
    const calls = spyFetch({ items: [] })
    await fetchAdminModelCatalog()
    assert.equal(calls[0].auth, null,
      '发了一个空的 Bearer，服务端会收到一个看着像有凭据的请求')
  })
})

describe('清单项真的能变成一份可用的配置', () => {
  test('拉回来的记录 → 选厂商 → 请求层拿到它的端点与模型，且过得了自己的预检', () => {
    save({ apiKey: 'sk-local-test', provider: 'platform', baseUrl: '', model: '' })
    const item = toModelItem(WIRE[0])
    const s = chooseProvider(item.id, item)
    assert.equal(s.provider, 'bailian')
    const cred = getRequestCredential()
    assert.equal(cred.baseUrl, WIRE[0].base_url)
    assert.equal(cred.model, 'qwen-plus')
    assert.equal(validateBaseUrl(cred.baseUrl), null, '清单里的默认端点过不了前端自己的预检')
    assert.equal(cred.apiKey, 'sk-local-test', '选厂商把已存的 key 顺手清掉了')
  })

  test('清单里空端点的那几行（「平台」/「自定义」）落到配置里仍是空', () => {
    // 空端点 = 用服务端默认，不是「把用户上一次填的端点留着」。
    const item = toModelItem(WIRE[1])
    const s = chooseProvider(item.id, item)
    assert.equal(s.baseUrl, '')
    assert.equal(s.model, '')
    assert.equal(s.mode, 'platform')
  })
})

describe('厂商清单再搬回前端硬编码，就转红', () => {
  test('byok.js 里没有任何厂商服务地址', () => {
    for (const host of VENDOR_HOSTS) {
      assert.ok(!hostPattern(host).test(byokLib), `byok.js 里又写死了 ${host}`)
    }
  })

  test('byok.js 里没有任何一条厂商记录的字段', () => {
    // 厂商记录必带 label / hint（给人看的部分）。byok.js 该留的只有
    // provider / baseUrl / model 三个配置字段，都不在这个名单里。
    // 这条不认变量名：数组改名成 VENDORS 照样红。
    const catalogKeys = ['label', 'hint', 'models', 'base_url', 'default_model', 'is_real']
    const found = catalogKeys.filter((k) => new RegExp(`\\b${k}\\s*:`).test(byokLib))
    assert.deepEqual(found, [], `byok.js 里又出现了厂商记录的字段：${found.join(' ')}`)
  })

  test('byok.js 不再导出 PROVIDERS', () => {
    assert.ok(!/\bPROVIDERS\b/.test(byokLib), '硬编码表的名字又回来了')
  })

  test('整个 src 下没有任何厂商服务地址（换个文件写一样红）', () => {
    const hits = []
    for (const file of srcFiles()) {
      const src = stripComments(readFileSync(file, 'utf8'))
      for (const host of VENDOR_HOSTS) {
        if (hostPattern(host).test(src)) hits.push(`${relative(SRC, file)}:${host}`)
      }
    }
    assert.deepEqual(hits, [], `前端源码里又写死了厂商地址：${hits.join(' ')}`)
  })

  test('下拉渲染的是拉回来的清单，不是模块常量', () => {
    assert.ok(!/\bPROVIDERS\b/.test(dialogVue), '弹窗里还在按模块常量渲染下拉')
    assert.ok(dialogVue.includes('v-for="p in catalog"'), '下拉不是按拉回来的清单渲染')
    assert.ok(
      dialogScript.includes("from '../api/models.js'"),
      '弹窗没有从接口拿清单',
    )
  })

  test('选厂商时把那条记录一起交给 byok.js（那边已经查不到表了）', () => {
    assert.ok(
      /chooseProvider\(\s*id,\s*catalog\.value\.find\(/.test(dialogScript),
      '调用方没把记录传进去：byok.js 既查不到表，也就没有默认端点可填',
    )
  })
})
