<template>
  <div class="bg-panel rounded-2xl border border-line overflow-hidden card-hover">
    <!-- Tab 导航 -->
    <div class="flex border-b border-line bg-panel-2/50">
      <button v-for="tab in tabs" :key="tab.key"
        @click="activeTab = tab.key"
        :class="[
          'flex-1 py-3.5 text-sm font-medium transition-all duration-200 relative',
          activeTab === tab.key
            ? 'text-blue-400 bg-panel'
            : 'text-gray-500 hover:text-gray-700 hover:bg-panel-2/50'
        ]">
        <span class="flex items-center justify-center gap-1.5">
          <component :is="tab.icon" v-if="tab.icon" class="w-4 h-4" />
          {{ tab.label }}
        </span>
        <div v-if="activeTab === tab.key"
          class="absolute bottom-0 left-3 right-3 h-0.5 bg-violet rounded-full"></div>
      </button>
    </div>

    <!-- 额度显式 + 停止入口：常驻可见，停止按钮在整个流式期间都可用 -->
    <div v-if="videoUrl" class="flex items-center justify-between gap-3 px-5 py-2 border-b border-line bg-panel-2/30">
      <span class="text-xs text-gray-500">每日免费额度</span>
      <div class="flex items-center gap-2">
        <span class="text-xs font-medium px-2 py-0.5 rounded-full" :class="quotaBadgeClass">{{ quotaLabel }}</span>
        <button v-if="loading || chatLoading" @click="loading ? stopSummarize() : stopChat()"
          class="px-2 py-0.5 rounded-full border border-line text-xs text-gray-500
                 hover:text-gray-900 hover:bg-gray-100 transition-all duration-200 active:scale-95">
          停止
        </button>
      </div>
    </div>

    <!-- 视频标签：属于视频的属性而不是总结的内容，所以挂在面板常驻行（切 Tab 都在）。
         数组为空时不渲染容器，只留 v-if 在容器上、不与 v-for 同元素。 -->
    <div v-if="videoTags.length" class="flex flex-wrap items-center gap-1.5 px-5 py-2 border-b border-line bg-panel-2/30">
      <span class="text-[10px] font-pixel text-gray-500">#TAGS</span>
      <span v-for="tag in videoTags" :key="tag"
        class="text-[10px] font-pixel bg-blue-50 text-blue-400 border border-blue-100 px-1.5 py-0.5 rounded">
        #{{ tag }}
      </span>
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
        <div class="w-14 h-14 rounded-2xl bg-blue-50 border border-line flex items-center justify-center mb-4">
          <svg class="w-7 h-7 text-blue-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"/>
            <path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15z"/>
          </svg>
        </div>
        <h3 class="text-base font-semibold text-gray-800 mb-1.5">AI 智能解析</h3>
        <p class="text-sm text-gray-500 max-w-sm leading-relaxed">解析视频字幕，生成总结摘要、思维导图，并支持针对视频内容的 AI 问答</p>
        <p class="text-xs text-gray-500 mt-1.5 mb-6">仅下载视频的话，无需启动此功能</p>
        <button @click="startSummarize"
          class="px-6 py-2.5 rounded-xl bg-violet text-white text-sm font-medium
                 hover:bg-blue-600 transition-all duration-200 active:scale-95
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
          <div class="flex flex-wrap items-center gap-2 text-xs text-gray-500 mb-3 bg-ink/60 border border-line rounded-lg px-3 py-2">
            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
            <span>共 {{ subtitleData.segments?.length || 0 }} 条字幕</span>
            <!-- 导出字幕 -->
            <div class="ml-auto flex items-center gap-1.5">
              <button v-if="subtitleData.segments?.length" @click="exportSubtitle('srt')"
                class="px-2.5 py-1 rounded-md bg-panel border border-line text-gray-500 hover:text-teal-300 hover:border-teal-200 transition-colors flex items-center gap-1 font-pixel"
                title="导出带时间轴的 SRT 字幕文件">
                <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                SRT
              </button>
              <button @click="exportSubtitle('txt')"
                class="px-2.5 py-1 rounded-md bg-panel border border-line text-gray-500 hover:text-teal-300 hover:border-teal-200 transition-colors flex items-center gap-1 font-pixel"
                title="导出纯文本字幕（每句一行）">
                <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                TXT
              </button>
            </div>
          </div>
          <!-- 结构化展示：每条字幕独立一行（代码解析，不经 LLM 处理） -->
          <div v-if="subtitleData.segments?.length" class="space-y-0.5">
            <p v-for="(seg, i) in subtitleData.segments" :key="i"
              class="leading-relaxed text-gray-600 px-1 rounded hover:bg-panel-2 transition-colors">
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
          <pre class="whitespace-pre-wrap text-gray-600 bg-ink/60 border border-line rounded-xl p-4 text-xs">{{ mindmapMd }}</pre>
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
          <!-- 问答历史收纳按钮 -->
          <button v-if="chatHistoryList.length" @click="showChatHistory = !showChatHistory"
            :class="[
              'flex-shrink-0 w-11 rounded-xl border flex items-center justify-center transition-all duration-200',
              showChatHistory
                ? 'bg-blue-50 border-blue-200 text-blue-400'
                : 'bg-ink/60 border-line text-gray-400 hover:text-gray-600 hover:bg-gray-100'
            ]"
            title="问答历史记录">
            <svg class="w-4.5 h-4.5" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>
            </svg>
          </button>
          <div class="relative flex-1">
            <svg class="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            <input v-model="chatQuestion" type="text" placeholder="输入关于视频的问题..."
              class="w-full pl-10 pr-4 py-2.5 rounded-xl border border-line bg-ink/60 text-sm text-gray-800
                     focus:ring-2 focus:ring-blue-500 focus:border-transparent
                     outline-none transition-all duration-200 placeholder:text-gray-500"
              @keyup.enter="handleChat" />
          </div>
          <button @click="handleChat" :disabled="chatLoading || !chatQuestion.trim()"
            class="px-5 py-2.5 rounded-xl bg-violet text-white text-sm font-medium
                   hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed
                   transition-all duration-200 active:scale-95 whitespace-nowrap flex items-center gap-2">
            <svg v-if="chatLoading" class="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
            </svg>
            {{ chatLoading ? '思考中...' : '提问' }}
          </button>
        </div>

        <!-- 问答历史抽屉 -->
        <Transition name="drawer">
          <div v-if="showChatHistory && chatHistoryList.length"
            class="rounded-2xl border border-line bg-panel-2/60 overflow-hidden">
            <div class="flex items-center justify-between px-4 py-2.5 border-b border-line">
              <div class="flex items-center gap-2 text-xs text-gray-500 font-medium">
                <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>
                </svg>
                问答历史（{{ chatHistoryList.length }}）
              </div>
              <button @click="showChatHistory = false" class="text-gray-400 hover:text-gray-600 transition-colors">
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
            <ul class="max-h-64 overflow-y-auto divide-y divide-line/60">
              <li v-for="(chat, i) in chatHistoryList" :key="i">
                <button @click="applyChatHistory(chat)"
                  class="w-full text-left px-4 py-3 hover:bg-panel transition-colors group">
                  <p class="text-xs font-medium text-gray-700 truncate group-hover:text-blue-400">
                    <span class="text-blue-400 mr-1 font-pixel">Q</span>{{ chat.question }}
                  </p>
                  <p class="text-[11px] text-gray-500 mt-1 truncate">
                    <span class="text-gray-500 mr-1 font-pixel">A</span>{{ chat.answer?.slice(0, 60) }}{{ (chat.answer || '').length > 60 ? '...' : '' }}
                  </p>
                </button>
              </li>
            </ul>
          </div>
        </Transition>

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
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { marked } from 'marked'
import { Transformer } from 'markmap-lib'
import { Markmap } from 'markmap-view'
import { summarizeVideo, chatWithVideo, fetchQuota } from '../api/summarize.js'
import { fetchChatSession, saveHistory } from '../api/history.js'
import { describeQuota, quotaBadgeClass as quotaBadgeClassOf } from '../lib/quota.js'

