<template>
  <header ref="headerRef" :class="[
    'fixed top-0 left-0 right-0 z-50 transition-all duration-300',
    scrolled
      ? 'bg-ink/90 backdrop-blur-xl border-b border-line'
      : 'bg-transparent'
  ]">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
      <!-- Logo：整块可点并带 title，否则用户不知道这里能回首页 -->
      <button type="button" @click="$emit('go-home')" title="回到首页"
        class="flex items-center gap-2.5 rounded-xl px-1.5 py-1 -ml-1.5
               hover:bg-gray-100/70 transition-colors">
        <PixelLogo :size="32" />
        <div class="flex items-baseline gap-1.5">
          <span class="text-lg font-bold text-gray-900">VidDigest</span>
        </div>
      </button>

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
        <!-- 社区：常驻第三项。原先只在首页营销区放一个「浏览社区 →」按钮，
             解析出视频之后那个按钮就没了，社区成了只在特定状态下可达的角落。 -->
        <button @click="$emit('open-community')" :title="'社区'"
          :class="[
            'p-2 sm:px-4 sm:py-1.5 rounded-lg transition-colors flex items-center gap-1.5',
            page === 'community' ? 'text-blue-400 bg-blue-50 font-medium' : 'text-gray-500 hover:text-gray-800 hover:bg-gray-100'
          ]">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>
            <path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
          </svg>
          <span class="hidden md:inline text-sm">社区</span>
        </button>
      </nav>

      <!-- Desktop Actions -->
      <div class="flex items-center gap-2">
        <template v-if="user">
          <div class="flex items-center gap-2 mr-1">
            <!-- 悬停/点击展开额度面板：额度拆成「解析 / 追问」两个计数器
                 （工单 #4）之后，藏在页面里的话用户根本不知道自己还剩几次。 -->
            <div class="relative" @mouseenter="onQuotaEnter" @mouseleave="quotaOpen = false">
              <button type="button" @click="quotaOpen = !quotaOpen" :title="'查看额度'"
                class="flex items-center gap-1.5 px-2 py-1 rounded-lg text-sm text-gray-500
                       hover:text-gray-800 hover:bg-gray-100 transition-colors"
                :aria-expanded="quotaOpen">
                <span class="max-w-[120px] truncate">{{ user.email }}</span>
                <svg class="w-3.5 h-3.5 flex-shrink-0" :class="quotaOpen && 'rotate-180'"
                  viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
                  stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </button>

              <div v-if="quotaOpen"
                class="absolute right-0 top-full mt-2 w-64 rounded-2xl bg-panel border border-line
                       shadow-xl shadow-black/20 p-4 z-50">
                <p class="text-xs font-medium text-gray-400 mb-3">今日额度</p>
                <div class="space-y-2.5">
                  <div v-for="slot in quotaRows" :key="slot.key"
                    class="flex items-center justify-between gap-3">
                    <span class="text-sm text-gray-600">{{ slot.label }}</span>
                    <span class="text-sm font-medium font-pixel"
                      :class="slot.exhausted ? 'text-red-500' : 'text-gray-800'">{{ slot.text }}</span>
                  </div>
                  <div v-if="quotaRows.length === 0"
                    class="text-xs text-gray-400 py-1">额度读取中…</div>
                </div>
                <p class="text-[11px] text-gray-400 mt-3 pt-3 border-t border-line">
                  每天 0 点重置。带入自己的 API key 可不消耗追问额度。
                </p>
              </div>
            </div>
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
import { ref, computed, onMounted, onUnmounted } from 'vue'
import PixelLogo from './PixelLogo.vue'

const props = defineProps({
  user: { type: Object, default: null },
  page: { type: String, default: 'home' },
  showVipEntry: { type: Boolean, default: false },
  quota: { type: Object, default: null },
  quotaLoading: { type: Boolean, default: false },
})
const emit = defineEmits(['login', 'register', 'logout', 'open-vip', 'go-home', 'open-history', 'open-community', 'request-quota'])

const scrolled = ref(false)
const headerRef = ref(null)
const quotaOpen = ref(false)

/**
 * 额度面板的两行。
 *
 * 复用 lib/quota.js 的判定口径而不是自己再抄一遍：同一份额度如果
 * 页面上有两套解释（「用完」到底看 parse 还是 chat），用户会看到
 * 自相矛盾的提示。exhausted 沿用那里的规则——任一计数器用完就警示。
 */
const quotaRows = computed(() => {
  const q = props.quota
  if (!q) return []
  if (!q.logged_in) return [{ key: 'login', label: '状态', text: '登录后可用', exhausted: false }]
  if (q.unlimited) return [{ key: 'unlimited', label: '状态', text: '无限次', exhausted: false }]

  const rows = []
  for (const [key, label] of [['parse', '解析'], ['chat', '追问']]) {
    const slot = q[key]
    if (!slot || typeof slot !== 'object') continue
    const remaining = Number(slot.remaining)
    const limit = Number(slot.limit)
    if (!Number.isFinite(remaining) || !Number.isFinite(limit)) continue
    if (remaining === -1) {
      rows.push({ key, label, text: '无限', exhausted: false })
    } else {
      rows.push({
        key, label,
        text: remaining <= 0 ? `已用完（0 / ${limit}）` : `${remaining} / ${limit}`,
        exhausted: remaining <= 0,
      })
    }
  }
  return rows
})

function onQuotaEnter() {
  quotaOpen.value = true
  emit('request-quota')
}

// 点击页面别处关闭面板
function onDocClick(e) {
  if (headerRef.value && !headerRef.value.contains(e.target)) quotaOpen.value = false
}

function onScroll() {
  scrolled.value = window.scrollY > 20
}

onMounted(() => {
  window.addEventListener('scroll', onScroll, { passive: true })
  document.addEventListener('click', onDocClick)
})
onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
  document.removeEventListener('click', onDocClick)
})
</script>
