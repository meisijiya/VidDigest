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
            <span class="text-gray-900">AI 万能</span>
            <span class="text-coral-500">视频下载器</span>
          </h1>
          <p class="text-lg sm:text-xl text-gray-500 max-w-xl mx-auto leading-relaxed">
            粘贴链接，一键解析下载 + <span class="text-gray-700 font-medium">AI 智能总结</span>
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

    <!-- 底部像素阶梯装饰（仅完整模式） -->
    <div v-if="!compact" class="absolute bottom-0 left-0 right-0 pointer-events-none" aria-hidden="true">
      <svg class="w-full h-14 sm:h-20" viewBox="0 0 1440 80" preserveAspectRatio="none">
        <g fill="#161F36">
          <rect x="0" y="40" width="120" height="40"/>
          <rect x="120" y="48" width="120" height="32"/>
          <rect x="240" y="32" width="120" height="48"/>
          <rect x="360" y="52" width="120" height="28"/>
          <rect x="480" y="40" width="120" height="40"/>
          <rect x="600" y="56" width="120" height="24"/>
          <rect x="720" y="36" width="120" height="44"/>
          <rect x="840" y="48" width="120" height="32"/>
          <rect x="960" y="28" width="120" height="52"/>
          <rect x="1080" y="52" width="120" height="28"/>
          <rect x="1200" y="40" width="120" height="40"/>
          <rect x="1320" y="48" width="120" height="32"/>
        </g>
        <g>
          <rect x="240" y="32" width="120" height="3" fill="#7C3AED" opacity="0.55"/>
          <rect x="720" y="36" width="120" height="3" fill="#EC4899" opacity="0.55"/>
          <rect x="960" y="28" width="120" height="3" fill="#06B6D4" opacity="0.55"/>
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
</style>
