<template>
  <div class="min-h-[calc(100vh-4rem)]">
    <div class="max-w-4xl mx-auto px-4 sm:px-6 py-8">
      <!-- 页头 -->
      <div class="flex items-center gap-3 mb-6">
        <button @click="$emit('back')"
          class="w-9 h-9 rounded-xl bg-panel border border-line flex items-center justify-center
                 text-gray-500 hover:text-gray-800 hover:border-gray-300 transition-colors">
          <svg class="w-4.5 h-4.5" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/>
          </svg>
        </button>
        <div>
          <h1 class="text-xl font-bold text-gray-900">社区</h1>
          <p class="text-xs text-gray-400 mt-0.5">同一个视频全站只有一份总结，谁先解析谁说了算</p>
        </div>
      </div>

      <!-- 搜索：需登录。未登录时按钮可见但点了会提示登录——
           列表本来就不需要登录，把搜索入口藏起来反而让人以为没有搜索。 -->
      <form @submit.prevent="doSearch" class="flex flex-wrap items-center gap-2 mb-4">
        <div class="relative flex-1 min-w-[220px]">
          <svg class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>
          </svg>
          <input v-model="keyword" type="search"
            placeholder="搜视频名称关键词，或粘贴完整视频链接"
            class="w-full pl-9 pr-3 py-2 rounded-xl bg-panel border border-line text-sm
                   text-gray-800 placeholder-gray-400 focus:border-blue-200 focus:outline-none" />
        </div>
        <button type="submit" :disabled="searching"
          class="px-4 py-2 rounded-xl bg-blue text-on-primary text-sm font-medium
                 hover:bg-blue-600 transition-colors disabled:opacity-50">
          {{ searching ? '搜索中...' : '搜索' }}
        </button>
        <button v-if="activeQuery || activeTag" type="button" @click="clearSearch"
          class="px-3 py-2 rounded-xl text-xs text-gray-400 hover:text-gray-700
                 hover:bg-panel transition-colors">
          清除
        </button>
      </form>

      <!-- 搜索需登录：明确说清是登录问题，不是搜索坏了 -->
      <div v-if="needLogin" class="mb-4 flex items-center gap-3 rounded-xl px-4 py-2.5
           bg-amber-50 border border-amber-100 text-xs text-amber-700">
        <span>搜索与视频详情需要登录后使用</span>
        <button @click="$emit('need-login')"
          class="ml-auto px-3 py-1 rounded-lg bg-panel border border-amber-200 font-medium
                 hover:bg-amber-100 transition-colors">去登录</button>
      </div>

      <!-- 短查询必然召回 0（trigram 滑窗至少 3 字符）。
           把这个边界说出来，否则「搜不到」与「社区里没有」长得一模一样。 -->
      <div v-if="tooShort" class="mb-4 rounded-xl px-4 py-2.5 bg-amber-50 border
           border-amber-100 text-xs text-amber-700">
        关键词「{{ tooShort.q }}」不足 {{ tooShort.min }} 个字符，全文检索
        至少需要 {{ tooShort.min }} 个——补全一点再搜。
      </div>

      <!-- 标签筛选 -->
      <div v-if="tagOptions.length" class="flex flex-wrap items-center gap-2 mb-5">
        <button @click="selectTag('')"
          :class="['px-3 py-1.5 rounded-lg text-xs font-pixel transition-colors border',
                   activeTag === ''
                     ? 'bg-blue text-on-primary border-blue'
                     : 'bg-panel text-gray-500 border-line hover:border-gray-300']">
          全部
        </button>
        <button v-for="t in tagOptions" :key="t" @click="selectTag(t)"
          :class="['px-3 py-1.5 rounded-lg text-xs font-pixel transition-colors border',
                   activeTag === t
                     ? 'bg-blue text-on-primary border-blue'
                     : 'bg-panel text-gray-500 border-line hover:border-gray-300']">
          {{ t }}
        </button>
      </div>

      <!-- 加载骨架 -->
      <div v-if="loading" class="space-y-3">
        <div v-for="n in 4" :key="n" class="skeleton h-28 rounded-2xl"></div>
      </div>

      <!-- 真故障与「没有内容」分开呈现。放在空状态**之前**：
           否则一次 500 会被渲染成「社区还是空的」，用户会以为该换个
           关键词再搜，而真正的原因（网络 / 服务端故障）被吞掉了。 -->
      <div v-else-if="loadError" class="flex flex-col items-center justify-center py-24">
        <p class="text-sm mb-1 text-red-500">社区列表加载失败</p>
        <p class="text-xs text-gray-400">{{ loadError }}</p>
        <button @click="load" class="mt-4 px-3 py-1.5 rounded-lg bg-panel border border-line
                text-xs text-gray-500 hover:border-gray-300 transition-colors">重试</button>
      </div>

      <!-- 空状态 -->
      <div v-else-if="!items.length" class="flex flex-col items-center justify-center py-24 text-gray-400">
        <p class="text-sm mb-1">{{ emptyText }}</p>
        <p class="text-xs text-gray-400">{{ emptyHint }}</p>
      </div>

      <!-- 卡片列表：只渲染封面、标题、标签。列表响应体里本来就没有
           总结 / 字幕 / 思维导图——组件也**不去读**这些字段，
           免得后端哪天放宽了白名单而前端顺手就把它显示出来。

           单列铺满：之前是 sm:grid-cols-2，卡片挤在半宽里，
           大屏上右侧整片空白，看起来像没加载完。 -->
      <ul v-else class="grid gap-4">
        <li v-for="item in items" :key="item.id">
          <!-- 用 button 而不是 div：整张卡片就是一个可点区域，
               键盘能聚焦、回车能触发、屏幕阅读器会念成可点击。
               之前 div + @click 让卡片「看起来能点但点不动」——用户
               按了空白处没反应，只能以为功能坏了。 -->
          <button type="button" @click="openDetail(item)"
            class="group w-full h-full text-left bg-panel rounded-2xl border border-line
                   hover:border-blue-200 hover:shadow-md transition-all duration-200
                   active:scale-[0.995] focus:outline-none focus-visible:ring-2
                   focus-visible:ring-blue-400 overflow-hidden">
            <div class="flex gap-4 p-4">
              <img v-if="item.cover_url" :src="proxyThumbnail(item.cover_url)"
                :alt="item.video_title || '视频封面'" loading="lazy"
                class="w-28 h-20 rounded-xl object-cover bg-panel border border-line flex-shrink-0" />
              <div v-else
                class="w-28 h-20 rounded-xl bg-blue-50 border border-line flex-shrink-0
                       flex items-center justify-center">
                <svg class="w-6 h-6 text-blue-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
                </svg>
              </div>
              <div class="flex-1 min-w-0">
                <h3 class="text-sm font-semibold text-gray-800 line-clamp-2 leading-snug">
                  {{ item.video_title || '未命名视频' }}
                </h3>
                <p class="text-xs font-pixel text-gray-400 truncate mt-1">{{ item.video_url }}</p>
                <div v-if="item.tags.length" class="flex flex-wrap gap-1.5 mt-2">
                  <span v-for="tag in item.tags" :key="tag"
                    class="text-[10px] font-pixel bg-blue-50 text-blue-400 border border-blue-100
                           px-1.5 py-0.5 rounded font-medium">{{ tag }}</span>
                </div>
              </div>
            </div>
          </button>
        </li>
      </ul>

      <!-- 翻页 -->
      <div v-if="totalPages > 1" class="flex items-center justify-center gap-3 mt-6">
        <button @click="goPage(page - 1)" :disabled="page <= 1 || loading"
          class="px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500
                 hover:border-gray-300 transition-colors disabled:opacity-40">上一页</button>
        <span class="text-xs text-gray-400 font-pixel">{{ page }} / {{ totalPages }}</span>
        <button @click="goPage(page + 1)" :disabled="page >= totalPages || loading"
          class="px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500
                 hover:border-gray-300 transition-colors disabled:opacity-40">下一页</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { fetchCommunityVideos, searchCommunity } from '../api/community.js'

