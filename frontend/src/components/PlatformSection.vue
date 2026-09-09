<template>
  <section ref="sectionRef" class="py-20 sm:py-24 relative overflow-hidden">
    <div class="max-w-5xl mx-auto px-4 sm:px-6 text-center">
      <div class="mb-14">
        <span class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-teal-50 border border-teal-100/50 text-teal-300 text-xs font-pixel mb-4">
          <span class="w-1.5 h-1.5 rounded-[1px] bg-cyan-500"></span>
          Platforms
        </span>
        <h2 class="text-3xl sm:text-4xl font-bold text-gray-900 mb-3">支持的海量平台</h2>
        <p class="text-gray-500">基于 yt-dlp 核心引擎，覆盖全球主流视频平台</p>
      </div>

      <div class="flex flex-wrap justify-center gap-3">
        <span v-for="(p, i) in platforms" :key="p.name"
          :class="[
            'inline-flex items-center gap-2 px-5 py-2.5 rounded-full border text-sm transition-all duration-300',
            'opacity-0 scale-90',
            visible ? 'opacity-100 scale-100' : '',
            hoveredPlatform === p.name
              ? 'border-gray-300 bg-panel-2 text-gray-800 -translate-y-0.5'
              : 'border-line bg-panel text-gray-500 hover:border-gray-300 hover:-translate-y-0.5'
          ]"
          :style="{ transitionDelay: `${i * 50}ms` }"
          @mouseenter="hoveredPlatform = p.name"
          @mouseleave="hoveredPlatform = null"
        >
          <span class="w-1.5 h-1.5 rounded-[1px]" :class="p.color"></span>
          {{ p.name }}
        </span>
      </div>

      <p class="text-sm text-gray-400 mt-8 flex items-center justify-center gap-2 font-pixel">
        <span class="w-1.5 h-1.5 rounded-[1px] bg-cyan-500 animate-pixel-blink"></span>
        ...以及 1800+ 其他平台
      </p>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

/* 平台徽章：像素点循环使用品牌四色 */
const brandColors = ['bg-violet', 'bg-purple', 'bg-pink', 'bg-cyan-500']
const platformNames = [
  'YouTube', 'Bilibili', '抖音', 'Twitter/X', 'Instagram',
  'Facebook', 'TikTok', 'Vimeo', 'Twitch', 'SoundCloud',
  'Reddit', 'Pinterest', 'DailyMotion', 'LinkedIn',
]
const platforms = platformNames.map((name, i) => ({
  name,
  color: brandColors[i % brandColors.length],
}))

const hoveredPlatform = ref(null)
const visible = ref(false)
const sectionRef = ref(null)
let observer = null

onMounted(() => {
  if (!sectionRef.value) return
  observer = new IntersectionObserver(
    ([entry]) => {
      if (entry.isIntersecting) {
        visible.value = true
        observer?.disconnect()
      }
    },
    { threshold: 0.1 }
  )
  observer.observe(sectionRef.value)
})

onUnmounted(() => observer?.disconnect())
</script>
