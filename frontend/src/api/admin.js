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
 * 后端契约是 snake_case，前端只用 camelCase。
 *
 * **这个文件是转换的唯一发生地**（工单 #15 收口）。三份转换 —— 用户 / 社区 / 模型
 * —— 都在这里，组件拿到的已经是转好的形状。
 *
 * 为什么非得收在一处：转换散进组件之后，同一份数据会有两种命名形态混在数据流里，
 * 而「读不到的键」不会报错、只会退化成 `undefined` 或默认值。工单 #15 实测过两例：
 * ① `enabled` 两种形态同名读得到，所以布尔/数字口径不一致时**症状只是某几列显示异常**；
 * ② `sort_order` 已翻成 `sortOrder`，`toAdminModelItem` 再读 `sort_order` 读不到就
 * 退回 0，于是**每保存一次就把该行排序号抹成 0**。两处单看都对，数据流上是错的。
 */
const num = (v) => (typeof v === 'number' ? v : Number(v) || 0)

/**
 * 后台用户行 → camelCase。
 *
 * `is_admin` / `is_vip` 翻成布尔（模板只判真假）；额度那几项翻成数字
 * （`-1` 无限、`0` 停用，数字判定比布尔更直接）；`*_override` 保持原值，
 * 因为 `null`（=回落全局）与 `0`（=一条都不能用）是**两种不同状态**。
 */
export function toAdminUser(r) {
  return {
    id: r.id,
    email: r.email,
    isAdmin: !!r.is_admin,
    isVip: !!r.is_vip,
    vipExpireAt: r.vip_expire_at,
    createdAt: r.created_at,
    parseUsed: num(r.parse_used),
    chatUsed: num(r.chat_used),
    parseLimit: num(r.parse_limit),
    chatLimit: num(r.chat_limit),
    parseLimitOverride: r.parse_limit_override,
    chatLimitOverride: r.chat_limit_override,
    parseLimitSource: r.parse_limit_source,
    chatLimitSource: r.chat_limit_source,
  }
}

/**
 * 后台社区行 → camelCase。
 *
 * `status` 原样透传：组件要靠它分「占位中」与「已就绪」，
 * 翻成布尔会把两个状态压成一个。
 */
export function toAdminCommunityItem(r) {
  return {
    id: r.id,
    videoUrl: r.video_url,
    title: r.title,
    authorEmail: r.author_email,
    tags: Array.isArray(r.tags) ? r.tags : [],
    status: r.status,
    createdAt: r.created_at,
  }
}

/**
 * 后台用户列表（只读）。**需管理员**。
 *
 * 返回 **items 已转成 camelCase**（`toAdminUser`）。转换在 api 层做完，
 * 组件拿到就能直接用——不再自己 `.map(toUser)`。
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
  const page1 = toPage(res.data, '用户列表')
  return { ...page1, items: page1.items.map(toAdminUser) }
}

/**
 * 后台社区记录列表。**需管理员**。
 *
 * 不过滤 status：pending 占位行恰恰是后台最该看的（谁占了位没解析完）。
 * status 现已在契约里，组件据此显示「占位中」——管理员要能分辨一条空壳与
 * 一条真内容，删之前才知道自己在删什么。
 *
 * 返回 **items 已转成 camelCase**（`toAdminCommunityItem`）。
 */
export async function fetchAdminCommunity({ page = 1, pageSize = 20 } = {}) {
  const { limit, offset } = toLimitOffset(page, pageSize)
  const res = await client().get('/api/admin/community', {
    params: { limit, offset },
  })
  const p = toPage(res.data, '社区记录')
  return { ...p, items: p.items.map(toAdminCommunityItem) }
}

/**
 * 调整某个用户的解析 / 对话额度。**需管理员。**
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
/**
 * 改一个厂商行（ADR 0010「模型清单可改」）。**需管理员**。
 *
 * 两条不可省的翻译：
 *
 * 1. **只带调用方真的传了的键**。后端是 PATCH 语义：没出现的键 = 不改那一项。
 *    无条件把七个字段全发过去，等于每次改一个显示名都顺手把模型列表、
 *    端点、排序全刷成草稿里的值 —— 而草稿可能没加载全。
 * 2. **enabled 翻成 0/1**。后端显式拒布尔值（Python 里 `True == 1`，
 *    `{"enabled": true}` 会静静地变成「上架」）。前端必须先翻。
 *
 * 键名翻成 snake_case：发 camelCase 的话 pydantic 会当成「七个字段都没出现」，
 * 静默什么都不改还返回 200 —— 与 setUserQuota 那条是同一类坑。
 *
 * @returns {Promise<{item: object, platformDefault: string|null}>}
 *   `item` 是**回读**结果。`platformDefault` 让前端能当场显示
 *   「这一改会影响平台默认模型是什么」，而不是让管理员去猜。
 */