const emit = defineEmits(['back', 'need-login', 'open-video'])

const items = ref([])
const total = ref(0)
const totalPages = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(true)
const searching = ref(false)
const needLogin = ref(false)

/**
 * 加载失败的原因。与 items 为空**必须**是两回事：
 * 把 500 渲染成「社区还是空的」，用户会以为社区没内容，
 * 于是反复换关键词——而真正的原因（网络、500）被吞掉了。
 */
const loadError = ref('')

/**
 * 短查询提示。全文检索用 trigram 滑窗，不足 3 个字符**必然**召回 0，
 * 所以后端会把这个边界一起返回（q_too_short / min_chars）。
 * 不说的话，「搜机器」和「社区里没有机器」在界面上完全一样。
 */
const tooShort = ref(null)

/** 生效中的检索条件：空串表示未按该维度筛选。 */
const activeQuery = ref('')
const activeTag = ref('')
const keyword = ref('')

/** 标签选项由当前页的卡片汇总而来，不额外维护一份标签词表。 */
const tagOptions = computed(() => {
  const seen = new Set()
  for (const item of items.value) for (const t of item.tags) seen.add(t)
  return [...seen]
})

const emptyText = computed(() => (activeQuery.value || activeTag.value ? '没有匹配的内容' : '社区还是空的'))
const emptyHint = computed(() => (activeQuery.value || activeTag.value
  ? '换个关键词，或按标签浏览'
  : '第一个解析视频的人会把它放进来'))

