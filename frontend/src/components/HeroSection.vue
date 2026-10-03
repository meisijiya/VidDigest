<template>
  <section :class="[
    'flex flex-col items-center justify-center transition-all duration-700 ease-out relative overflow-hidden',
    compact ? 'px-4 py-8' : 'px-4 min-h-[70vh] pt-24 pb-16'
  ]">
    <!-- 背景装饰：漂浮像素方块（平涂低透明度，依次点亮/下坠） -->
    <div class="absolute inset-0 pointer-events-none overflow-hidden" aria-hidden="true">
      <span class="absolute top-[18%] left-[12%] w-3 h-3 rounded-[3px] bg-blue/25 animate-pixel-blink"></span>
      <span class="absolute top-[30%] left-[20%] w-2 h-2 rounded-[2px] bg-blue/25 animate-pixel-blink delay-2"></span>
      <span class="absolute top-[16%] right-[16%] w-3 h-3 rounded-[3px] bg-amber/25 animate-pixel-blink delay-3"></span>
      <span class="absolute top-[42%] right-[10%] w-2 h-2 rounded-[2px] bg-cyan/30 animate-pixel-blink delay-1"></span>
      <span class="absolute bottom-[28%] left-[8%] w-2.5 h-2.5 rounded-[2px] bg-cyan/25 animate-pixel-blink delay-4"></span>
      <span class="absolute bottom-[36%] right-[22%] w-2 h-2 rounded-[2px] bg-blue/25 animate-pixel-blink delay-5"></span>
      <!-- 大号像素箭头母题（右侧，极淡） -->
      <svg class="absolute -right-6 top-1/2 -translate-y-1/2 w-64 h-64 opacity-[0.05] hidden lg:block" viewBox="0 0 64 64" fill="none">
        <rect x="14" y="14" width="12" height="12" rx="2" fill="var(--color-blue-500)"/>
        <rect x="26" y="14" width="12" height="12" rx="2" fill="var(--color-blue-300)"/>
        <rect x="38" y="14" width="12" height="12" rx="2" fill="var(--color-amber-500)"/>
        <rect x="26" y="26" width="12" height="12" rx="2" fill="var(--color-amber-400)"/>
        <rect x="38" y="26" width="12" height="12" rx="2" fill="var(--color-cyan-500)"/>
        <rect x="38" y="38" width="12" height="12" rx="2" fill="var(--color-blue-700)"/>
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
            <span class="text-amber-500">理解平台</span>
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
              class="mr-2 px-6 py-2.5 rounded-xl bg-blue text-on-primary text-sm font-medium
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
          <!-- 顶线本身就是一条渐变带：两端羽化到全透明，中间实色。
               渐变用的是**这一段自己的颜色**，不是白色 —— 白色高光画在这
               上面的话，看上去就又变成「一条常驻实线 + 一个光点在跑」，
               而那条实线全程可见，正是要去掉的那个形态。
               只给凸起生成（凹没有顶线）。 -->
          <linearGradient
            v-for="s in wallSteps.filter(w => w.line)" :key="`line-${s.id}`"
            :id="`wall-line-${s.id}`"
            gradientUnits="userSpaceOnUse"
            :x1="s.x" :x2="s.x + 120" y1="0" y2="0">
            <stop offset="0%" :stop-color="s.line" stop-opacity="0"/>
            <stop offset="25%" :stop-color="s.line" stop-opacity="1"/>
            <stop offset="75%" :stop-color="s.line" stop-opacity="1"/>
            <stop offset="100%" :stop-color="s.line" stop-opacity="0"/>
          </linearGradient>
        </defs>

        <g class="wall-drift">
          <g v-for="s in wallSteps" :key="s.id"
             class="wall-step"
             :style="{ animationDelay: s.delay + 's' }">
            <!-- 向下多伸 20 个单位，落在 viewBox 之外被 svg 裁掉：
                 底边因此始终是齐的，凸起浮起来时底下不会露出缝。 -->
            <rect :x="s.x" :y="s.y" width="120" :height="100 - s.y" fill="var(--color-panel)"/>
            <!-- 顶线：只有凸起有。整条线就是上面那个渐变，没有额外的实心底，
                 必须和块同处一个 <g>，分开写线一浮动就脱节。
                 相位与凸起的起伏共用 s.delay：这段浮到最高的刻，正好亮到最盛。 -->
            <template v-if="s.line">
              <rect :x="s.x" :y="s.y" width="120" height="3"
                    :fill="`url(#wall-line-${s.id})`"
                    class="wall-line"
                    :style="{ animationDelay: s.delay + 's' }"/>
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
 * 底部城墙：12 段严格交替 —— 凸（垛口）/ 凹（两垛之间的缺口）。
 *
 * y 决定高度（越小越突出）。**凹一律比凸矮**，中间留出一段落差，
 * 轮廓才读得出「一个一个垛口」，而不是随机起伏的锯齿。
 * 只有凸起带 line（顶线 + 扫光），凹不画线。
 *
 * 相邻两个凸起不能同色：并排同色时中间那道分界就消失了，两垛会看成一根长条。
 * 带子是两份拼的，所以**循环接缝那一对也要查** —— 最后一段的凸起紧挨着
 * 下一份的第一段，同样不能撞色。
 *
 * 颜色全部取自 style.css 的 @theme：blue / amber / cyan 三族，6 个值互不相同，
 * 按同族深浅推进再换族；末尾 amber 接回开头 blue 时色相跨度 168°，
 * 循环点上不会看出「一圈结束了」。
 * tests/nav-wall.test.mjs 会把每个值拿去和 @theme 对账，配色跑偏当场转红。
 *
 * ⚠ 这 6 个值是**写死的 hex 字面量**，测试要求它们能在 @theme 里查到，
 * 所以它们不随明亮主题变化（亮色下这组深色值会偏暗）。
 * 要让城墙跟主题，需要另加一层「色值 -> 令牌」的映射，那是后续工作。
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

