<template>
  <section ref="sectionRef" class="py-20 sm:py-24 relative overflow-hidden">
    <!-- 背景装饰：散落像素点 -->
    <div class="absolute inset-0 pointer-events-none" aria-hidden="true">
      <span class="absolute top-1/4 right-[8%] w-2 h-2 rounded-[2px] bg-blue/20 animate-pixel-blink"></span>
      <span class="absolute bottom-1/4 left-[6%] w-2.5 h-2.5 rounded-[2px] bg-cyan/20 animate-pixel-blink delay-3"></span>
    </div>

    <div class="max-w-7xl mx-auto px-4 sm:px-6 relative z-10">
      <div class="text-center mb-14">
        <span class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-blue-50 border border-blue-100/50 text-blue-400 text-xs font-pixel mb-4">
          <span class="w-1.5 h-1.5 rounded-[1px] bg-blue-500"></span>
          Features
        </span>
        <h2 class="text-3xl sm:text-4xl font-bold text-gray-900 mb-3">核心功能</h2>
        <p class="text-gray-500 max-w-xl mx-auto">一站式视频处理工具，从下载到 AI 深度分析</p>
      </div>

      <!-- 6 张 = 3 列 × 2 行刚好占满。原先 5 张配 5 列，第 6 个功能加进来时
           会掉到下一行留个空位；宁可改列数也不要留半行空档。 -->
      <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
        <div v-for="(f, i) in features" :key="f.title"
          :class="[
            'group relative bg-panel p-6 sm:p-7 rounded-2xl border border-line card-hover cursor-default',
            'opacity-0 translate-y-6 transition-all duration-700 ease-out',
            visible ? 'opacity-100 translate-y-0' : ''
          ]"
          :style="{ transitionDelay: `${i * 100}ms` }"
        >
          <!-- 卡片顶部像素装饰：hover 时逐格点亮 -->
          <div class="absolute top-0 left-6 right-6 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-500">
            <span class="h-[3px] flex-1 rounded-b-[1px]" :class="f.barColor"></span>
            <span class="h-[3px] w-4 rounded-b-[1px] bg-line"></span>
            <span class="h-[3px] w-2 rounded-b-[1px] bg-line"></span>
          </div>

          <!-- 图标 -->
          <div :class="[
            'w-11 h-11 rounded-xl border border-line flex items-center justify-center mb-4 transition-all duration-300 group-hover:scale-110',
            f.iconBg
          ]">
            <component :is="f.icon" class="w-5 h-5" :class="f.iconColor" />
          </div>

          <h3 class="font-semibold text-gray-900 mb-1.5 text-sm">{{ f.title }}</h3>
          <p class="text-xs text-gray-500 leading-relaxed">{{ f.desc }}</p>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useSectionAnimation } from '../composables/useSectionAnimation.js'

// 图标组件
import IconVideo from './icons/IconVideo.vue'
import IconZap from './icons/IconZap.vue'
import IconMapPin from './icons/IconMapPin.vue'
import IconFileText from './icons/IconFileText.vue'
import IconHeart from './icons/IconHeart.vue'
import IconUsers from './icons/IconUsers.vue'

const features = [
  { icon: IconVideo, title: '多平台下载', desc: '支持 YouTube、B站、抖音等 1800+ 平台视频解析下载', iconBg: 'bg-blue-50 group-hover:bg-blue-100', iconColor: 'text-blue-400', barColor: 'bg-blue' },
  { icon: IconZap, title: 'AI 总结', desc: 'LLM 智能分析视频内容，一键生成深度摘要', iconBg: 'bg-amber-50 group-hover:bg-amber-100', iconColor: 'text-amber-600', barColor: 'bg-amber-600' },
  { icon: IconUsers, title: '社区共享', desc: '同一个链接全站只解析一次，总结进入公共区共享', iconBg: 'bg-cyan-50 group-hover:bg-cyan-100', iconColor: 'text-cyan-400', barColor: 'bg-cyan-400' },
  { icon: IconMapPin, title: '思维导图', desc: '自动提取知识结构，生成可视化思维导图', iconBg: 'bg-emerald-50 group-hover:bg-emerald-100', iconColor: 'text-emerald-600', barColor: 'bg-emerald-600' },
  { icon: IconFileText, title: '字幕导出', desc: 'SRT/VTT/TXT 多格式字幕下载，支持离线语音转写', iconBg: 'bg-blue-50 group-hover:bg-blue-100', iconColor: 'text-blue-400', barColor: 'bg-blue' },
  { icon: IconHeart, title: '免费试用', desc: '每账号每日 3 次免费 AI 总结额度，0 点重置', iconBg: 'bg-amber-50 group-hover:bg-amber-100', iconColor: 'text-amber-400', barColor: 'bg-amber' },
]

const sectionRef = useSectionAnimation()
const visible = ref(false)
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
