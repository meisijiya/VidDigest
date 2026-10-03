<template>
  <div class="bg-panel rounded-2xl border border-line overflow-hidden card-hover">
    <!-- 缩略图：点击跳转视频源页面 -->
    <a
      v-if="sourceUrl"
      :href="sourceUrl"
      target="_blank"
      rel="noopener noreferrer"
      class="relative block aspect-video bg-gray-100 overflow-hidden group"
      title="在新标签页打开视频源"
    >
      <img
        :src="proxyThumbnail(video.thumbnail)"
        :alt="video.title"
        class="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
      />
      <div class="absolute inset-0 bg-gradient-to-t from-black/30 to-transparent"></div>
      <!-- 悬浮提示：外链图标 -->
      <span class="absolute top-3 right-3 w-7 h-7 rounded-lg bg-black/55 backdrop-blur-sm flex items-center justify-center
                   opacity-0 group-hover:opacity-100 transition-opacity duration-200 text-cyan-300">
        <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
        </svg>
      </span>
      <span class="absolute bottom-3 right-3 bg-black/60 backdrop-blur-sm text-white text-xs font-pixel px-2.5 py-1 rounded-full flex items-center gap-1.5">
        <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        {{ video.duration_string }}
      </span>
    </a>
    <div v-else class="relative aspect-video bg-gray-100 overflow-hidden">
      <img
        :src="proxyThumbnail(video.thumbnail)"
        :alt="video.title"
        class="w-full h-full object-cover transition-transform duration-500 hover:scale-105"
      />
      <div class="absolute inset-0 bg-gradient-to-t from-black/30 to-transparent"></div>
      <span class="absolute bottom-3 right-3 bg-black/60 backdrop-blur-sm text-white text-xs font-pixel px-2.5 py-1 rounded-full flex items-center gap-1.5">
        <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        {{ video.duration_string }}
      </span>
    </div>

    <!-- 视频信息 -->
    <div class="p-5 space-y-4">
      <div>
        <h3 class="font-semibold text-gray-900 line-clamp-2 leading-snug">{{ video.title }}</h3>
        <div class="flex items-center gap-2 text-sm text-gray-500 mt-2">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
          <span>{{ video.uploader }}</span>
          <span>·</span>
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
          <span>{{ video.platform }}</span>
        </div>
      </div>

      <!-- 格式选择：视频与纯音频分两块。音频没有分辨率，
           把它塞进「选择画质」那一栏是在骗人。 -->
      <div v-if="video.formats?.length">
        <template v-if="videoFormats.length">
          <label class="block text-sm font-medium text-gray-700 mb-2.5">选择画质</label>
          <div class="grid grid-cols-2 gap-2">
            <button
              v-for="fmt in videoFormats"
              :key="fmt.format_id"
              @click="selectFormat(fmt)"
              :class="[
                'px-3 py-2.5 text-sm rounded-xl border transition-all duration-200 text-left relative overflow-hidden',
                selectedFormat?.format_id === fmt.format_id
                  ? 'border-blue-500 bg-blue-50 text-blue-400 ring-1 ring-blue-500/30'
                  : 'border-line text-gray-700 hover:border-gray-300 hover:bg-gray-100'
              ]"
            >
              <div class="font-medium font-pixel">{{ formatTitle(fmt) }}</div>
              <div class="text-xs text-gray-500 mt-0.5">{{ fmt.label }}</div>
            </button>
          </div>
        </template>
        <template v-if="audioFormats.length">
          <label class="block text-sm font-medium text-gray-700 mt-4 mb-2.5">纯音频</label>
          <div class="grid grid-cols-2 gap-2">
            <button
              v-for="fmt in audioFormats"
              :key="fmt.format_id"
              @click="selectFormat(fmt)"
              :class="[
                'px-3 py-2.5 text-sm rounded-xl border transition-all duration-200 text-left relative overflow-hidden',
                selectedFormat?.format_id === fmt.format_id
                  ? 'border-blue-500 bg-blue-50 text-blue-400 ring-1 ring-blue-500/30'
                  : 'border-line text-gray-700 hover:border-gray-300 hover:bg-gray-100'
              ]"
            >
              <div class="font-medium font-pixel">{{ formatTitle(fmt) }}</div>
              <div class="text-xs text-gray-500 mt-0.5">{{ fmt.label }}</div>
            </button>
          </div>
        </template>
      </div>

      <!-- 下载按钮 -->
      <button
        @click="handleDownload"
        :disabled="!selectedFormat || downloading"
        class="w-full py-3 rounded-xl bg-blue text-on-primary font-medium text-sm
               hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed
               transition-all duration-200 active:scale-[0.98] flex items-center justify-center gap-2"
      >
        <span v-if="downloading" class="flex items-end gap-[3px] h-4" aria-hidden="true">
          <span class="w-1.5 h-1.5 rounded-[1px] bg-white animate-pixel-drop"></span>
          <span class="w-1.5 h-1.5 rounded-[1px] bg-white animate-pixel-drop delay-1"></span>
          <span class="w-1.5 h-1.5 rounded-[1px] bg-white animate-pixel-drop delay-2"></span>
        </span>
        <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        {{ downloading ? '下载中...' : downloadLabel }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  video: Object,
  sourceUrl: String,
  downloading: Boolean,
})
const emit = defineEmits(['download'])

const selectedFormat = ref(null)

// 音频与视频分成两块。**未知 kind 一律当视频** —— 方向是刻意挑的：
// 后端哪天回归漏了 kind，用户最坏是看到一个归错块的选项，而不是一个
// 凭空消失的选项（后者正是当初让「没有音频」这件事看不见的原因）。
// 「每条格式都必须自报 kind」由 backend/tests/test_audio_download.py 钉住。
const videoFormats = computed(() =>
  (props.video?.formats || []).filter((fmt) => fmt.kind !== 'audio')
)
const audioFormats = computed(() =>
  (props.video?.formats || []).filter((fmt) => fmt.kind === 'audio')
)

// 按钮文案随选中项走。界面上写着「下载音频」而用户拿到一个带声的 mp4，
// 是最难查的一类 bug —— 不报错，文件也确实下来了。
const downloadLabel = computed(() =>
  selectedFormat.value?.kind === 'audio' ? '下载音频' : '下载视频'
)

function formatTitle(fmt) {
  if (fmt.kind !== 'audio') return fmt.resolution
  // 音频没有分辨率，拿一个码率当标题；码率也未知就退到容器名
  // （抖音的 mp3 就是这种情况 —— 它有码率，只是我们没去查）。
  return fmt.abr ? fmt.abr + ' kbps' : ((fmt.ext || '').toUpperCase() || '纯音频')
}

function selectFormat(fmt) {
  selectedFormat.value = fmt
}

function handleDownload() {
  if (!selectedFormat.value) return
  emit('download', selectedFormat.value.format_id)
}

function proxyThumbnail(url) {
  if (!url) return ''
  return '/api/proxy/thumbnail?url=' + encodeURIComponent(url)
}
</script>
