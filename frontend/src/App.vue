<template>
  <div class="min-h-screen flex flex-col">
    <AppHeader
      :user="currentUser"
      :page="currentPage"
      @login="showAuthModal('login')"
      @register="showAuthModal('register')"
      @logout="handleLogout"
      :show-vip-entry="membershipEnabled"
      @go-home="currentPage = 'home'"
      @open-history="currentPage = 'history'"
    />
    <main class="flex-1 pt-16">
      <template v-if="currentPage === 'home'">
      <HeroSection
        @parse="handleParse"
        :loading="loading"
        :compact="!!videoData"
        :showSlogan="!videoData || demoMode"
      />

      <!-- 视频结果区域 -->
      <Transition name="slide-up">
        <section v-if="videoData" class="py-6 sm:py-10">
          <div class="max-w-7xl mx-auto px-4 sm:px-6">
            <!-- 缓存复用提示 + 重新解析 -->
            <div v-if="fromCache"
              class="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs rounded-xl px-4 py-2.5 bg-teal-50 border border-teal-100 text-teal-300">
              <svg class="w-4 h-4 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
              </svg>
              <span>社区里已有这一份总结，直接复用，不用重复解析也不扣次数</span>
              <button @click="reparse" :disabled="reparseLoading"
                class="ml-auto px-3 py-1 rounded-lg bg-panel border border-teal-200 text-teal-300 font-medium
                       hover:bg-teal-500 hover:text-ink hover:border-teal-500 disabled:opacity-50
                       transition-all duration-200 active:scale-95 flex items-center gap-1.5">
                <svg :class="['w-3.5 h-3.5', reparseLoading && 'animate-spin']" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>
                </svg>
                {{ reparseLoading ? '重新解析中...' : '重新解析' }}
              </button>
            </div>
            <div class="flex flex-col lg:flex-row gap-6 lg:gap-8">
              <div class="w-full lg:w-2/5 lg:max-w-[420px] lg:flex-shrink-0">
                <VideoResult
                  :video="videoData"
                  :sourceUrl="currentUrl"
                  :downloading="downloading"
                  @download="handleDownload"
                />
              </div>
              <div class="flex-1 min-w-0">
                <VideoSummary
                  :videoUrl="currentUrl"
                  :videoTitle="videoData.title"
                  :videoData="videoData"
                  :initialHistory="historyDetail"
                  :key="summaryKey"
                  :user="currentUser"
                />
              </div>
            </div>
          </div>
        </section>
      </Transition>

      <!-- 营销区域（仅在无视频数据时展示） -->
      <template v-if="!videoData || demoMode">
        <button @click="currentPage = 'community'"
          class="block mx-auto mt-6 px-5 py-2.5 rounded-xl bg-panel border border-line
                 text-sm text-gray-600 hover:border-blue-200 hover:text-blue-500
                 transition-colors">浏览社区 →</button>
        <FeatureSection />
        <HowToSection />
        <ComparisonSection />
        <PricingSection
          v-if="membershipEnabled"
          @open-vip="handleOpenVip"
          @need-login="showAuthModal('login')"
        />
        <PlatformSection />
      </template>
      </template>

      <!-- 社区页：列表与标签筛选对访客开放，搜索与详情需登录 -->
      <CommunityPage v-else-if="currentPage === 'community'"
        @back="currentPage = 'home'"
        @need-login="showAuthModal('login')"
        @open-video="openCommunityVideo"
      />

      <!-- 解析历史页 -->
      <HistoryPage v-else @back="currentPage = 'home'" @open-record="handleOpenRecord" />
    </main>

    <AppFooter />
    <AuthModal
      :visible="authModalVisible"
      :initialMode="authModalMode"
      @close="authModalVisible = false"
      @success="handleAuthSuccess"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { parseVideo, downloadViaServer } from './api/video.js'
import { getSavedUser, fetchMe, logout as logoutApi, isLoggedIn } from './api/auth.js'
import { createCheckoutSession } from './api/payment.js'
import { fetchHistoryByUrl, saveHistory } from './api/history.js'
import { publishCommunityCard } from './api/community.js'

