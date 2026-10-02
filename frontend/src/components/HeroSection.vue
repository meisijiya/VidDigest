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
         数据驱动而不是手写 12 个 rect：波浪的错峰延迟要按 x 位算出来，
         手写就只能靠眼睛数，越加越长必然错位。 -->
    <div v-if="!compact" class="absolute bottom-0 left-0 right-0 pointer-events-none" aria-hidden="true">
      <svg class="w-full h-14 sm:h-20" viewBox="0 0 1440 80" preserveAspectRatio="none">
        <defs>
          <!-- 扫光带：userSpaceOnUse 各自锚在自己的顶线上，
               translateX 走完 120 就正好扫过整条线。 -->
          <linearGradient
            v-for="s in wallSteps.filter(w => w.line)" :key="`grad-${s.x}`"
            :id="`wall-shine-${s.x}`"
            gradientUnits="userSpaceOnUse"
            :x1="s.x - 12" :x2="s.x + 18" y1="0" y2="0"
            class="wall-shine"
            :style="{ animationDelay: s.shineDelay + 's' }">
            <stop offset="0%" stop-color="#fff" stop-opacity="0"/>
            <stop offset="50%" stop-color="#fff" stop-opacity="0.9"/>
            <stop offset="100%" stop-color="#fff" stop-opacity="0"/>
          </linearGradient>
        </defs>

        <g v-for="s in wallSteps" :key="s.x"
           class="wall-step"
           :style="{ animationDelay: s.delay + 's' }">
          <rect :x="s.x" :y="s.y" width="120" :height="80 - s.y" fill="#161F36"/>
          <template v-if="s.line">
            <rect :x="s.x" :y="s.y" width="120" height="3" :fill="s.line" opacity="0.55"/>
            <rect :x="s.x" :y="s.y" width="120" height="3" :fill="`url(#wall-shine-${s.x})`"/>
          </template>
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
 * delay 按 x 顺序递增 —— 波浪是**相邻段错开**才连成一条波线，
 * 不是各自乱浮。spread 0.16s × 12 段 = 1.92s，必须小于动画周期 2.4s，
 * 否则最后一段的相位会追过第一段，波会「回卷」而不是往前推。
 *
 * shineDelay 单独给：顶线扫光要比波浪慢半拍，三条线依次亮才像信号
 * 顺着墙跑过去。
 */
const wallSteps = [
  { x: 0, y: 40, delay: 0.00, line: null, shineDelay: 0.0 },
  { x: 120, y: 48, delay: 0.16, line: null, shineDelay: 0.0 },
  { x: 240, y: 32, delay: 0.32, line: '#7C3AED', shineDelay: 0.0 },
  { x: 360, y: 52, delay: 0.48, line: null, shineDelay: 0.0 },
  { x: 480, y: 40, delay: 0.64, line: null, shineDelay: 0.0 },
  { x: 600, y: 56, delay: 0.80, line: null, shineDelay: 0.0 },
  { x: 720, y: 36, delay: 0.96, line: '#EC4899', shineDelay: 0.8 },
  { x: 840, y: 48, delay: 1.12, line: null, shineDelay: 0.0 },
  { x: 960, y: 28, delay: 1.28, line: '#06B6D4', shineDelay: 1.6 },
  { x: 1080, y: 52, delay: 1.44, line: null, shineDelay: 0.0 },
  { x: 1200, y: 40, delay: 1.60, line: null, shineDelay: 0.0 },
  { x: 1320, y: 48, delay: 1.76, line: null, shineDelay: 0.0 },
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

/* ── 城墙：海浪 ──────────────────────────────────────────
   块和它顶上的彩色线必须浮在**同一个** <g> 里：分开写线会脱节，
   波浪一走位，顶线就留在原地，看着像贴错了。 */
.wall-step {
  animation: wall-wave 2.4s ease-in-out infinite;
}
@keyframes wall-wave {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-6px); }
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
