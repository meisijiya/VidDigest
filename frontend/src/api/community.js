import axios from 'axios'

function client() {
  const token = localStorage.getItem('auth_token')
  return axios.create({
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
}

/**
 * 列表 / 搜索共用的查询参数。
 *
 * 空标签**不传**：未选中任何标签时应当退回公开浏览，而「不带 tag」与
 * 「带一个空 tag」在代码里是两回事——后者读起来像「筛选了一个叫空
 * 字符串的标签」。
 */
function browseParams({ page = 1, pageSize = 20, tag = '' } = {}) {
  const params = { page, page_size: pageSize }
  if (tag) params.tag = tag
  return params
}

/**
 * 社区列表。**任何人都能看**，未登录同样可用——所以这里刻意不因为
 * 没有 token 就跳过请求。翻页与按标签筛选同属浏览，未登录也开放。
 */
export async function fetchCommunityVideos({ page = 1, pageSize = 20, tag = '' } = {}) {
  const res = await client().get('/api/community/videos', {
    params: browseParams({ page, pageSize, tag }),
  })
  return res.data
}

/**
 * 社区里出现过的全部标签及条数。**任何人可读。**
 *
 * 标签选项必须来自这里，不能从当前页卡片汇总：后者随结果集变化——
 * 翻页或选中某个标签之后，其余标签会从筛选行里整片消失，用户没法
 * 再叠加第二个筛选。这份清单与服务端是同一份真值，取一次后常驻。
 */
export async function fetchCommunityTags() {
  const res = await client().get('/api/community/tags')
  return res.data.items || []
}

/**
 * 社区里有没有这一条，以及我能不能改写它。**需登录**。
 *
 * 判据必须是社区视频表（服务端事实），不能拿「我解析过这个视频」推断：
 * 陌生人打开一条别人解析的视频时，他自己的历史里当然没有这一条，
 * 那样判的结果是「社区里没有」——于是他看不到复用提示，
 * 「重新解析」按钮也永远不会出现。
 */
export async function fetchCommunityByUrl(url) {
  const res = await client().get('/api/community/videos/by-url', { params: { url } })
  return res.data
}

/**
 * 社区搜索：视频名称关键词 / 视频链接精确定位 / 标签。**需登录**。
 *
 * 401 在这里被翻译成 needLogin 标记返回，而不是抛异常：
 * 「没登录」是这个页面的常态之一（列表照样能看），不该和「搜索失败」
 * 一起变成一个红色报错框。
 *
 * tag 接受**逗号分隔的多值**，后端按并集处理（命中任一标签即命中）。
 */
export async function searchCommunity({ q = '', tag = '', page = 1, pageSize = 20 } = {}) {
  try {
    const res = await client().get('/api/community/search', {
      params: { q, ...browseParams({ page, pageSize, tag }) },
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
