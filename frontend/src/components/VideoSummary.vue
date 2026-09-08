<template>
  <div class="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden card-hover">
    <!-- Tab 导航 -->
    <div class="flex border-b border-gray-100 bg-gray-50/30">
      <button v-for="tab in tabs" :key="tab.key"
        @click="activeTab = tab.key"
        :class="[
          'flex-1 py-3.5 text-sm font-medium transition-all duration-200 relative',
          activeTab === tab.key
            ? 'text-blue-600 bg-white'
            : 'text-gray-400 hover:text-gray-600 hover:bg-gray-50/50'
        ]">
        <span class="flex items-center justify-center gap-1.5">
          <component :is="tab.icon" v-if="tab.icon" class="w-4 h-4" />
          {{ tab.label }}
        </span>
        <div v-if="activeTab === tab.key"
          class="absolute bottom-0 left-3 right-3 h-0.5 bg-gradient-to-r from-blue-600 to-indigo-500 rounded-full"></div>
      </button>
    </div>

    <!-- Tab 内容 -->
    <div class="p-5 min-h-[200px]">
      <!-- 空状态 -->
      <div v-if="!videoUrl" class="flex flex-col items-center justify-center py-12 text-gray-400">
        <svg class="w-12 h-12 mb-3 text-gray-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
        </svg>
        <p class="text-sm">⬆ 粘贴视频链接开始解析</p>
      </div>

      <!-- AI 解析待启动：不自动消耗请求次数，由用户手动触发 -->
      <div v-else-if="!started" class="flex flex-col items-center justify-center py-14 text-center animate-fade-in">
        <div class="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500/10 to-indigo-500/10 flex items-center justify-center mb-4">
          <svg class="w-7 h-7 text-blue-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"/>
            <path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15z"/>
          </svg>
        </div>
        <h3 class="text-base font-semibold text-gray-800 mb-1.5">AI 智能解析</h3>
        <p class="text-sm text-gray-400 max-w-sm leading-relaxed">解析视频字幕，生成总结摘要、思维导图，并支持针对视频内容的 AI 问答</p>
        <p class="text-xs text-gray-300 mt-1.5 mb-6">仅下载视频的话，无需启动此功能</p>
        <button @click="startSummarize"
          class="px-6 py-2.5 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 text-white text-sm font-medium
                 hover:shadow-lg hover:shadow-blue-500/25 transition-all duration-200 active:scale-95
                 flex items-center gap-2">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
          </svg>
          开始 AI 解析
        </button>
      </div>

      <!-- 总结摘要 -->
      <div v-else-if="activeTab === 'summary'" class="prose prose-slate max-w-none prose-headings:text-gray-900 prose-headings:font-semibold prose-p:text-gray-600 prose-p:leading-relaxed prose-strong:text-gray-900">
        <div v-if="noSubtitle" class="flex flex-col items-center justify-center py-10 text-gray-400">
          <svg class="w-10 h-10 mb-2 text-gray-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          <p class="text-sm">该视频没有可用字幕，无法生成总结</p>
        </div>
        <div v-else-if="errorMsg" class="flex items-center gap-3 text-red-500 bg-red-50 rounded-xl px-4 py-3 text-sm">{{ errorMsg }}</div>
        <div v-else-if="!summaryMd && loading" class="space-y-3">
          <div class="skeleton h-5 w-3/4"></div>
          <div class="skeleton h-4 w-full"></div>
          <div class="skeleton h-4 w-5/6"></div>
          <div class="skeleton h-4 w-2/3"></div>
          <div class="text-center text-gray-400 text-sm mt-4 animate-pulse">AI 正在分析视频内容...</div>
        </div>
        <div v-else v-html="renderedSummary" class="summary-content animate-fade-in"></div>
      </div>

      <!-- 字幕文本 -->
      <div v-else-if="activeTab === 'subtitle'" class="text-sm text-gray-600">
        <div v-if="errorMsg" class="flex items-center gap-3 text-red-500 bg-red-50 rounded-xl px-4 py-3 text-sm">{{ errorMsg }}</div>
        <div v-else-if="!subtitleData && loading" class="space-y-2">
          <div v-for="n in 6" :key="n" class="skeleton h-4" :style="{ width: `${60 + Math.random() * 30}%` }"></div>
          <div class="text-center text-gray-400 text-sm mt-4 animate-pulse">提取字幕中...</div>
        </div>
        <div v-else-if="subtitleData?.has_subtitle">
          <div class="flex items-center gap-2 text-xs text-gray-400 mb-3 bg-gray-50 rounded-lg px-3 py-2">
            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
            共 {{ subtitleData.segments?.length || 0 }} 条字幕
          </div>
          <!-- 结构化展示：每条字幕独立一行（代码解析，不经 LLM 处理） -->
          <div v-if="subtitleData.segments?.length" class="space-y-0.5">
            <p v-for="(seg, i) in subtitleData.segments" :key="i"
              class="leading-relaxed text-gray-600 px-1 rounded hover:bg-gray-50 transition-colors">
              {{ seg.text }}
            </p>
          </div>
          <p v-else class="whitespace-pre-wrap leading-relaxed">{{ subtitleData.full_text }}</p>
        </div>
        <div v-else class="flex flex-col items-center justify-center py-10 text-gray-400">
          <svg class="w-10 h-10 mb-2 text-gray-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          <p class="text-sm">该视频没有可用字幕</p>
        </div>
      </div>

      <!-- 思维导图 -->
      <div v-else-if="activeTab === 'mindmap'" class="w-full">
        <div v-if="noSubtitle" class="flex flex-col items-center justify-center py-10 text-gray-400">
          <svg class="w-10 h-10 mb-2 text-gray-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          <p class="text-sm">该视频没有可用字幕，无法生成思维导图</p>
        </div>
        <div v-else-if="errorMsg" class="flex items-center gap-3 text-red-500 bg-red-50 rounded-xl px-4 py-3 text-sm">{{ errorMsg }}</div>
        <div v-else-if="!mindmapMd && loading" class="space-y-3">
          <div class="skeleton h-5 w-1/2"></div>
          <div class="skeleton h-4 w-2/3 ml-4"></div>
          <div class="skeleton h-4 w-1/2 ml-4"></div>
          <div class="skeleton h-4 w-3/4 ml-8"></div>
          <div class="text-center text-gray-400 text-sm mt-4 animate-pulse">生成思维导图中...</div>
        </div>
        <div v-else-if="mindmapMd && mindmapRenderFailed" class="prose prose-slate max-w-none text-sm">
          <div class="flex items-center gap-2 text-yellow-600 bg-yellow-50 rounded-xl px-4 py-2.5 mb-4 text-xs">
            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            思维导图渲染失败，显示原始内容：
          </div>
          <pre class="whitespace-pre-wrap text-gray-600 bg-gray-50 rounded-xl p-4 text-xs">{{ mindmapMd }}</pre>
        </div>
        <!-- markmap 要求容器为 <svg> 元素，传 div 会导致图形静默不渲染 -->
        <svg v-else-if="mindmapMd" ref="markmapContainer" class="w-full h-[480px] animate-fade-in"></svg>
        <div v-else class="flex flex-col items-center justify-center py-10 text-gray-400">
          <svg class="w-10 h-10 mb-2 text-gray-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          <p class="text-sm">暂无思维导图</p>
        </div>
      </div>

      <!-- AI 问答 -->
      <div v-else-if="activeTab === 'chat'" class="space-y-4">
        <div class="flex gap-2">
          <div class="relative flex-1">
            <svg class="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            <input v-model="chatQuestion" type="text" placeholder="输入关于视频的问题..."
              class="w-full pl-10 pr-4 py-2.5 rounded-xl border border-gray-200 bg-gray-50/50 text-sm
                     focus:ring-2 focus:ring-blue-500 focus:border-transparent focus:bg-white
                     outline-none transition-all duration-200"
              @keyup.enter="handleChat" />
          </div>
          <button @click="handleChat" :disabled="chatLoading || !chatQuestion.trim()"
            class="px-5 py-2.5 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 text-white text-sm font-medium
                   hover:shadow-lg hover:shadow-blue-500/25 disabled:opacity-50 disabled:cursor-not-allowed
                   transition-all duration-200 active:scale-95 whitespace-nowrap flex items-center gap-2">
            <svg v-if="chatLoading" class="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
            </svg>
            {{ chatLoading ? '思考中...' : '提问' }}
          </button>
        </div>
        <div v-if="noSubtitle" class="flex flex-col items-center justify-center py-8 text-gray-400">
          <svg class="w-10 h-10 mb-2 text-gray-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          <p class="text-sm">该视频没有可用字幕，无法回答问题</p>
        </div>
        <div v-else-if="errorMsg" class="flex items-center gap-3 text-red-500 bg-red-50 rounded-xl px-4 py-3 text-sm">{{ errorMsg }}</div>
        <div v-else-if="chatAnswer" class="prose prose-slate max-w-none text-sm animate-fade-in">
          <div v-html="renderedChatAnswer"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { marked } from 'marked'
