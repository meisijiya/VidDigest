import axios from 'axios'

function client() {
  const token = localStorage.getItem('auth_token')
  return axios.create({
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
}

/**
 * 社区列表。**任何人都能看**，未登录同样可用——所以这里刻意不因为
 * 没有 token 就跳过请求。翻页与按标签筛选同属浏览，未登录也开放。
 */
export async function fetchCommunityVideos({ page = 1, pageSize = 20, tag = '' } = {}) {
  const res = await client().get('/api/community/videos', {
    params: { page, page_size: pageSize, tag },
  })
  return res.data
}

/**
 * 社区搜索：视频名称关键词 / 视频链接精确定位 / 标签。**需登录**。
 *
 * 401 在这里被翻译成 needLogin 标记返回，而不是抛异常：
 * 「没登录」是这个页面的常态之一（列表照样能看），不该和「搜索失败」
 * 一起变成一个红色报错框。
 */
export async function searchCommunity({ q = '', tag = '', page = 1, pageSize = 20 } = {}) {
  try {
    const res = await client().get('/api/community/search', {
      params: { q, tag, page, page_size: pageSize },
    })
    return { ...res.data, needLogin: false }
  } catch (err) {
    if (err.response?.status === 401) return { items: [], total: 0, needLogin: true }
    throw err
  }
}

/** 回填社区卡片的标题与封面（解析成功后调用一次）。需登录。 */
export async function publishCommunityCard(payload) {
  const res = await client().post('/api/community/cards', payload)
  return res.data
}
