<template>
  <div>
    <HeroSection
      @parse="emit('parse', $event)"
      :loading="loading"
      :compact="!!videoData"
      :showSlogan="!videoData || demoMode"
    />

    <!-- 视频结果区域 -->
    <Transition name="slide-up">
      <section v-if="videoData" class="py-6 sm:py-10">
        <div class="max-w-7xl mx-auto px-4 sm:px-6">
          <!-- 缓存复用提示 + 重新解析 -->
          <div
            v-if="fromCache"
            class="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs rounded-xl px-4 py-2.5 bg-cyan-50 border border-cyan-100 text-cyan-300"
          >
            <svg
              class="w-4 h-4 flex-shrink-0"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
            </svg>
            <span>社区里已有这一份总结，直接复用，不用重复解析也不扣次数</span>
            <button
              @click="emit('reparse')"
              :disabled="reparseLoading || canRegenerate !== true"
              :title="
                canRegenerate === true ? '用新的提示词重新生成并覆盖这一份' : '只有首次解析这个视频的人才能重新生成'
              "
              class="ml-auto px-3 py-1 rounded-lg bg-panel border border-cyan-200 text-cyan-300 font-medium hover:bg-cyan-500 hover:text-on-solid hover:border-cyan-500 disabled:opacity-50 disabled:hover:bg-panel disabled:hover:text-cyan-300 disabled:hover:border-cyan-200 transition-all duration-200 active:scale-95 flex items-center gap-1.5"
            >
              <svg
                :class="['w-3.5 h-3.5', reparseLoading && 'animate-spin']"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
                <path d="M3 3v5h5" />
              </svg>
              {{ reparseButtonText }}
            </button>
          </div>
          <div class="flex flex-col lg:flex-row gap-6 lg:gap-8">
            <div class="w-full lg:w-2/5 lg:max-w-[420px] lg:flex-shrink-0">
              <VideoResult
                :video="videoData"
                :sourceUrl="currentUrl"
                :downloading="downloading"
                @download="emit('download', $event)"
              />
            </div>
            <div class="flex-1 min-w-0">
              <VideoSummary
                :videoUrl="currentUrl"
                :videoTitle="videoData.title"
                :videoData="videoData"
                :initialHistory="historyDetail"
                :key="summaryKey"
                :user="user"
                :hasCommunityResult="fromCache"
                :regenerateRequested="regenerateRequested"
                :byok="byok"
                @ownership="emit('ownership', $event)"
                @regenerating="emit('regenerating', $event)"
                @open-byok="emit('open-byok')"
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
      <PricingSection v-if="membershipEnabled" @open-vip="emit('open-vip')" @need-login="emit('need-login')" />
      <PlatformSection />
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * 首页视图（路由 `/` 的组件）。
 *
 * ## 为什么从 App.vue 里搬出来
 *
 * 引入 Vue Router 之后，**URL 与「现在显示哪一页」是同一件事**：
 * 路由表里有四条真实路径（`/`、`/history`、`/community`、`/admin`），
 * 页面组件由 `<router-view>` 按路由渲染，而不是由 App.vue 里的
 * `v-if / v-else-if` 链自己挑。
 *
 * 原来那条链能工作，但它是**第二套**页面状态：一份在 `currentPage` 这个
 * 局部 ref 里，一份在 URL 里。两者靠 `pushState` 手工同步，漏一处就是
 * 「地址栏变了、页面没变」。搬出来之后，路由是唯一真值。
 *
 * ## 为什么用 props 而不是搬进 store
 *
 * 解析态（videoData / currentUrl / summaryKey …）是**首页自己的**状态，
 * 别处不读。搬进全局 store 会让 store 变成垃圾桶：谁都能写、谁都得懂。
 * 这里用显式 props 把状态与事件列出来，多写几行样板，换来「这份状态
 * 归首页」这件事在代码上一眼可见。
 */

import HeroSection from '../components/HeroSection.vue'
import VideoResult from '../components/VideoResult.vue'
import VideoSummary from '../components/VideoSummary.vue'
import FeatureSection from '../components/FeatureSection.vue'
import HowToSection from '../components/HowToSection.vue'
import ComparisonSection from '../components/ComparisonSection.vue'
import PricingSection from '../components/PricingSection.vue'
import PlatformSection from '../components/PlatformSection.vue'
import type { ParsedVideo } from '../api/video.ts'
import type { HistoryDetail } from '../api/history.ts'
import type { SessionUser } from '../api/auth.ts'
import type { ByokPublicState } from '../lib/byok.ts'

/**
 * 首页视图的全部入参。
 *
 * 一次 declareProps 把这一屏需要的东西列全——分成十几次 declareProps
 * 只会让「这个视图到底吃什么」这件事变得难读。
 */
defineProps<{
  /** 解析请求在飞。 */
  loading: boolean
  /** 解析结果（没有结果时为 null，营销区域才显示）。 */
  videoData: ParsedVideo | null
  /** 演示模式：即使有结果也继续显示营销区域。 */
  demoMode: boolean
  /** 这份结果是社区里复用的（前端据此提示「不扣次数」）。 */
  fromCache: boolean
  /** 重新生成在飞。 */
  reparseLoading: boolean
  /** 能不能重新生成：null = 还没问过服务端，true/false = 服务端给的答案。 */
  canRegenerate: boolean | null
  /** 按钮文案（由 shell 算好，三态各有各的说法）。 */
  reparseButtonText: string
  /** 当前视频地址。 */
  currentUrl: string
  /** 下载在飞。 */
  downloading: boolean
  /** 个人问答历史（从历史页回填）。 */
  historyDetail: HistoryDetail | null
  /** 递增即强制重建 VideoSummary。 */
  summaryKey: number
  /** 当前登录用户。 */
  user: SessionUser | null
  /** 一次性信号：下一次 VideoSummary 挂载时按「重新解析」发起。 */
  regenerateRequested: boolean
  /** 自带凭据的公开状态（不含 key）。 */
  byok: ByokPublicState
  /** 会员功能开关。 */
  membershipEnabled: boolean
}>()

const emit = defineEmits<{
  (e: 'parse', url: string): void
  (e: 'download', formatId: string): void
  (e: 'reparse'): void
  (e: 'ownership', payload: { can_regenerate?: boolean }): void
  (e: 'regenerating', value: boolean): void
  (e: 'open-byok'): void
  (e: 'open-vip'): void
  (e: 'need-login'): void
}>()
</script>