import { Transformer } from 'markmap-lib'
import { Markmap } from 'markmap-view'
import { summarizeVideo, chatWithVideo } from '../api/summarize.js'

// 单换行也渲染为换行，避免 LLM 输出被合并成一段
marked.setOptions({ gfm: true, breaks: true })

const props = defineProps({
  videoUrl: String,
  videoTitle: String,
  user: Object,
})

const activeTab = ref('summary')
const tabs = [
  { key: 'summary', label: '总结摘要', icon: null },
  { key: 'mindmap', label: '思维导图', icon: null },
  { key: 'subtitle', label: '字幕文本', icon: null },
  { key: 'chat', label: 'AI 问答', icon: null },
]

const loading = ref(false)
const started = ref(false)
const summaryMd = ref('')
const mindmapMd = ref('')
const subtitleData = ref(null)
const errorMsg = ref('')
const noSubtitle = ref(false)
const renderedSummary = computed(() => summaryMd.value ? marked(summaryMd.value) : '')
const markmapContainer = ref(null)
const mindmapRenderFailed = ref(false)

const chatQuestion = ref('')
const chatAnswer = ref('')
const chatLoading = ref(false)
const renderedChatAnswer = computed(() => chatAnswer.value ? marked(chatAnswer.value) : '')

let markmapInstance = null