/**
 * 缩略图走本站代理：视频站普遍有防盗链，直连会返回一片灰。
 * 封面地址来自社区卡片（可被回填），因此仍然经代理而不是浏览器直取。
 */
function proxyThumbnail(url) {
  if (!url) return ''
  if (url.startsWith('/')) return url
  return `/api/proxy/thumbnail?url=${encodeURIComponent(url)}`
}

async function load() {
  loading.value = true
  loadError.value = ''
  tooShort.value = null
  try {
    const res = await searchCommunity({
      q: activeQuery.value, tag: activeTag.value, page: page.value, pageSize,
    })
    if (res.needLogin) {
      // 未登录：搜索不可用，退回公开列表。
      // 搜索框对访客仍然可见，列表也仍然能翻——只有搜索被挡住。
      needLogin.value = true
      activeQuery.value = ''
      keyword.value = ''
      const list = await fetchCommunityVideos({
        page: page.value, pageSize, tag: activeTag.value,
      })
      applyList(list)
      return
    }
    needLogin.value = false
    applyList(res)
    if (res.q_too_short) tooShort.value = { q: activeQuery.value, min: res.min_chars }
  } catch (e) {
    // 不再静默变成空列表：故障要说出来，否则用户分不清
    // 「社区没内容」和「这次请求挂了」。
    loadError.value = (e && e.message) || '加载失败'
    items.value = []
    total.value = 0
    totalPages.value = 0
  } finally {
    loading.value = false
  }
}

function applyList(res) {
  items.value = res.items || []
  total.value = res.total || 0
  totalPages.value = res.total_pages || 0
  page.value = res.page || 1
}

async function doSearch() {
  searching.value = true
  activeQuery.value = keyword.value.trim()
  page.value = 1
  await load()
  searching.value = false
}

function clearSearch() {
  keyword.value = ''
  activeQuery.value = ''
  activeTag.value = ''
  page.value = 1
  load()
}

function selectTag(tag) {
  activeTag.value = tag
  page.value = 1
  load()
}

function goPage(n) {
  if (n < 1 || (totalPages.value && n > totalPages.value)) return
  page.value = n
  load()
}

/**
 * 点卡片进详情。
 *
 * 这里**不**自己去拉详情，也不预判登录状态——那是 App.vue 的职责
 * （它掌握登录态与 AuthModal）。本组件只负责把这条视频交出去。
 *
 * 详情内容一律走 /api/summarize：它读的就是社区视频表（工单 #6），
 * 社区里已有结果时原样回放，不调模型也不扣额度；还没有才真正解析。
 * 因此不存在「详情从个人历史取」的第二条路径。
 */
function openDetail(item) {
  emit('open-video', item)
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
