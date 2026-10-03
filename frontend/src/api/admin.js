import axios from 'axios'

import { toItems, toModelItem } from './models.js'

/**
 * 管理端接口。**需登录且是管理员**（服务端 `require_admin`）。
 *
 * 认证实例的写法与 `api/community.js` 一致：token 从 `auth_token` 取，
 * 没有就不发 Authorization 头（而不是发一个空的 Bearer）。
 */
function client() {
  const token = localStorage.getItem('auth_token')
  return axios.create({
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
}

/**
 * 管理端的一条清单记录 → camelCase：在公共字段之上多两个
 * `enabled` 与 `sortOrder`。
 *
 * 与 `toModelItem` 分开而不是合成一个，是因为「全部行」是**管理视角**：
 * 公开端点按契约只给 `enabled = 1` 的行，混进管理页要用的字段会让
 * 那条边界在同一个函数里变得没法看。
 *
 * `enabled` 同样是 0 / 1 数字，与库表和接口口径一致。
 */
export function toAdminModelItem(raw) {
  return {
    ...toModelItem(raw),
    enabled: Number(raw?.enabled) ? 1 : 0,
    sortOrder: Number(raw?.sort_order) || 0,
  }
}

/** 一次请求 + 一次形状检查。两种出口共用，免得两条路各断各的。 */
async function fetchAdminItems(map) {
  const res = await client().get('/api/admin/models')
  return { items: toItems(res.data, map) }
}

/**
 * 全部厂商清单（含 `enabled = 0` 的行）。**仅管理员**。
 *
 * 注意与 `api/models.js` 的 `fetchPublicModelCatalog` 分开：普通用户调这个
 * 会拿到 403，BYOK 面板要用的是公开那一份。
 *
 * @returns {Promise<{ items: object[] }>} items 已转成 camelCase
 */
export async function fetchAdminModelCatalog() {
  return fetchAdminItems(toAdminModelItem)
}

/**
 * 同一个端点的**原文**出口：`{ items: [...] }` 就是服务端给的那一份
 * （snake_case，不做任何字段映射）。
 *
 * 留它是因为 `AdminPage.vue` 现在 import 的正是这个名字，并且自己在组件里
 * 做那层映射。新代码要 camelCase 请用 `fetchAdminModelCatalog()`，或者
 * 直接 `.map(toAdminModelItem)` —— 转换收在 api 层，而不是散在组件里。
 *
 * @returns {Promise<{ items: object[] }>} 服务端原文
 */
export async function fetchAdminModels() {
  return fetchAdminItems((row) => row)
}
// ── 分页列表（工单 #12）─────────────────────────────────────
//
// 服务端返回 `{items, total, limit, offset}`（数据层 `list_admin_users` /
// `list_admin_community` 的原样），而组件按 `page` / `pageSize` 想。
// 翻译收在**这一层**：组件不该知道 offset 怎么算，api 层也不该知道
// 组件内部有个 v.page。
//
// **返回的 pageSize 用服务端回传的那一个**，不是我们请求的那一个：
// 服务端对 limit 有上界（ADMIN_PAGE_SIZE_*），请求 500 会被夹到上界。
// 组件若还按自己请求的 500 算页数，翻到第二页就会漏行 / 重复行。

/** 把组件的 page/pageSize 翻成服务端的 limit/offset。page 从 1 起。 */
function toLimitOffset(page, pageSize) {
  const p = Number.isFinite(page) && page >= 1 ? Math.floor(page) : 1
  const size = Number.isFinite(pageSize) && pageSize >= 1 ? Math.floor(pageSize) : 20
  return { limit: size, offset: (p - 1) * size }
}

/** 形状不对就抛，与 `toItems` 同一纪律：接口没按约定返回 ≠ 这一页是空的。 */
function toPage(data, what) {
  if (!data || !Array.isArray(data.items)) {
    throw new Error(`${what}响应形状不对`)
  }
  const limit = Number(data.limit) > 0 ? Number(data.limit) : data.items.length
  const offset = Number(data.offset) >= 0 ? Number(data.offset) : 0
  return {
    items: data.items,
    total: Number(data.total) || 0,
    limit,
    offset,
    page: Math.floor(offset / limit) + 1,
    pageSize: limit,
  }
}

/**
 * 后台用户列表（只读）。**需管理员**。
 *
 * 返回**服务端原文**（snake_case），字段映射由 `AdminPage.vue` 的 `toUser`
 * 负责——与同文件里 `fetchAdminModels` 的出口保持同一个约定。
 *
 * @param {object} o
 * @param {number} [o.page=1]      从 1 起
 * @param {number} [o.pageSize=20]
 * @param {string} [o.q='']        按邮箱模糊匹配
 * @returns {Promise<{items: object[], total: number, page: number, pageSize: number}>}
 */
export async function fetchAdminUsers({ page = 1, pageSize = 20, q = '' } = {}) {
  const { limit, offset } = toLimitOffset(page, pageSize)
  const res = await client().get('/api/admin/users', {
    params: { limit, offset, q: String(q || '').trim() },
  })
  return toPage(res.data, '用户列表')
}

/**
 * 后台社区记录列表（只读）。**需管理员**。
 *
 * 不过滤 status：pending 占位行恰恰是后台最该看的（谁占了位没解析完）。
 * 契约里这一项没有 status 字段，所以组件也拿不到——见工单后续项。
 */
export async function fetchAdminCommunity({ page = 1, pageSize = 20 } = {}) {
  const { limit, offset } = toLimitOffset(page, pageSize)
  const res = await client().get('/api/admin/community', {
    params: { limit, offset },
  })
  return toPage(res.data, '社区记录')
}

/**
 * 调整某个用户的解析 / 对话额度。**需管理员。本文件唯一的写操作。**
 *
 * 字段名必须翻成 snake_case（`parse_limit` / `chat_limit`）才与服务端
 * `QuotaUpdateRequest` 对上——发 camelCase 的话 pydantic 会当成「两个字段
 * 都没出现」，于是 `exclude_unset` 得到空字典，**静默什么都不改还返回 200**。
 * 那是本页最容易犯也最难发现的错，所以在这里翻，不留给调用方。
 *
 * 语义（服务端 `QuotaUpdateRequest` 的注释）：
 *   - 传 `null` → 清除覆盖，回落全局
 *   - 键整个不出现 → 这一项不动
 * 本函数**总是**把两个键都发出去，因为组件的 `toQuotaValue` 只会产出
 * `null` 或整数（留空 = 清除），语义一致。
 *
 * @returns {Promise<{user: object, note: string|null, message: string}>}
 *   `user` 是**回读**结果（服务端重查库），不是请求值的回显。
 *   `note === 'vip_not_effective'` 时本次调整此刻不生效（VIP 短路）。
 */
export async function setUserQuota(userId, { parseLimit = null, chatLimit = null } = {}) {
  const res = await client().post(`/api/admin/users/${userId}/quota`, {
    parse_limit: parseLimit ?? null,
    chat_limit: chatLimit ?? null,
  })
  if (!res.data || !res.data.user || typeof res.data.user !== 'object') {
    throw new Error('额度调整响应形状不对')
  }
  return { user: res.data.user, note: res.data.note ?? null, message: res.data.message ?? '' }
}
