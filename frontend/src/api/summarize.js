/**
 * SSE 流读取。
 *
 * 之前 summarizeVideo / chatWithVideo 各自内联了一份 30 行的读取循环，
 * 两份各有一份自己的 `currentEvent` —— 而它被声明在 while 循环体内，
 * 跨 chunk 时会丢事件名；同时 onDone 只在收到 [DONE] 时触发，
 * 断流/异常时前端永远等不到，loading 卡死。
 */

/**
 * SSE 行解析器。状态（未消费的半行 + 当前事件名）必须跨 feed 保持，
 * 因为 `event: x` 和 `data: {...}` 常落在不同的 chunk 里。
 */
export function createSseParser() {
  let buffer = ''
  let currentEvent = ''

  return {
    /** 喂入一段文本，返回本次已完整的行（最后半行留在 buffer 里等下个 chunk） */
    feed(text) {
      buffer += text
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      return lines
    },

    /** 消费一行。返回 { event, data }，非事件行返回 null */
    take(line) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim()
        return null
      }
      if (!line.startsWith('data: ')) return null

      const data = line.slice(6).trim()
      if (data === '[DONE]') return { event: 'done', data: null }
      try {
        return { event: currentEvent, data: JSON.parse(data) }
      } catch {
        return { event: currentEvent, data }  // 非 JSON 负载原样透传
      }
    },
  }
}

const TOKEN_KEY = 'auth_token'

/**
 * 读一个 SSE 端点。
 * onDone 保证被调用恰好一次：正常收到 [DONE]、流自然结束、抛异常、用户取消，四条路径都会走到。
 * 返回 { done, cancel }：done 是结束的 Promise，cancel 是用户主动停止。
 */
function streamSse(path, payload, { route, onError, onCancel, onDone }) {
  const parser = createSseParser()
  const controller = new AbortController()
  let cancelled = false
  let finished = false
  const finish = () => {
    if (finished) return
    finished = true
    onDone?.()
  }

  const done = (async () => {
    try {
      const token = localStorage.getItem(TOKEN_KEY)
      const res = await fetch(path, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      })

      const reader = res.body.getReader()
      const decoder = new TextDecoder()

      while (true) {
        const { done: ended, value } = await reader.read()
        if (ended) break

        for (const line of parser.feed(decoder.decode(value, { stream: true }))) {
          const ev = parser.take(line)
          if (!ev) continue
          if (ev.event === 'done') finish()
          else route(ev.event, ev.data)
        }
      }
    } catch (err) {
      // 主动取消不算错误：走 onCancel，让调用方区分「用户停了」和「出错了」
      if (cancelled || err?.name === 'AbortError') onCancel?.()
      else onError?.({ message: err.message })
    } finally {
      finish()  // 断流 / 异常 / 取消都收尾，UI 不会永久停在 loading
    }
  })()

  return {
    done,
    cancel() {
      if (finished) return
      cancelled = true
      controller.abort()
    },
  }
}

const SUMMARY_ROUTES = {
  subtitle: 'onSubtitle',
  summary: 'onSummary',
  mindmap: 'onMindmap',
  tags: 'onTags',
  quota: 'onQuota',
  error: 'onError',
}

const CHAT_ROUTES = {
  answer: 'onAnswer',
  quota: 'onQuota',
  error: 'onError',
}

export function summarizeVideo(url, language, callbacks) {
  return streamSse('/api/summarize', { url, language }, {
    route: (event, data) => callbacks[SUMMARY_ROUTES[event]]?.(data),
    onError: callbacks.onError,
    onCancel: callbacks.onCancel,
    onDone: callbacks.onDone,
  })
}

/**
 * 追问。不再传字幕全文——服务端从社区视频表取（工单 #8）。
 * 字幕全文在网络上白跑两趟没有意义，还让前端有机会篡改它。
 */
export function chatWithVideo(url, question, callbacks) {
  return streamSse('/api/chat', { url, question }, {
    route: (event, data) => callbacks[CHAT_ROUTES[event]]?.(data),
    onError: callbacks.onError,
    onCancel: callbacks.onCancel,
    onDone: callbacks.onDone,
  })
}

/** 当前剩余额度。只读，不消耗。 */
export async function fetchQuota() {
  const token = localStorage.getItem(TOKEN_KEY)
  const res = await fetch('/api/quota', {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) throw new Error('获取额度失败')
  return res.json()
}