// 单换行也渲染为换行，避免 LLM 输出被合并成一段
marked.setOptions({ gfm: true, breaks: true })

const props = defineProps({
  videoUrl: String,
  videoTitle: String,
  videoData: Object,
  // 只用于回填**个人问答历史**，不再用于回填总结 / 思维导图 / 字幕。
  // 原因写在 setup 里那段注释处：那些内容只走 /api/summarize（社区视频表）。
  initialHistory: Object,
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
// 视频标签：一次解析产出的视频级属性，后端保证非空且已去重，这里只存不加工
const videoTags = ref([])
const errorMsg = ref('')
const noSubtitle = ref(false)
const renderedSummary = computed(() => summaryMd.value ? marked(summaryMd.value) : '')
const markmapContainer = ref(null)
const mindmapRenderFailed = ref(false)

const chatQuestion = ref('')
const chatAnswer = ref('')
const chatLoading = ref(false)
const renderedChatAnswer = computed(() => chatAnswer.value ? marked(chatAnswer.value) : '')
const chatHistoryList = ref([])
const showChatHistory = ref(false)

// 额度显式：只读展示，不消耗。解析与追问是两个独立计数器，各显示各的。
const quotaInfo = ref(null)
const quotaLabel = computed(() => describeQuota(quotaInfo.value))
const quotaBadgeClass = computed(() => quotaBadgeClassOf(quotaInfo.value))

async function refreshQuota() {
  try {
    quotaInfo.value = await fetchQuota()
  } catch {
    quotaInfo.value = null
  }
}

function applyQuotaEvent(d) {
  // SSE 的 quota 事件带完整的两个额度，整体替换而不是只取顶层字段——
  // 只留 remaining/limit 会把 parse/chat 丢掉，界面退回单数字。
  quotaInfo.value = {
    logged_in: true,
    unlimited: !!d.unlimited,
    remaining: d.remaining,
    limit: d.limit,
    parse: d.parse ?? null,
    chat: d.chat ?? null,
  }
}

// 进行中的流句柄，用于用户主动停止
let summaryStream = null
let chatStream = null

let markmapInstance = null

// 刻意**没有**「从个人历史回填 AI 结果」这条捷径（工单 #7 顺带修）。
//
// 原来这里是：拿到 initialHistory.id 就直接渲染自己那份 summary_md，
// 根本不请求 /api/summarize。后果是社区里明明只有一份总结，有过个人
// 历史的人却永远看不到它——同一链接在两个人屏幕上呈现两份不同内容，
// 与「无论谁先解析，看到的都是同一份」直接抵触。
//
// 现在唯一的详情数据源是 /api/summarize，它读的就是社区视频表：
// 已有结果原样回放（不调模型、不扣额度），没有才真正解析。
// 少一条路径，就少一处能让内容来源分叉的地方。
//
// 唯一还从个人记录里读的，是**问答历史**：追问本来就是按用户隔离的个人
// 对话，社区视频表里没有、也不该有它（ADR 0001 的两表分工）。
// 注意只读 chat_history 一个字段，summary / mindmap / subtitle 一律不碰。
if (Array.isArray(props.initialHistory?.chat_history)) {
  chatHistoryList.value = props.initialHistory.chat_history
}

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
      /* 深色面板适配：品牌四色按层级平涂 */
      color: (node) => ['#7C3AED', '#A855F7', '#EC4899', '#06B6D4'][node.state?.depth % 4] || '#A855F7',
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
  // 换视频时先中止上一个视频的流
  summaryStream?.cancel()
  chatStream?.cancel()
  started.value = false
  loading.value = false
  summaryMd.value = ''
  mindmapMd.value = ''
  subtitleData.value = null
  videoTags.value = []
  errorMsg.value = ''
  noSubtitle.value = false
  chatAnswer.value = ''
  chatHistoryList.value = []
  showChatHistory.value = false
})