import AppHeader from './components/AppHeader.vue'
import HeroSection from './components/HeroSection.vue'
import VideoResult from './components/VideoResult.vue'
import VideoSummary from './components/VideoSummary.vue'
import FeatureSection from './components/FeatureSection.vue'
import HowToSection from './components/HowToSection.vue'
import ComparisonSection from './components/ComparisonSection.vue'
import PricingSection from './components/PricingSection.vue'
import { MEMBERSHIP_ENABLED as membershipEnabled } from './config/features.js'
import PlatformSection from './components/PlatformSection.vue'
import HistoryPage from './components/HistoryPage.vue'
import CommunityPage from './components/CommunityPage.vue'
import AuthModal from './components/AuthModal.vue'
import AppFooter from './components/AppFooter.vue'

const currentUser = ref(getSavedUser())
const authModalVisible = ref(false)
const authModalMode = ref('login')

const currentPage = ref('home')
const loading = ref(false)
const downloading = ref(false)
const videoData = ref(null)
const currentUrl = ref('')
const summaryKey = ref(0)
const demoMode = ref(true)
const historyDetail = ref(null)
const fromCache = ref(false)
const reparseLoading = ref(false)

/** URL 规范化：剥离跟踪参数，提取平台视频 ID 作为历史/缓存的 key，提升命中率 */
function canonicalUrl(raw) {
  let url = (raw || '').trim()
  try {
    const u = new URL(url)
    // B 站：以 BV/av 号为 key
    const bv = url.match(/(BV[0-9A-Za-z]{10})/)
    if (bv && u.hostname.includes('bilibili')) return `https://www.bilibili.com/video/${bv[1]}`
    if (u.hostname.includes('bilibili')) {
      const av = url.match(/av(\d+)/)
      if (av) return `https://www.bilibili.com/video/av${av[1]}`
    }
    // YouTube：以 v 参数 / youtu.be 短链为 key
    if (u.hostname.includes('youtube.com') && u.searchParams.get('v')) {
      return `https://www.youtube.com/watch?v=${u.searchParams.get('v')}`
    }
    if (u.hostname === 'youtu.be') {
      return `https://www.youtube.com/watch?v=${u.pathname.slice(1)}`
    }
    // 其他平台：仅剥离跟踪查询参数
    const keep = new URLSearchParams()
    for (const [k, v] of u.searchParams) {
      if (!/^(utm_|track|trackid|spm|vd_source|from|request_id)/i.test(k)) keep.append(k, v)
    }
    const qs = keep.toString()
    return `${u.origin}${u.pathname}${qs ? '?' + qs : ''}`
  } catch { return url }
}

/** 解析完成（或缓存命中）后落库：无论是否 AI 解析都进入历史记录 */
function persistParseRecord(url, data) {
  if (!isLoggedIn()) return
  saveHistory({
    url,
    video_title: data?.title || '',
    video_data: data || null,
  })
}

async function handleParse(url) {
  loading.value = true
  videoData.value = null
  historyDetail.value = null
  fromCache.value = false
  summaryKey.value++
  const key = canonicalUrl(url)
  currentUrl.value = key
  try {
    // 问一句「社区里有没有这一份」，只用于提示，不用于取内容。
    //
    // 原来这里是命中就**直接返回**：不解析、不请求 AI，拿个人历史里的
    // summary_md 渲染。那条路径会永久绕过社区视频表——社区里明明只有
    // 一份总结，有过个人历史的人看到的却是另一份（工单 #7 顺带修）。
    // 现在它只决定要不要显示「社区已有」这条提示；内容一律由
    // VideoSummary 请求 /api/summarize 拿，那条路读的就是社区视频表。
    if (isLoggedIn()) {
      try {
        fromCache.value = !!(await fetchHistoryByUrl(key))
      } catch {
        fromCache.value = false
      }
    }
    // 视频源信息（标题/封面/时长/格式）仍走 /api/parse：它不消耗额度，
    // 而且下载与时长这些字段只有解析结果里有，社区卡片不存。
    const res = await parseVideo(url)
    if (res.success) {
      videoData.value = res.data
      demoMode.value = false
      persistParseRecord(key, res.data)
      // 回填社区卡片的标题与封面：社区列表要显示它们，而服务端解析时
      // 拿不到（字幕流里没有平台标题与缩略图地址）。失败不影响主流程。
      publishCard(key, res.data)
    } else {
      alert('解析失败：' + (res.error || '未知错误'))
    }
  } catch (err) {
    const msg = err.response?.data?.detail?.error || err.response?.data?.detail || err.message
    alert('解析失败：' + msg)
  } finally {
    loading.value = false
  }
}

