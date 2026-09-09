import axios from 'axios'

function client() {
  const token = localStorage.getItem('auth_token')
  return axios.create({
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
}

/** 获取最近 30 条解析历史 */
export async function fetchHistories() {
  const res = await client().get('/api/history')
  return res.data.items
}

/** 获取单条历史完整内容 */
export async function fetchHistoryDetail(id) {
  const res = await client().get(`/api/history/${id}`)
  return res.data
}

/** 删除单条历史 */
export async function deleteHistory(id) {
  await client().delete(`/api/history/${id}`)
}

/** 按视频 URL 查历史记录（解析复用缓存；未命中返回 null） */
export async function fetchHistoryByUrl(url) {
  try {
    const res = await client().get('/api/history/by-url', { params: { url } })
    return res.data.item
  } catch {
    return null
  }
}

/** 保存/更新解析历史（同一视频去重，空字段不覆盖已有数据） */
export async function saveHistory(payload) {
  try {
    await client().post('/api/history/save', payload)
  } catch { /* 历史保存失败不影响主流程 */ }
}

/** 追加一条 AI 问答到解析历史 */
export async function saveChatToHistory(url, question, answer) {
  try {
    await client().post('/api/history/chat', { url, question, answer })
  } catch { /* 忽略 */ }
}