function startSummarize() {
  if (!props.videoUrl || started.value) return
  started.value = true
  loading.value = true
  summaryMd.value = ''
  mindmapMd.value = ''
  subtitleData.value = null
  videoTags.value = []
  errorMsg.value = ''
  noSubtitle.value = false
  chatAnswer.value = ''
  chatHistoryList.value = []
  showChatHistory.value = false

  let failed = false
  let stopped = false
  summaryStream = summarizeVideo(props.videoUrl, 'zh', {
    onSubtitle: (data) => {
      subtitleData.value = data
    },
    onSummary: (token) => {
      summaryMd.value += token
    },
    onMindmap: (data) => {
      mindmapMd.value = data.markdown
    },
    onTags: (data) => {
      videoTags.value = data
    },
    onQuota: applyQuotaEvent,
    onCancel: () => {
      stopped = true
    },
    onError: (err) => {
      failed = true
      if (err.message.includes('没有可用的字幕')) {
        noSubtitle.value = true
      } else {
        errorMsg.value = err.message
      }
    },
    onDone: () => {
      // 断流 / 异常 / 主动取消都会走到这里（onDone 保证恰好一次）
      loading.value = false
      summaryStream = null
      // 必须在上面的 chatHistoryList 清空**之后**回填：
      // startSummarize 开头把列表清空了，早于它调就会白填一次。
      hydrateChatHistory()
      if (!failed && !stopped) {
        persistHistory()
      } else {
        // 出错或用户主动停止：额度可能已经扣掉，重新拉一次真实值
        refreshQuota()
      }
    },
  })
}