/**
 * 清洗思维导图 Markdown，保证 markmap 可解析：
 * 1. 去除代码块围栏；2. 截掉第一个标题前的说明文字；
 * 3. 保证存在一级根标题；4. 无任何标题时按纯文本行兜底构造导图结构。
 */
function sanitizeMindmap(md) {
  let text = (md || '').trim()
  if (!text) return '# 视频思维导图\n- 暂无内容'

  // 1. 去除代码块围栏
  text = text.replace(/^```(?:markdown|md)?\s*\n?/i, '').replace(/\n?```\s*$/i, '')

  // 2. 截掉第一个任意级别标题之前的内容
  const headingMatch = text.match(/^#{1,6}\s+\S/m)
  if (headingMatch) {
    const idx = text.indexOf(headingMatch[0])
    text = text.slice(idx)
    // 3. 保证存在 `# ` 一级根标题
    if (!/^#\s+\S/m.test(text)) {
      const firstH2 = text.match(/^##\s+(.+)$/m)
      if (firstH2) {
        text = `# ${firstH2[1].trim()}\n${text.slice(text.indexOf(firstH2[0]) + firstH2[0].length)}`
      } else {
        text = `# 视频思维导图\n${text}`
      }
    }
    return text.trim()
  }

  // 4. 兜底：没有任何 Markdown 标题时，把非空文本行转为根节点下的列表
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean)
  const items = lines
    .map(l => l.replace(/^[-*•#\d.、\s]+/, '').trim())
    .filter(Boolean)
    .slice(0, 50)
    .map(l => `- ${l}`)
    .join('\n')
  return items ? `# 视频思维导图\n${items}` : '# 视频思维导图\n- 暂无内容'
}

function renderMindmap() {
  if (!markmapContainer.value || !mindmapMd.value) return
  mindmapRenderFailed.value = false
  try {
    if (markmapInstance) {
      markmapInstance.destroy()
      markmapInstance = null
    }
    markmapContainer.value.innerHTML = ''
    const transformer = new Transformer()
    const { root } = transformer.transform(sanitizeMindmap(mindmapMd.value))
    markmapInstance = Markmap.create(markmapContainer.value, {
      zoom: true,
      pan: true,
      maxWidth: 600,
    }, root)
  } catch (e) {
    console.error('Markmap render error:', e)
    mindmapRenderFailed.value = true
  }
}

watch(mindmapMd, (val) => {
  if (val && activeTab.value === 'mindmap') {
    nextTick(() => renderMindmap())
  }
})

watch(activeTab, (tab) => {
  if (tab === 'mindmap' && mindmapMd.value) {
    nextTick(() => renderMindmap())
  }
})

// 视频链接变化：仅重置状态，不自动发起 AI 请求（由用户点击"开始 AI 解析"手动触发）
watch(() => props.videoUrl, (newUrl) => {
  started.value = false
  loading.value = false
  summaryMd.value = ''
  mindmapMd.value = ''
  subtitleData.value = null
  errorMsg.value = ''
  noSubtitle.value = false
  chatAnswer.value = ''
})

function startSummarize() {
  if (!props.videoUrl || started.value) return
  started.value = true
  loading.value = true
  summaryMd.value = ''
  mindmapMd.value = ''
  subtitleData.value = null
  errorMsg.value = ''
  noSubtitle.value = false
  chatAnswer.value = ''

  summarizeVideo(props.videoUrl, 'zh', {
    onSubtitle: (data) => {
      subtitleData.value = data
    },
    onSummary: (token) => {
      summaryMd.value += token
    },
    onMindmap: (data) => {
      mindmapMd.value = data.markdown
    },
    onQuota: () => {},
    onError: (err) => {
      if (err.message.includes('没有可用的字幕')) {
        noSubtitle.value = true
      } else {
        errorMsg.value = err.message
      }
    },
    onDone: () => {
      loading.value = false
    },
  })
}

async function handleChat() {
  if (!chatQuestion.value.trim()) return
  chatLoading.value = true
  chatAnswer.value = ''
  await chatWithVideo(props.videoUrl, chatQuestion.value, subtitleData.value?.full_text || '', {
    onAnswer: (token) => {
      chatAnswer.value += token
    },
    onError: (err) => {
      chatAnswer.value = `错误：${err.message}`
    },
    onDone: () => {
      chatLoading.value = false
    },
  })
}
</script>
