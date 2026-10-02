<template>
  <section :class="[
    'flex flex-col items-center justify-center transition-all duration-700 ease-out relative overflow-hidden',
    compact ? 'px-4 py-8' : 'px-4 min-h-[70vh] pt-24 pb-16'
  ]">
    <!-- 背景装饰：漂浮像素方块（平涂低透明度，依次点亮/下坠） -->
    <div class="absolute inset-0 pointer-events-none overflow-hidden" aria-hidden="true">
      <span class="absolute top-[18%] left-[12%] w-3 h-3 rounded-[3px] bg-violet/25 animate-pixel-blink"></span>
      <span class="absolute top-[30%] left-[20%] w-2 h-2 rounded-[2px] bg-purple/25 animate-pixel-blink delay-2"></span>
      <span class="absolute top-[16%] right-[16%] w-3 h-3 rounded-[3px] bg-pink/25 animate-pixel-blink delay-3"></span>
      <span class="absolute top-[42%] right-[10%] w-2 h-2 rounded-[2px] bg-cyan/30 animate-pixel-blink delay-1"></span>
      <span class="absolute bottom-[28%] left-[8%] w-2.5 h-2.5 rounded-[2px] bg-cyan/25 animate-pixel-blink delay-4"></span>
      <span class="absolute bottom-[36%] right-[22%] w-2 h-2 rounded-[2px] bg-violet/25 animate-pixel-blink delay-5"></span>
      <!-- 大号像素箭头母题（右侧，极淡） -->
      <svg class="absolute -right-6 top-1/2 -translate-y-1/2 w-64 h-64 opacity-[0.05] hidden lg:block" viewBox="0 0 64 64" fill="none">
        <rect x="14" y="14" width="12" height="12" rx="2" fill="#7C3AED"/>
        <rect x="26" y="14" width="12" height="12" rx="2" fill="#A855F7"/>
        <rect x="38" y="14" width="12" height="12" rx="2" fill="#EC4899"/>
        <rect x="26" y="26" width="12" height="12" rx="2" fill="#A855F7"/>
        <rect x="38" y="26" width="12" height="12" rx="2" fill="#06B6D4"/>
        <rect x="38" y="38" width="12" height="12" rx="2" fill="#06B6D4"/>
      </svg>
    </div>

    <div class="relative z-10 w-full max-w-3xl flex flex-col items-center">
      <!-- 标语 -->
      <Transition name="slogan">
        <div v-if="showSlogan" class="text-center mb-10">
          <div class="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-panel border border-line text-cyan-400 text-xs font-pixel mb-6 animate-fade-in">
            <span class="w-1.5 h-1.5 rounded-[1px] bg-cyan-500 animate-pulse"></span>
            支持 1800+ 平台
          </div>
          <h1 class="text-4xl sm:text-5xl lg:text-6xl font-bold tracking-tight mb-4">
            <span class="text-gray-900">AI 视频</span>
            <span class="text-coral-500">理解平台</span>
          </h1>
          <p class="text-lg sm:text-xl text-gray-500 max-w-xl mx-auto leading-relaxed">
            粘贴链接，生成 <span class="text-gray-700 font-medium">总结 / 思维导图 / 问答</span>
          </p>
        </div>
      </Transition>

      <!-- 搜索框 -->
      <div :class="[
        'w-full max-w-2xl transition-all duration-500',
        showSlogan ? '' : 'mt-2'
      ]">
        <div class="relative group">
          <div class="relative flex items-center bg-panel rounded-2xl border border-line
                      shadow-lg shadow-black/30
                      focus-within:border-blue-500 focus-within:ring-4 focus-within:ring-blue-50 transition-all duration-300">
            <svg class="ml-5 w-5 h-5 text-gray-400 flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input
              v-model="url"
              type="url"
              placeholder="粘贴视频链接，例如 https://www.bilibili.com/video/BV..."
              class="flex-1 px-4 py-4 bg-transparent text-sm text-gray-800 placeholder-gray-400 focus:outline-none"
              @keyup.enter="handleParse"
            />
            <button
              @click="handleParse"
              :disabled="loading"
              class="mr-2 px-6 py-2.5 rounded-xl bg-violet text-white text-sm font-medium
                     hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed
                     transition-all duration-200 active:scale-95 whitespace-nowrap"
            >
              <span class="flex items-center gap-2">
                <!-- 加载中：像素方块下坠动画 -->
                <span v-if="loading" class="flex items-end gap-[3px] h-4" aria-hidden="true">
                  <span class="w-1.5 h-1.5 rounded-[1px] bg-white animate-pixel-drop"></span>
                  <span class="w-1.5 h-1.5 rounded-[1px] bg-white animate-pixel-drop delay-1"></span>
                  <span class="w-1.5 h-1.5 rounded-[1px] bg-white animate-pixel-drop delay-2"></span>
                </span>
                <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/>
                </svg>
                {{ loading ? '解析中...' : '解析' }}
              </span>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部像素阶梯（仅完整模式）。
         数据驱动而不是手写 rect：波浪相位、循环拼接都靠算，手写只能靠眼睛数。
         两条动画分属两层 <g>：
           .wall-drift 整条带子向右平移（凸起「向右动」）
           .wall-step  每段在漂移之上再上下浮（凸起「上下动」）
         嵌套 transform 会相乘，所以两层分开写、各自只管一个方向。 -->
    <div v-if="!compact" class="absolute bottom-0 left-0 right-0 pointer-events-none" aria-hidden="true">
      <svg class="w-full h-14 sm:h-20 overflow-hidden" viewBox="0 0 1440 80" preserveAspectRatio="none">
        <defs>
          <!-- 扫光带：userSpaceOnUse 各自锚在自己的顶线上，
               translateX 走完 120 就正好扫过整条线。 -->
          <linearGradient
            v-for="s in wallSteps.filter(w => w.line)" :key="`grad-${s.id}`"
            :id="`wall-shine-${s.id}`"
            gradientUnits="userSpaceOnUse"
            :x1="s.x - 12" :x2="s.x + 18" y1="0" y2="0"
            class="wall-shine"
            :style="{ animationDelay: s.shineDelay + 's' }">
            <stop offset="0%" stop-color="#fff" stop-opacity="0"/>
            <stop offset="50%" stop-color="#fff" stop-opacity="0.9"/>
            <stop offset="100%" stop-color="#fff" stop-opacity="0"/>
          </linearGradient>
        </defs>

        <g class="wall-drift">
          <g v-for="s in wallSteps" :key="s.id"
             class="wall-step"
             :style="{ animationDelay: s.delay + 's' }">
            <!-- 向下多伸 20 个单位，落在 viewBox 之外被 svg 裁掉：
                 底边因此始终是齐的，凸起浮起来时底下不会露出缝。 -->
            <rect :x="s.x" :y="s.y" width="120" :height="100 - s.y" fill="#161F36"/>
            <template v-if="s.line">
              <rect :x="s.x" :y="s.y" width="120" height="3" :fill="s.line" opacity="0.55"/>
              <rect :x="s.x" :y="s.y" width="120" height="3" :fill="`url(#wall-shine-${s.id})`"/>
            </template>
          </g>
        </g>
      </svg>
    </div>
  </section>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  loading: Boolean,
  compact: Boolean,
  showSlogan: Boolean,
})
const emit = defineEmits(['parse'])