/**
 * 从服务端回填追问记录。
 *
 * 为什么必须由服务端给：追问记录按 (user_id, video_url) 存，**不依赖这个
 * 用户解析过这个视频**。B 追问 A 解析的视频时他并没有 parse_history 行，
 * 只读 initialHistory.chat_history 的话，刷新一次就再也看不到自己问过什么。
 *
 * 空结果不覆盖本地列表：服务端返回空说明这一轮没能落库（写失败或被拒），
 * 拿空列表盖掉用户刚看到的回答，比不同步更糟。
 */
async function hydrateChatHistory() {
  if (!props.user || !props.videoUrl) return
  try {
    const turns = await fetchChatSession(props.videoUrl)
    if (turns.length) chatHistoryList.value = turns
  } catch { /* 读不到就保持现状，不影响主流程 */ }
}

function stopSummarize() {
  summaryStream?.cancel()
}

/** AI 解析完成后保存到解析历史（仅登录用户；失败静默，不影响主流程） */
function persistHistory() {
  if (!props.user || !props.videoUrl) return
  if (!summaryMd.value && !mindmapMd.value) return
  saveHistory({
    url: props.videoUrl,
    video_title: props.videoTitle || '',
    video_data: props.videoData || null,
    summary_md: summaryMd.value,
    mindmap_md: mindmapMd.value,
    subtitle_data: subtitleData.value || null,
  })
}

