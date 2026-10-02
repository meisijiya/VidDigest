/**
 * 错误分类：把「链接不对」「网络问题」「服务端故障」分成不同提示。
 *
 * 为什么值得单独一个模块：用户看到「解析失败：解析失败: ...」时
 * 什么也做不了——他不知道是链接贴错了、被限流了、还是服务挂了。
 * 三种情况的下一步完全不同，提示也必须不同：
 *
 *   链接不对   → 让他重新检查链接（换个视频 / 确认是不是完整地址）
 *   限流/风控  → 让他等一会儿（平台在拦，不是他的错）
 *   网络/服务  → 让他重试
 *
 * 判据要能**变异**：只断言「出现了某种提示」的话，把分类逻辑整个
 * 删掉、恒返回第一个分支，它照样绿。所以下面每条分支都有对应用例。
 */

/** 从各种错误形状里把「人话」挖出来。 */
function messageOf(err) {
  const detail = err?.response?.data?.detail
  // 后端有两种 detail 形状：字符串，以及 {success, error}
  const raw = (detail && typeof detail === 'object' ? detail.error : detail)
    || err?.message || '未知错误'
  // 后端常把原因包在「解析失败: xxx」里，剥掉这层重复前缀，
  // 否则界面会显示「解析失败：解析失败: xxx」这种叠字。
  return String(raw).replace(/^(解析|下载)失败[:：]\s*/, '').trim() || '未知错误'
}

/** 链接本身有问题：不是有效的视频地址，或平台明确说不认识。 */
function looksLikeBadLink(err) {
  const m = messageOf(err).toLowerCase()
  if (/unsupported url|invalid url|not a valid|不支持|无效的?url|无法识别|unknown url/.test(m)) return true
  // yt-dlp 的典型报错：它认得这个域名，但上面没有视频
  if (/video unavailable|requested format is not available|no video|private video|稿件不可见|视频不存在|已失效/.test(m)) return true
  return false
}

/** 平台在拦：风控 / 限流 / 需要登录。这类**不是用户的错**。 */
function looksLikeThrottled(err) {
  const m = messageOf(err).toLowerCase()
  return /429|rate limit|too many|blocked|forbidden|风控|限流|频率|稍后重试|访问过于频繁/.test(m)
}

/** 网络层问题：连不上、超时。 */
function looksLikeNetwork(err) {
  if (err?.response) return false // 拿到了 HTTP 响应就不是网络问题
  const m = messageOf(err).toLowerCase()
  return /network|timeout|timed out|econnrefused|failed to fetch|load failed|网络|超时|连接/.test(m)
}

/**
 * 返回统一弹窗需要的 { title, message, hint }。
 * hint 是「用户下一步该做什么」——这是分类的真正目的。
 */
export function classifyError(err, action = 'parse') {
  const message = messageOf(err)

  if (looksLikeBadLink(err)) {
    return {
      title: '这个链接好像不对',
      message: message,
      hint: '请检查链接是否完整、是否还有效。B 站请用 '
          + 'bilibili.com/video/BV... 这样的完整地址，不要只贴一段文字。',
    }
  }

  if (looksLikeThrottled(err)) {
    return {
      title: '平台暂时拦住了',
      message: message,
      hint: '这不是你的问题——视频站对频繁请求做了限制。等一两分钟再试就好，'
          + '换个视频通常也能立刻解析。',
    }
  }

  if (looksLikeNetwork(err)) {
    return {
      title: '连不上服务器',
      message: message,
      hint: '请检查网络后重试。如果一直失败，可能是本地服务没有启动。',
    }
  }

  // 兜底：拿不准就归到服务端问题，并说清可以重试。
  // 不用「未知错误」这种说法——用户看到它只会更困惑。
  return {
    title: action === 'download' ? '下载失败' : '解析失败',
    message: message,
    hint: '这通常是服务端临时的问题，可以重试一次。如果反复失败，'
        + '请换一个视频链接试试。',
  }
}