const url = ref('')

/**
 * 底部城墙：12 段阶梯，y 决定凸起高度（越高越靠上），line 是顶线颜色。
 *
 * 两条动画的周期必须凑成整数倍，否则循环点会「跳」一下：
 *   WALL_BOB_SEC  每段上下浮一轮 —— 12 段 × WALL_STEP_DELAY 正好等于它，
 *                 于是第 12 段的相位接回第 1 段，波是**连续**推过去的，
 *                 中间没有一段「所有块都停在原位」的空窗。
 *   WALL_DRIFT_SEC 整条带子横向滑一轮 = 12s = 5 个上下浮周期。
 *                 两层 <g> 各自独立循环，对齐后接缝才看不出来。
 */
const WALL_WIDTH = 1440
const WALL_DRIFT_SEC = 12
const WALL_BOB_SEC = 2.4
const WALL_STEP_DELAY = 0.2

const wallBase = [
  { x: 0,    y: 40, line: null,       shineDelay: 0.0 },
  { x: 120,  y: 48, line: null,       shineDelay: 0.0 },
  { x: 240,  y: 32, line: '#7C3AED', shineDelay: 0.0 },
  { x: 360,  y: 52, line: null,       shineDelay: 0.0 },
  { x: 480,  y: 40, line: null,       shineDelay: 0.0 },
  { x: 600,  y: 56, line: null,       shineDelay: 0.0 },
  { x: 720,  y: 36, line: '#EC4899', shineDelay: 0.8 },
  { x: 840,  y: 48, line: null,       shineDelay: 0.0 },
  { x: 960,  y: 28, line: '#06B6D4', shineDelay: 1.6 },
  { x: 1080, y: 52, line: null,       shineDelay: 0.0 },
  { x: 1200, y: 40, line: null,       shineDelay: 0.0 },
  { x: 1320, y: 48, line: null,       shineDelay: 0.0 },
]