/** 回填社区卡片展示信息（静默失败：卡片少个封面不该打断解析流程） */
function publishCard(url, data) {
  if (!isLoggedIn()) return
  publishCommunityCard({
    url,
    video_title: data?.title || '',
    cover_url: data?.thumbnail || '',
  }).catch(() => {})
}

/**
 * 从社区点开一条视频。
 *
 * 详情需登录——但**判登录放在这里**，而不是让请求去撞 401：
 * 详情内容统一由 VideoSummary 请求 /api/summarize 拿（读的是社区视频表），
 * 未登录时那条请求会返回 SSE 的 need_login 错误事件。这里先拦一道，
 * 访客点卡片立刻得到登录框，而不是先白跑一次请求再看到报错。
 */
function openCommunityVideo(item) {
  if (!isLoggedIn()) {
    showAuthModal('login')
    return
  }
  handleParse(item.video_url)
}

/** 重新解析：强制走完整解析流程（社区那份仍然复用，不重复调模型） */
async function reparse() {
  if (!currentUrl.value || reparseLoading.value) return
  reparseLoading.value = true
  videoData.value = null
  historyDetail.value = null
  fromCache.value = false
  summaryKey.value++
  try {
    const res = await parseVideo(currentUrl.value)
    if (res.success) {
      videoData.value = res.data
      demoMode.value = false
      persistParseRecord(currentUrl.value, res.data)
      publishCard(currentUrl.value, res.data)
    } else {
      alert('解析失败：' + (res.error || '未知错误'))
    }
  } catch (err) {
    const msg = err.response?.data?.detail?.error || err.response?.data?.detail || err.message
    alert('解析失败：' + msg)
  } finally {
    reparseLoading.value = false
  }
}

/** 从历史页点击记录：回填视频源，并带上个人问答历史 */
function handleOpenRecord(detail) {
  currentPage.value = 'home'
  currentUrl.value = detail.video_url || ''
  videoData.value = detail.video_data || null
  // 只为了个人问答历史（VideoSummary 只读 chat_history 一个字段）。
  // 总结 / 思维导图 / 字幕一律由 /api/summarize 从社区视频表出，
  // 不再从个人记录回填——那正是让同一链接呈现两份总结的根因。
  historyDetail.value = detail
  summaryKey.value++
  if (!detail.video_data) {
    // 历史记录缺少视频源信息时，重新解析补全（不消耗 AI 次数）
    handleParse(detail.video_url)
    historyDetail.value = detail
  }
  window.scrollTo({ top: 0 })
}

async function handleDownload(formatId) {
  downloading.value = true
  try {
    const response = await downloadViaServer(currentUrl.value, formatId)
    const contentDisposition = response.headers['content-disposition']
    let filename = 'video.mp4'
    if (contentDisposition) {
      const match = contentDisposition.match(/filename\*?=(?:UTF-8'')?([^;\n]+)/i)
      if (match) filename = decodeURIComponent(match[1].replace(/"/g, ''))
    }
    const blob = new Blob([response.data])
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    window.URL.revokeObjectURL(url)
  } catch (err) {
    alert('下载失败：' + (err.message || '请稍后重试'))
  } finally {
    downloading.value = false
  }
}

function showAuthModal(mode) {
  authModalMode.value = mode
  authModalVisible.value = true
}

function handleAuthSuccess(user) {
  currentUser.value = user
}

function handleLogout() {
  logoutApi()
  currentUser.value = null
}

async function handleOpenVip() {
  if (!isLoggedIn()) {
    showAuthModal('login')
    return
  }
  try {
    const { checkout_url } = await createCheckoutSession('monthly')
    window.location.href = checkout_url
  } catch (err) {
    alert(err.response?.data?.detail || '创建支付失败')
  }
}

function checkPaymentResult() {
  const params = new URLSearchParams(window.location.search)
  if (params.get('payment') === 'success') {
    window.history.replaceState({}, '', window.location.pathname)
    if (isLoggedIn()) {
      setTimeout(async () => {
        try { currentUser.value = await fetchMe() } catch {}
      }, 1000)
    }
  }
}

onMounted(() => {
  checkPaymentResult()
})
</script>

<style>
/* 全局过渡动画 */
.slide-up-enter-active {
  transition: all 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}
.slide-up-leave-active {
  transition: all 0.3s ease-in;
}
.slide-up-enter-from {
  opacity: 0;
  transform: translateY(30px);
}
.slide-up-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}
</style>
