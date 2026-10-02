<template>
  <div class="min-h-screen flex flex-col">
    <AppHeader
      :user="currentUser"
      :page="currentPage"
      :quota="quotaInfo"
      :quota-loading="quotaLoading"
      @request-quota="refreshQuota"
      @login="showAuthModal('login')"
      @register="showAuthModal('register')"
      @logout="handleLogout"
      :show-vip-entry="membershipEnabled"
      @go-home="goHome"
      @open-history="openHistory"
      @open-community="openCommunity"
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
                  :hasCommunityResult="fromCache"
                />
              </div>
            </div>
          </div>
        </section>
      </Transition>

      <!-- 营销区域（仅在无视频数据时展示） -->
      <template v-if="!videoData || demoMode">
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
      <HistoryPage v-else @back="goHome" @open-record="handleOpenRecord" @error="showErrorFromPage" />
    </main>

    <AppFooter @go-home="goHome" />
    <AuthModal
      :visible="authModalVisible"
      :initialMode="authModalMode"
      @close="authModalVisible = false"
      @success="handleAuthSuccess"
    />
    <ErrorModal
      :visible="errorModal.visible"
      :title="errorModal.title"
      :message="errorModal.message"
      :hint="errorModal.hint"
      @close="errorModal.visible = false"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { parseVideo, downloadViaServer } from './api/video.js'
import { getSavedUser, fetchMe, logout as logoutApi, isLoggedIn } from './api/auth.js'
import { fetchQuota } from './api/summarize.js'
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
import ErrorModal from './components/ErrorModal.vue'
import { classifyError } from './lib/errors.js'

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
const errorModal = ref({ visible: false, title: '', message: '', hint: '' })

/** 顶栏额度面板的数据源。悬停时才拉，避免每次渲染都打一次接口。 */
const quotaInfo = ref(null)
const quotaLoading = ref(false)

async function refreshQuota() {
  if (quotaLoading.value) return
  quotaLoading.value = true
  try {
    quotaInfo.value = await fetchQuota()
  } catch {
    quotaInfo.value = null
  } finally {
    quotaLoading.value = false
  }
}

/** 统一错误出口：所有失败都走这里，不再散落 alert() */
function showError(err, action = 'parse') {
  const { title, message, hint } = classifyError(err, action)
  errorModal.value = { visible: true, title, message, hint }
}

/** 子组件已经组织好文案的错误，直接进同一个弹窗 */
function showErrorFromPage(payload) {
  errorModal.value = {
    visible: true,
    title: payload?.title || '出错了',
    message: payload?.message || '',
    hint: payload?.hint || '',
  }
}

/**
 * 回到起始页。
 *
 * 之前只改 currentPage，而 videoData 仍然留着——于是点了 Logo 之后
 * 页面确实切回了 home，但解析结果区还挂在下面，看起来像「没反应」。
 * 「回起始页」的含义是回到输入框，所以这里把解析态一并清掉。
 */
function goHome() {
  currentPage.value = 'home'
  videoData.value = null
  currentUrl.value = ''
  historyDetail.value = null
  fromCache.value = false
  summaryKey.value++
  window.scrollTo({ top: 0 })
}

/** 去历史页时同样清掉解析态：否则历史列表上方会压着上一个视频的结果 */
function openHistory() {
  currentPage.value = 'history'
  window.scrollTo({ top: 0 })
}

/**
 * 去社区页。**不清** videoData：社区和首页的输入框是并列入口，
 * 从社区点进某个视频详情时结果区还得在（openCommunityVideo 自己会处理）。
 * 与 openHistory 的差别是有意的。
 */
function openCommunity() {
  currentPage.value = 'community'
  window.scrollTo({ top: 0 })
}

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

/**
 * 请求发出之前的本地校验。
 *
 * 为什么要在前端拦一道：拼错链接、只贴了半句、粘了一整段分享文案
 * 这三种最常见，交给后端也要跑一趟网络才知道结果，用户白等几秒
 * 还只看到一句「解析失败」。本地判掉能立刻给出可执行的提示。
 *
 * 刻意**不**做过度校验（比如要求必须是已知平台）：本仓定位是
 * 支持 1800+ 平台的白名单判不准，在这里拦掉合法链接比不拦更糟。
 */
function validateUrlInput(raw) {
  const text = (raw || '').trim()
  if (!text) {
    return { ok: false, title: '还没填链接', message: '请先粘贴一个视频链接。',
      hint: 'B 站请用 bilibili.com/video/BV... 这样的完整地址。' }
  }
  // 从分享文本里抽第一个 http(s) 链接——和后端 clean_url 同一套思路。
  const m = text.match(/https?:\/\/[^\s）\)"'＞，。、；：！？》>\]]+/)
  if (!m) {
    return { ok: false, title: '这不是一个链接', message: text.slice(0, 80),
      hint: '没有找到 http:// 或 https:// 开头的地址。'
          + '如果你是整段复制了分享文案，请把其中的链接复制出来再试。' }
  }
  return { ok: true, url: m[0] }
}

async function handleParse(url) {
  const check = validateUrlInput(url)
  if (!check.ok) {
    errorModal.value = { visible: true, ...check }
    return
  }
  loading.value = true
  videoData.value = null
  historyDetail.value = null
  fromCache.value = false
  summaryKey.value++
  const key = canonicalUrl(check.url)
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
    const res = await parseVideo(check.url)
    if (res.success) {
      videoData.value = res.data
      demoMode.value = false
      persistParseRecord(key, res.data)
      // 回填社区卡片的标题与封面：社区列表要显示它们，而服务端解析时
      // 拿不到（字幕流里没有平台标题与缩略图地址）。失败不影响主流程。
      publishCard(key, res.data)
    } else {
      showError({ message: res.error || '未知错误' })
    }
  } catch (err) {
    showError(err)
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
  // 必须切回首页，否则社区页仍然盖在上面：请求发出去了、解析也成功了，
  // 用户看到的却还是社区列表——症状就是「点了卡片没反应」。
  currentPage.value = 'home'
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
      showError({ message: res.error || '未知错误' })
    }
  } catch (err) {
    showError(err)
  } finally {
    reparseLoading.value = false
  }
}

/** 从历史页点击记录：回填视频源，并带上个人问答历史 */
async function handleOpenRecord(detail) {
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
    await handleParse(detail.video_url)
    historyDetail.value = detail
    window.scrollTo({ top: 0 })
    return
  }
  // 问一句社区里有没有这一份，决定要不要让 VideoSummary 自动展示。
  // 必须在 summaryKey++ **之后**查：组件是靠 key 重建的，先查再改 key
  // 才能保证它拿到的是本次的结果而不是上一个视频的。
  // 查失败一律当作「没有」，退回手动触发——宁可多点一次，
  // 也不能在结果其实不存在时自动发起并扣掉额度。
  fromCache.value = false
  try {
    fromCache.value = !!(await fetchHistoryByUrl(detail.video_url))
  } catch {
    fromCache.value = false
  }
  summaryKey.value++
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
    showError(err, 'download')
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
    showError(err, 'payment')
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
