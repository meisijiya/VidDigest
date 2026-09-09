<template>
  <footer class="relative overflow-hidden text-gray-500 bg-[#0B1220] border-t border-line">
    <!-- 顶部像素阶梯沿：与页面的像素化过渡 -->
    <div class="absolute top-0 left-0 right-0 flex justify-center gap-1 pt-0 pointer-events-none" aria-hidden="true">
      <span v-for="(w, i) in pixelSteps" :key="i"
        class="block w-2 rounded-b-[2px]"
        :class="w.color"
        :style="{ height: w.h + 'px' }"></span>
    </div>

    <div class="relative max-w-7xl mx-auto px-4 sm:px-6 pt-16 pb-10">
      <div class="flex flex-col items-center text-center">
        <!-- Logo -->
        <div class="flex items-center gap-2.5 mb-4 group">
          <PixelLogo :size="36" />
          <div class="flex items-baseline gap-1.5">
            <span class="text-xl font-bold text-gray-900">VidDown</span>
            <span class="text-xl font-light text-cyan-300 font-pixel">AI</span>
          </div>
        </div>

        <p class="text-sm text-gray-500 max-w-md leading-relaxed">
          AI 万能视频下载总结器 — 基于 Vue 3 + FastAPI + yt-dlp + DeepSeek + Stripe 构建
        </p>

        <!-- 技术栈徽章 -->
        <div class="flex flex-wrap items-center justify-center gap-2 mt-6">
          <span v-for="tech in ['Vue 3', 'FastAPI', 'yt-dlp', 'DeepSeek', 'Stripe']" :key="tech"
            class="text-[11px] font-pixel px-2.5 py-1 rounded-full bg-panel border border-line text-gray-500">
            {{ tech }}
          </span>
        </div>

        <div class="flex items-center gap-4 mt-8 pt-6 w-full max-w-md border-t border-line">
          <span class="text-xs font-pixel text-gray-400 mx-auto">© 2026 VidDownAI · All rights reserved.</span>
        </div>
      </div>
    </div>
  </footer>
</template>

<script setup>
import PixelLogo from './PixelLogo.vue'

/* 像素阶梯：品牌四色平涂，高低错落形成下坠动势 */
const pixelSteps = [
  { h: 6, color: 'bg-[#7C3AED]' },
  { h: 10, color: 'bg-[#A855F7]' },
  { h: 6, color: 'bg-[#EC4899]' },
  { h: 14, color: 'bg-[#A855F7]' },
  { h: 6, color: 'bg-[#06B6D4]' },
  { h: 18, color: 'bg-[#06B6D4]' },
]
</script>