export async function updateAdminModel(providerId, patch = {}) {
  const body = {}
  if ('label' in patch) body.label = patch.label
  if ('hint' in patch) body.hint = patch.hint
  if ('baseUrl' in patch) body.base_url = patch.baseUrl
  if ('models' in patch) body.models = patch.models
  if ('defaultModel' in patch) body.default_model = patch.defaultModel
  if ('enabled' in patch) body.enabled = patch.enabled ? 1 : 0
  if ('sortOrder' in patch) body.sort_order = patch.sortOrder

  if (Object.keys(body).length === 0) {
    // 与后端同一条纪律：空 patch 会被判 400，这里先挡住，省一次往返
    throw new Error('没有任何要改的字段')
  }

  const res = await client().patch(`/api/admin/models/${providerId}`, body)
  if (!res.data || !res.data.item) {
    throw new Error('模型清单更新响应形状不对')
  }
  return {
    item: toAdminModelItem(res.data.item),
    platformDefault: res.data.platform_default || null,
  }
}

/**
 * 后台建号（ADR 0012）。**需管理员。**
 *
 * `email` / `password` 原样发（它们本来就是 snake_case 同形），`is_admin`
 * 显式翻成布尔——后端声明的是 `bool`，发 0/1 虽然也能过，但让「这个字段
 * 是不是开关」这件事在契约上只有一个答案。
 *
 * @returns {Promise<{user: object}>} `user` 是**回读**结果，不是请求值的回显。
 */
export async function createAdminUser({ email, password, isAdmin = false } = {}) {
  const res = await client().post('/api/admin/users', {
    email,
    password,
    is_admin: !!isAdmin,
  })
  if (!res.data || !res.data.user || typeof res.data.user !== 'object') {
    throw new Error('建号响应形状不对')
  }
  return { user: res.data.user }
}

/**
 * 改管理员标记（ADR 0012）。**需管理员。**
 *
 * **只发 is_admin 一个键**：后端 `UserAdminUpdateRequest` 是 extra="forbid"，
 * 多带一个键会整个 422（其中包括 is_vip——VIP 不在后台可改范围）。
 *
 * @returns {Promise<{user: object}>}
 */
export async function setUserAdmin(userId, isAdmin) {
  const res = await client().patch(`/api/admin/users/${userId}`, {
    is_admin: !!isAdmin,
  })
  if (!res.data || !res.data.user || typeof res.data.user !== 'object') {
    throw new Error('权限调整响应形状不对')
  }
  return { user: res.data.user }
}

/**
 * 标签词表（分组 + 上限）。**需管理员**。
 *
 * 前端**不**自己维护一份词表：`CommunityPage.vue` 的标签筛选已经是
 * 「从已加载的卡片汇总」，不在前端抄第二份。后台要让人**勾选**标签就绕不开
 * 词表，所以从服务端取一次——多这一个端点，好过词表在两个地方各活一份。
 *
 * 保留 groups 是因为词表分组顺带说明了每个标签的适用语境，摊平就把这个
 * 信息丢了。maxTags 由服务端给出，是上限的唯一真值。
 *
 * @returns {Promise<{maxTags: number, groups: {name: string, tags: string[]}[]}>}
 */
export async function fetchTagVocabulary() {
  const res = await client().get('/api/admin/tags/vocabulary')
  const d = res.data || {}
  if (!Array.isArray(d.groups) || !d.groups.length) {
    throw new Error('标签词表响应形状不对')
  }
  return {
    maxTags: Number(d.max_tags) > 0 ? Number(d.max_tags) : 3,
    groups: d.groups
      .filter((g) => g && Array.isArray(g.tags) && g.tags.length)
      .map((g) => ({ name: String(g.name || ''), tags: g.tags.map(String) })),
  }
}

/**
 * 改某条社区视频的标签。**需管理员**。
 *
 * `tags` 发**词表内**的值，最多 3 个。服务端会严格校验：词表外一律 400 并在
 * detail 里点名被拒的值——不像模型那条路径会静默回落到「其他」，因为管理员
 * 打错字不该被藏起来（见 ADR 0013）。
 *
 * @returns {Promise<{item: object}>} `item` 是**回读**结果，不是请求值的回显。
 */
export async function updateCommunityTags(videoId, tags) {
  const res = await client().patch(`/api/admin/community/${videoId}`, {
    tags: (tags || []).map(String),
  })
  if (!res.data || !res.data.item || typeof res.data.item !== 'object') {
    throw new Error('改标签响应形状不对')
  }
  return { item: res.data.item }
}

/**
 * 删掉一条社区视频（ADR 0013）。**需管理员**。
 *
 * **只删 `videos` 那一行**。解析过它的用户在自己「历史」里的记录不受影响——
 * 库里没有任何外键指向 videos，这正是该语义成立的前提。
 *
 * @returns {Promise<{deleted: number}>}
 */
export async function deleteCommunityVideo(videoId) {
  const res = await client().delete(`/api/admin/community/${videoId}`)
  return { deleted: res.data?.deleted ?? videoId }
}

/**
 * 删号（ADR 0012）。**需管理员。**
 *
 * 名下有订单或解析历史时后端回 **409**，body 形如
 * `{ detail, blockers: { orders: 2, parse_history: 5 } }`。
 * 本函数把 blockers 原样带出去（不塞进 message）：前端要的是数字，
 * 「还剩几行要处理」不能靠从中文里正则抠。
 *
 * @returns {Promise<{deleted: number}>}
 * @throws {Error} 附带 `.status` 与 `.blockers`（409 时）
 */
export async function deleteAdminUser(userId) {
  const res = await client().delete(`/api/admin/users/${userId}`)
  return { deleted: res.data?.deleted ?? userId }
}
