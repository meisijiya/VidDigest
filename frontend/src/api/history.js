import axios from 'axios'

function client() {
  const token = localStorage.getItem('auth_token')
  return axios.create({
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
}

/**
 * 解析历史列表（分页 + 关键词 / 标签 / 仅收藏 / AI 状态）。
 *
 * 返回**整个信封**而不是 items：界面上的「共 N 条」和翻页都要用到 total。
 * 拿 items.length 顶替的话，最后一页会显示成「共 7 条」—— 而库里其实有
 * 25 条，只是它们在第二页。
 */
export async function fetchHistories(params = {}) {
  const res = await client().get('/api/history', { params })
  return res.data
}

/**
 * 标签筛选的选项（带计数）。
 *
 * 刻意不在前端从当前页汇总：那样一来翻页或一筛选，标签就会增减，
 * 用户读起来是「这个筛选不生效」。
 */
export async function fetchHistoryFacets() {
  const res = await client().get('/api/history/facets')
  return res.data.items || []
}

/**
 * 删除一条历史。
 *
 * 收藏项在**服务端**有第二道保险：不带 force 时回 409。这里把它翻译成
 * ``{ refused: true }`` 而不是抛异常 —— 「被拦住」不是故障，调用方要弹
 * 第二道确认。抛异常的话界面只会显示「删除失败」，而那条记录其实好好地
 * 还在列表里，用户会以为已经删了。
 */
export async function deleteHistory(id, { force = false } = {}) {
  try {
    await client().delete(`/api/history/${id}`, { params: { force } })
    return { refused: false }
  } catch (e) {
    const detail = e && e.response && e.response.data && e.response.data.detail
    if (e && e.response && e.response.status === 409 && detail && detail.code === 'favorited') {
      return { refused: true, message: detail.message }
    }
    throw e
  }
}

/**
 * 收藏 / 取消收藏。**幂等设置**，不是翻转。
 *
 * 翻转在网络重试下会把结果反过来：点一下收藏成功，重试一次就变成取消了。
 */
export async function setHistoryFavorite(id, isFavorite) {
  await client().patch(`/api/history/${id}/favorite`, { is_favorite: isFavorite })
}

/**
 * 清空历史。默认**保住收藏**，服务端也是这个默认。
 *
 * 不再是「前端循环调 N 次删除」：上限 1000 条时那是 1000 个请求，
 * 中途失败会留下一半删一半没删的列表，而用户看不出是哪一半。
 */
export async function clearHistories({ force = false } = {}) {
  const res = await client().delete('/api/history', { params: { force } })
  return res.data
}

/** 获取单条历史完整内容 */
export async function fetchHistoryDetail(id) {
  const res = await client().get(`/api/history/${id}`)
  return res.data
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
