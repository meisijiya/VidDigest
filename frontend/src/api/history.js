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

/**
 * 当前用户与某个视频的追问会话（只有他自己读得到）。
 * 没有记录时返回空数组——那是正常状态，不是错误。
 * 不在这里吞异常：读不到要能被上游看见，而不是变成一个静默的空列表。
 */
export async function fetchChatSession(url) {
  const res = await client().get('/api/history/chat', { params: { url } })
  return res.data.chat_history || []
}

/** 保存/更新解析历史（同一视频去重，空字段不覆盖已有数据） */
export async function saveHistory(payload) {
  try {
    await client().post('/api/history/save', payload)
  } catch { /* 历史保存失败不影响主流程 */ }
}