/**
 * 两份拼成一条无限循环的带子：第二份整体左移一整圈 WALL_WIDTH。
 *
 * 带子向右平移 WALL_WIDTH 的过程中，第一份滑出右边界、第二份正好补进左边界，
 * 首尾是同一条墙，接缝看不出来。
 *
 * 两个副本**必须共用同一份 delay / shineDelay**——同一段墙在两处位置本就该
 * 同相位，否则循环点上那段墙会突然换个动作。
 */
const wallSteps = [
  ...wallBase.map((s, i) => ({ ...s, id: `a${i}`, delay: i * WALL_STEP_DELAY })),
  ...wallBase.map((s, i) => ({
    ...s, id: `b${i}`, delay: i * WALL_STEP_DELAY, x: s.x - WALL_WIDTH,
  })),
]

function extractUrl(text) {
  const match = text.match(/https?:\/\/[^\s）\)"\'＞，。、；：！？》>\]]+/)
  return match ? match[0] : text
}

function handleParse() {
  const raw = url.value.trim()
  if (!raw) return
  const cleanUrl = extractUrl(raw)
  emit('parse', cleanUrl)
  if (cleanUrl !== raw) url.value = cleanUrl
}
</script>

<style scoped>
.slogan-enter-active, .slogan-leave-active {
  transition: all 0.5s ease-out;
}
.slogan-enter-from, .slogan-leave-to {
  opacity: 0;
  transform: translateY(-16px);
}

/* ── 城墙：整条带子向右滑（凸起「向右动」）────────────────
   位移量必须正好等于 WALL_WIDTH：带子是两份拼的，滑一整圈第二份
   刚好顶到第一份原来的位置，接缝不跳。 */
.wall-drift {
  animation: wall-drift 12s linear infinite;
}
@keyframes wall-drift {
  from { transform: translateX(0); }
  to   { transform: translateX(1440px); }
}

/* ── 城墙：凸起上下浮（凸起「上下动」）──────────────────
   和 wall-drift 是嵌套的两层 <g>，transform 相乘，所以这里只写 Y。
   写 X 也不会立刻看出来错，但会让「向上下」变成「斜着晃」。 */
.wall-step {
  animation: wall-bob 2.4s ease-in-out infinite;
}
@keyframes wall-bob {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-5px); }
}

/* ── 城墙：顶线扫光 ──────────────────────────────────────
   CSS 的 transform 落在 <linearGradient> 上等价于 gradientTransform，
   移动的是渐变带本身，那条 3px 的线不用动。走满 120 就扫完一整条。 */
.wall-shine {
  animation: wall-shine 2.4s linear infinite;
}
@keyframes wall-shine {
  from { transform: translateX(0); }
  to   { transform: translateX(120px); }
}
</style>
