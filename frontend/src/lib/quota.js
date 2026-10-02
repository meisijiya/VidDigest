/**
 * 额度展示逻辑。额度拆成「解析 / 追问」两个独立计数器（工单 #4）后，
 * 用户需要同时看到两个数字——只有一个数字的话，拆分对用户不可见。
 *
 * 抽成纯函数而非留在组件里：这样没有构建工具也能测，
 * 也不会让展示规则散落在 computed 与模板之间。
 */

function quotaOf(q, kind) {
  const v = q?.[kind]
  if (v && typeof v === 'object') {
    const remaining = Number(v.remaining)
    const limit = Number(v.limit)
    if (Number.isFinite(remaining) && Number.isFinite(limit)) {
      return { remaining, limit }
    }
  }
  return null
}

/** 兼容期回退：后端可能仍只给顶层 remaining / limit。 */
function fallbackQuota(q) {
  const remaining = Number(q?.remaining)
  const limit = Number(q?.limit)
  if (Number.isFinite(remaining) && Number.isFinite(limit)) {
    return { remaining, limit }
  }
  return null
}

function isUnlimited(slot) {
  return slot != null && slot.remaining === -1
}

/** 两个额度都取不到有效值时不硬拼文案，直接给占位符。 */
export function describeQuota(q) {
  if (!q) return '—'
  if (!q.logged_in) return '登录后可用'
  if (q.unlimited) return '无限次'

  const parse = quotaOf(q, 'parse')
  const chat = quotaOf(q, 'chat')

  if (!parse && !chat) {
    const fb = fallbackQuota(q)
    if (!fb) return '—'
    if (fb.remaining <= 0) return `今日解析已用完（0 / ${fb.limit}）`
    return `今日剩余 ${fb.remaining} / ${fb.limit} 次`
  }

  const parts = []
  if (parse) {
    parts.push(
      isUnlimited(parse)
        ? '解析 无限'
        : parse.remaining <= 0
          ? `解析 已用完（0 / ${parse.limit}）`
          : `解析 ${parse.remaining} / ${parse.limit}`,
    )
  }
  if (chat) {
    parts.push(
      isUnlimited(chat)
        ? '追问 无限'
        : chat.remaining <= 0
          ? `追问 已用完（0 / ${chat.limit}）`
          : `追问 ${chat.remaining} / ${chat.limit}`,
    )
  }
  return parts.join(' · ')
}

export function quotaBadgeClass(q) {
  if (!q || !q.logged_in) return 'bg-ink/60 text-gray-500'
  if (q.unlimited) return 'bg-amber-100 text-amber-700'

  const slots = [quotaOf(q, 'parse'), quotaOf(q, 'chat')].filter(Boolean)
  if (slots.length === 0) {
    const fb = fallbackQuota(q)
    if (!fb) return 'bg-ink/60 text-gray-500'
    return fb.remaining <= 0 ? 'bg-red-50 text-red-600' : 'bg-blue-50 text-blue-600'
  }

  // 任一额度用完就该警示：两个计数器都耗尽才是真正「用完」
  if (slots.some((s) => !isUnlimited(s) && s.remaining <= 0)) {
    return 'bg-red-50 text-red-600'
  }
  return 'bg-blue-50 text-blue-600'
}