// 凸起（垛口）画顶线，凹不画。凹的 y 一律大于凸，最高凸 34 < 最浅凹 52。
const wallBase = [
  { x: 0,    y: 28, line: '#2fa1da' },   // 凸 blue-500 主色
  { x: 120,  y: 54 },                    // 凹
  { x: 240,  y: 32, line: '#1b5a79' },   // 凸 blue-300 同族压深
  { x: 360,  y: 56 },                    // 凹
  { x: 480,  y: 26, line: '#f49a34' },   // 凸 amber-500 唯一暖色
  { x: 600,  y: 52 },                    // 凹
  { x: 720,  y: 34, line: '#60ebc6' },   // 凸 cyan-500 信息色
  { x: 840,  y: 56 },                    // 凹
  { x: 960,  y: 30, line: '#67abcd' },   // 凸 blue-700 同族提亮
  { x: 1080, y: 54 },                    // 凹
  { x: 1200, y: 28, line: '#e17d0c' },   // 凸 amber-400 暖色压深
  { x: 1320, y: 56 },                    // 凹
]

/**
 * 两份拼成一条无限循环的带子：第二份整体左移一整圈 WALL_WIDTH。
 *
 * 带子向右平移 WALL_WIDTH 的过程中，第一份滑出右边界、第二份正好补进左边界，
 * 首尾是同一条墙，接缝看不出来。
 *
 * 两个副本**必须共用同一份 delay**——同一段墙在两处位置本就该
 * 同相位，否则循环点上那段墙会突然换个动作。扫光没有单独的字段：
 * 渐变的 animationDelay 也绑 delay，于是「这段被扫到」和「这段浮到最高」
 * 是同一刻，两条动画合成一个动作。
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

/* ── 城墙：顶线渐变显现 ─────────────────────────────────
   线条本体已经是「两端羽化 + opacity 呼吸」的一条渐变带，
   所以这里**不写位移**：写 translateX 就是在一条线上推一个光点，
   观感退回到「线全程可见 + 另有东西在动」。
   相位与 .wall-step 的起伏共用 s.delay，一个动作。 */
.wall-line {
  animation: wall-line 2.4s ease-in-out infinite;
}
@keyframes wall-line {
  0%, 100% { opacity: 0.25; }
  50%      { opacity: 1; }
}
</style>
