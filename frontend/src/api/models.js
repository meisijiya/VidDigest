import axios from 'axios'

/**
 * 平台厂商清单。**唯一**来源是服务端的 `GET /api/models`（工单 #13）。
 *
 * ## 为什么这个文件存在
 *
 * 接口字段是 snake_case（`base_url` / `default_model` / `is_real`），
 * 而本仓前端约定是 camelCase（`lib/byok.js` 的配置一直是 `baseUrl`）。
 * 这层转换**只在这里发生一次**：`ByokDialog.vue` 与 `AdminPage.vue` 拿到的
 * 已经是 camelCase，谁也不许再各写一份字段映射 —— 那种「顺手改一下」的
 * 字段名正是漂移的起点。
 *
 * ## 为什么是**白名单**投影而不是原样透传
 *
 * 透传意味着服务端将来多返回一个字段，它就会自动出现在页面上。
 * 所以 `toModelItem` 逐个列出要留的字段：多出来的（尤其是任何形似凭据的）
 * 一律丢掉。凭据一律不入库、不入接口（ADR 0004 / 0011），前端也不该
 * 为「万一」留一条把它渲染出去的路。
 */

const str = (v) => (v === null || v === undefined ? '' : String(v))

/**
 * snake_case 的一条清单记录 → camelCase。
 *
 * 保留的是 7 个公共字段：id / label / baseUrl / defaultModel / models /
 * hint / isReal。`platform` 与 `custom` 这类「不是真厂商」的行由
 * `isReal = 0` 标出来，UI 要能把它们与真实厂商分开呈现。
 *
 * `isReal` 保持 **0 / 1 数字**而不是布尔：接口与库表都用 0/1
 * （`is_real=0` / `enabled=1`），这里再翻一层布尔只会多一次转换，
 * 而管理表格要按同一套口径去比大小。
 */
export function toModelItem(raw) {
  const row = raw && typeof raw === 'object' ? raw : {}
  return {
    id: str(row.id),
    label: str(row.label),
    baseUrl: str(row.base_url),
    defaultModel: str(row.default_model),
    models: Array.isArray(row.models) ? row.models.map(str) : [],
    hint: str(row.hint),
    isReal: Number(row.is_real) ? 1 : 0,
  }
}

/**
 * 拆响应体并逐条映射。**形状不对就抛**，不返回空清单。
 *
 * 「清单是空的」和「接口没按约定返回」是两件事：后者静默变成空数组的话，
 * 页面上只剩一个不能用的下拉，用户没有任何线索知道该刷新还是该报错。
 */
export function toItems(data, map = toModelItem) {
  if (!data || !Array.isArray(data.items)) {
    throw new Error('模型清单响应形状不对')
  }
  return data.items.map(map)
}

/**
 * 公开的可用厂商清单（仅 `enabled = 1` 的行）。**任何人可读**：
 * BYOK 面板要给普通用户渲染厂商下拉，而普通用户调管理端点只会拿到 403
 * —— 那样「消除漂移」就成了「普通用户的厂商下拉没了」。
 *
 * 失败时**不吞异常**：由调用方决定怎么告诉用户。空清单会让用户以为
 * 「平台不支持自带 Key」。
 *
 * @returns {Promise<{ items: object[] }>} items 已转成 camelCase
 */
export async function fetchPublicModelCatalog() {
  const res = await axios.get('/api/models')
  return { items: toItems(res.data) }
}