async function handleChat() {
  if (!chatQuestion.value.trim() || chatStream) return
  chatLoading.value = true
  chatAnswer.value = ''
  const question = chatQuestion.value.trim()
  let failed = false
  let stopped = false
  const stream = chatWithVideo(props.videoUrl, question, {
    onAnswer: (token) => {
      chatAnswer.value += token
    },
    onQuota: applyQuotaEvent,
    onCancel: () => {
      stopped = true
    },
    onError: (err) => {
      failed = true
      chatAnswer.value = `错误：${err.message}`
    },
    onDone: () => {
      // 断流 / 异常 / 主动取消都会走到这里（onDone 保证恰好一次）
      chatLoading.value = false
      if (chatStream === stream) chatStream = null
      if (failed || stopped) {
        refreshQuota()
        return
      }
      // 组件内即时展示。持久化由服务端在答案产出时自己落（工单 #8）：
      // 前端不再回调保存接口——记录存不存在不该取决于浏览器有没有多发一个请求。
      chatHistoryList.value.push({ question, answer: chatAnswer.value })
      showChatHistory.value = false
      // 以服务端为准再拉一次：刷新后回来读的就是它，不依赖这次 push。
      hydrateChatHistory()
    },
  })
  chatStream = stream
  await stream.done
}

function stopChat() {
  chatStream?.cancel()
}

/** 点击历史记录行：回填问题与已保存的答复（不发起新 AI 请求，零消耗） */
function applyChatHistory(chat) {
  if (!chat?.question) return
  chatQuestion.value = chat.question
  chatAnswer.value = chat.answer || ''
  errorMsg.value = ''
  showChatHistory.value = false
}

/** 字幕导出：SRT（带时间轴）或 TXT（每句一行），纯前端生成下载 */
function exportSubtitle(format) {
  const segs = subtitleData.value?.segments || []
  const title = (props.videoTitle || 'subtitle').replace(/[\\/:*?"<>|]/g, '_').slice(0, 60)
  let content = ''
  let ext = format

  if (format === 'srt') {
    const ts = (s) => {
      const t = Math.max(0, Number(s) || 0)
      const h = String(Math.floor(t / 3600)).padStart(2, '0')
      const m = String(Math.floor((t % 3600) / 60)).padStart(2, '0')
      const sec = String(Math.floor(t % 60)).padStart(2, '0')
      const ms = String(Math.round((t % 1) * 1000)).padStart(3, '0')
      return `${h}:${m}:${sec},${ms}`
    }
    content = segs
      .map((seg, i) => `${i + 1}\n${ts(seg.start)} --> ${ts(seg.end || seg.start + 2)}\n${seg.text}`)
      .join('\n\n')
    if (!content) {
      // 无时间轴数据时降级为纯文本
      content = subtitleData.value?.full_text || ''
      ext = 'txt'
    }
  } else {
    content = segs.length ? segs.map((s) => s.text).join('\n') : (subtitleData.value?.full_text || '')
  }

  const blob = new Blob([`\ufeff${content}`], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${title}.${ext}`
  a.click()
  URL.revokeObjectURL(url)
}

// ── 生命周期 ──────────────────────────────────────────────
onMounted(() => {
  refreshQuota()
})

onUnmounted(() => {
  // 组件被卸载/换页时中止在途请求，避免往已销毁的组件上写状态
  summaryStream?.cancel()
  chatStream?.cancel()
  markmapInstance?.destroy?.()
  markmapInstance = null
})
</script>

<style scoped>
/* 思维导图深色适配：markmap 节点文字经 foreignObject 渲染，颜色取自 svg 上的
   --markmap-text-color 变量（库默认 #333，深色底不可见）。
   库默认样式定义于 .markmap（0,1,0），此处用 svg.markmap（0,1,1）提权覆盖。 */
:deep(svg.markmap) {
  --markmap-text-color: #dde2ee;
  --markmap-a-color: #22d3ee;
  --markmap-a-hover-color: #67e8f9;
  --markmap-code-bg: #1d2742;
  --markmap-code-color: #c4b5fd;
  --markmap-highlight-bg: rgba(124, 58, 237, 0.28);
  --markmap-highlight-node-bg: rgba(124, 58, 237, 0.14);
}
/* 问答历史抽屉展开/收起动画 */
.drawer-enter-active,
.drawer-leave-active {
  transition: all 0.25s ease;
  transform-origin: top;
}
.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
  transform: scaleY(0.9) translateY(-6px);
}
</style>
