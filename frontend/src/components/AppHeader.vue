<template>
  <header ref="headerRef" :class="[
    'fixed top-0 left-0 right-0 z-50 transition-all duration-300',
    scrolled
      ? 'bg-ink/90 backdrop-blur-xl border-b border-line'
      : 'bg-transparent'
  ]">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
      <!-- Logo -->
      <div class="flex items-center gap-2.5 group cursor-pointer" @click="$emit('go-home')">
        <PixelLogo :size="32" />
        <div class="flex items-baseline gap-1.5">
          <span class="text-lg font-bold text-gray-900">VidDigest</span>
        </div>
      </div>

      <!-- 中间导航（移动端仅图标，md 及以上显示图标+文字） -->
      <nav class="flex items-center gap-0.5 sm:gap-1">
        <button @click="$emit('go-home')" :title="'首页'"
          :class="[
            'p-2 sm:px-4 sm:py-1.5 rounded-lg transition-colors flex items-center gap-1.5',
            page === 'home' ? 'text-blue-400 bg-blue-50 font-medium' : 'text-gray-500 hover:text-gray-800 hover:bg-gray-100'
          ]">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>
          </svg>
          <span class="hidden md:inline text-sm">首页</span>
        </button>
        <button @click="$emit('open-history')" :title="'解析历史'"
          :class="[
            'p-2 sm:px-4 sm:py-1.5 rounded-lg transition-colors flex items-center gap-1.5',
            page === 'history' ? 'text-blue-400 bg-blue-50 font-medium' : 'text-gray-500 hover:text-gray-800 hover:bg-gray-100'
          ]">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
          </svg>
          <span class="hidden md:inline text-sm">历史</span>
        </button>
      </nav>

      <!-- Desktop Actions -->
      <div class="flex items-center gap-2">
        <template v-if="user">
          <div class="flex items-center gap-2 mr-1">
            <span class="text-sm text-gray-500 hidden sm:block max-w-[120px] truncate">{{ user.email }}</span>
            <span v-if="showVipEntry && user.is_vip"
              class="inline-flex items-center gap-1 text-xs bg-yellow-50 text-yellow-700 px-2.5 py-0.5 rounded-full font-medium border border-yellow-200/60">
              <svg class="w-3 h-3" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
              VIP
            </span>
          </div>
          <button @click="$emit('logout')"
            class="text-sm text-gray-500 hover:text-gray-700 transition-colors px-3 py-1.5 rounded-lg hover:bg-gray-100">
            退出
          </button>
        </template>
        <template v-else>
          <button @click="$emit('login')"
            class="text-sm text-gray-600 hover:text-gray-900 transition-colors px-4 py-1.5 rounded-lg hover:bg-gray-100">
            登录
          </button>
          <button @click="$emit('register')"
            class="text-sm bg-violet text-white px-5 py-1.5 rounded-full hover:bg-blue-600 transition-all duration-200 active:scale-95 flex items-center gap-1.5">
            <span class="w-1.5 h-1.5 bg-white rounded-[1px]"></span>
            注册
          </button>
        </template>
      </div>
    </div>
  </header>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import PixelLogo from './PixelLogo.vue'

defineProps({
  user: { type: Object, default: null },
  page: { type: String, default: 'home' },
  showVipEntry: { type: Boolean, default: false },
})
defineEmits(['login', 'register', 'logout', 'open-vip', 'go-home', 'open-history'])

const scrolled = ref(false)
const headerRef = ref(null)

function onScroll() {
  scrolled.value = window.scrollY > 20
}

onMounted(() => window.addEventListener('scroll', onScroll, { passive: true }))
onUnmounted(() => window.removeEventListener('scroll', onScroll))
</script>
