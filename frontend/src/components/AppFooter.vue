<template>
  <footer class="relative overflow-hidden text-gray-500 bg-ink border-t border-line">
    <!-- 顶部像素阶梯沿：与页面的像素化过渡 -->
    <div class="absolute top-0 left-0 right-0 flex justify-center gap-1 pt-0 pointer-events-none" aria-hidden="true">
      <span v-for="(w, i) in pixelSteps" :key="i"
        class="block w-2 rounded-b-[2px]"
        :class="w.color"
        :style="{ height: w.h + 'px' }"></span>
    </div>

    <div class="relative max-w-7xl mx-auto px-4 sm:px-6 pt-16 pb-10">
      <div class="flex flex-col items-center text-center">
        <!-- Logo：点击回首页。整块都是热区，不只是字和图标。 -->
        <button type="button" @click="$emit('go-home')"
          class="flex items-center gap-2.5 mb-4 group rounded-xl px-2 py-1 -mx-2
                 hover:bg-white/5 transition-colors"
          title="回到首页">
          <PixelLogo :size="36" />
          <div class="flex items-baseline gap-1.5">
            <span class="text-xl font-bold text-gray-900">VidDigest</span>
          </div>
        </button>

        <p class="text-sm text-gray-500 max-w-md leading-relaxed">
          AI 视频理解与下载平台 — 基于 Vue 3 + FastAPI + yt-dlp + LLM 构建
        </p>

        <!-- 技术栈徽章 -->
        <div class="flex flex-wrap items-center justify-center gap-2 mt-6">
          <span v-for="tech in ['Vue 3', 'FastAPI', 'yt-dlp', 'LLM']" :key="tech"
            class="text-[11px] font-pixel px-2.5 py-1 rounded-full bg-panel border border-line text-gray-500">
            {{ tech }}
          </span>
        </div>

        <div class="flex items-center gap-4 mt-8 pt-6 w-full max-w-md border-t border-line">
          <span class="text-xs font-pixel text-gray-400 mx-auto">© 2026 VidDigest · All rights reserved.</span>
        </div>
      </div>
    </div>
  </footer>
</template>

<script setup>
import PixelLogo from './PixelLogo.vue'

/** 页脚 Logo 与顶部 Logo 一样是「回到起始页」的入口。 */
defineEmits(['go-home'])

/* 像素阶梯：品牌六色平涂，高低错落形成下坠动势。
   走令牌而不是任意值：原来是 bg-[#7C3AED] 这类硬编码，完全绕过了 @theme，
   改配色时它们会变成无人认领的杂色。 */
const pixelSteps = [
  { h: 6, color: 'bg-blue-500' },
  { h: 10, color: 'bg-blue-300' },
  { h: 6, color: 'bg-amber-500' },
  { h: 14, color: 'bg-amber-400' },
  { h: 6, color: 'bg-cyan-500' },
  { h: 18, color: 'bg-blue-700' },
]
</script>
