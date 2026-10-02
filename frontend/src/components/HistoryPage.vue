<template>
  <div class="min-h-[calc(100vh-4rem)]">
    <div class="max-w-5xl mx-auto px-4 sm:px-6 py-8">
      <!-- 页头 -->
      <div class="flex items-center justify-between mb-6">
        <div class="flex items-center gap-3">
          <button @click="$emit('back')"
            class="w-9 h-9 rounded-xl bg-panel border border-line flex items-center justify-center
                   text-gray-500 hover:text-gray-800 hover:border-gray-300 transition-colors">
            <svg class="w-4.5 h-4.5" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/>
            </svg>
          </button>
          <div>
            <h1 class="text-xl font-bold text-gray-900">解析历史</h1>
            <p class="text-xs text-gray-400 mt-0.5">最近 30 条解析记录，点击可回填视频与 AI 结果</p>
          </div>
        </div>
        <button v-if="items.length" @click="confirmClearAll" :disabled="clearing"
          class="text-xs text-gray-400 hover:text-red-500 transition-colors px-3 py-1.5 rounded-lg hover:bg-red-50 disabled:opacity-50">
          {{ clearing ? '清空中...' : '清空全部' }}
        </button>
      </div>

      <!-- 加载骨架 -->
      <div v-if="loading" class="space-y-3">
        <div v-for="n in 5" :key="n" class="skeleton h-20 rounded-2xl"></div>
      </div>

      <!-- 空状态：像素母题已移到左上角 Logo（PixelLogo 组件自带错峰闪动），
           这里不再重复摆一个会跳的网格。 -->
      <div v-else-if="!items.length" class="flex flex-col items-center justify-center py-24 text-gray-400">
        <p class="text-sm mb-1">暂无解析历史</p>
        <p class="text-xs text-gray-400">完成一次视频解析后，记录会出现在这里</p>
      </div>

      <!-- 记录列表 -->
      <ul v-else class="space-y-3">
        <li v-for="item in items" :key="item.id">
          <div class="group bg-panel rounded-2xl border border-line hover:border-blue-200
                      transition-all duration-200 cursor-pointer overflow-hidden"
            @click="openRecord(item)">
            <div class="flex items-start gap-4 p-4">
              <!-- 封面：走本站代理，视频站普遍有防盗链，直连会返回一片灰。
                   取不到时回落到占位图标，而不是留一个破图。 -->
              <img v-if="item.cover_url" :src="proxyThumbnail(item.cover_url)"
                :alt="item.video_title || '视频封面'" loading="lazy"
                class="w-24 h-16 rounded-xl object-cover bg-panel border border-line flex-shrink-0" />
              <div v-else
                class="w-24 h-16 rounded-xl bg-blue-50 border border-line flex-shrink-0
                       flex items-center justify-center">
                <svg class="w-6 h-6 text-blue-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
                </svg>
              </div>
              <!-- 主体 -->
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <h3 class="text-sm font-semibold text-gray-800 truncate">{{ item.video_title || '未命名视频' }}</h3>
                  <span v-if="item.has_ai_result"
                    class="flex-shrink-0 text-[10px] font-pixel bg-blue-50 text-blue-400 border border-blue-100 px-1.5 py-0.5 rounded font-medium">AI</span>
                </div>
                <p class="text-xs font-pixel text-gray-400 truncate mt-1">{{ item.video_url }}</p>
                <p v-if="item.summary_preview" class="text-xs text-gray-500 mt-1.5 line-clamp-2 leading-relaxed">{{ item.summary_preview }}</p>
                <div class="flex items-center gap-3 mt-2 text-[11px] text-gray-400">
                  <span>{{ formatTime(item.updated_at) }}</span>
                  <span v-if="item.has_chat" class="text-gray-500">含问答记录</span>
                </div>
              </div>
              <!-- 删除 -->
              <button @click.stop="removeItem(item)" :disabled="deletingId === item.id"
                class="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center text-gray-400
                       hover:text-red-500 hover:bg-red-50 transition-colors disabled:opacity-50"
                title="删除记录">
                <svg v-if="deletingId !== item.id" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                <svg v-else class="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
              </button>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { fetchHistories, fetchHistoryDetail, deleteHistory } from '../api/history.js'

const emit = defineEmits(['back', 'open-record', 'error'])

const items = ref([])
const loading = ref(true)
const deletingId = ref(null)
const clearing = ref(false)

async function load() {
  loading.value = true
  try {
    items.value = await fetchHistories()
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

/**
 * 缩略图走本站代理：视频站普遍有防盗链，直连会返回一片灰。
 * 与 CommunityPage 同一套口径——两个列表都显示封面，不该一个能看一个不能。
 */
function proxyThumbnail(url) {
  if (!url) return ''
  if (url.startsWith('/')) return url
  return `/api/proxy/thumbnail?url=${encodeURIComponent(url)}`
}

async function openRecord(item) {
  try {
    const detail = await fetchHistoryDetail(item.id)
    emit('open-record', detail)
  } catch {
    emit('error', {
      title: '打不开这条记录',
      message: '加载记录详情失败，请重试。',
      hint: '可能是网络抖动。点「重试」或直接刷新页面再试一次。',
    })
  }
}

async function removeItem(item) {
  if (!confirm('确定删除这条解析记录吗？')) return
  deletingId.value = item.id
  try {
    await deleteHistory(item.id)
    items.value = items.value.filter(i => i.id !== item.id)
  } catch {
    emit('error', {
      title: '删除失败',
      message: '这条记录没能删掉，请重试。',
      hint: '通常是网络问题。刷新页面确认记录是否还在。',
    })
  } finally {
    deletingId.value = null
  }
}

async function confirmClearAll() {
  if (!confirm(`确定清空全部 ${items.value.length} 条解析记录吗？此操作不可恢复。`)) return
  clearing.value = true
  try {
    await Promise.all(items.value.map(i => deleteHistory(i.id)))
    items.value = []
  } catch {
    emit('error', {
      title: '清空失败',
      message: '没能清空全部记录，部分可能已经删掉了。',
      hint: '列表已重新加载，可以看看还剩哪些。',
    })
    await load()
  } finally {
    clearing.value = false
  }
}

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts.includes('T') ? ts : ts.replace(' ', 'T') + 'Z')
  if (Number.isNaN(d.getTime())) return ts
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`
  return d.toLocaleDateString('zh-CN')
}

onMounted(load)
</script>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
