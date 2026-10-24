<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="visible" class="fixed inset-0 z-[100] flex items-center justify-center" @click.self="$emit('close')">
        <div class="absolute inset-0 bg-black/60 backdrop-blur-sm"></div>
        <div
          class="relative bg-panel border border-line rounded-2xl shadow-2xl shadow-black/50 w-full max-w-md mx-4 p-8 animate-scale-in"
        >
          <!-- 关闭按钮 -->
          <button
            type="button"
            aria-label="关闭"
            @click="$emit('close')"
            class="absolute top-4 right-4 w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-all"
          >
            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>

          <!-- 标题 -->
          <div class="text-center mb-8">
            <div class="flex justify-center mb-4 group">
              <PixelLogo :size="48" />
            </div>
            <h2 class="text-2xl font-bold text-gray-900">
              {{ mode === 'login' ? '欢迎回来' : '创建账号' }}
            </h2>
            <p class="text-sm text-gray-500 mt-1">
              {{ mode === 'login' ? '登录后即可使用 AI 总结功能' : '注册免费账号，体验 AI 视频总结' }}
            </p>
          </div>

          <!-- 表单 -->
          <form @submit.prevent="handleSubmit" class="space-y-5">
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1.5">邮箱</label>
              <div class="relative">
                <svg
                  class="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
                  <polyline points="22,6 12,13 2,6" />
                </svg>
                <input
                  v-model="email"
                  type="email"
                  required
                  placeholder="name@example.com"
                  class="w-full pl-10 pr-4 py-2.5 rounded-xl border border-line bg-ink/60 text-sm text-gray-800 focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all duration-200 placeholder:text-gray-400"
                />
              </div>
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1.5">密码</label>
              <div class="relative">
                <svg
                  class="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                  <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                </svg>
                <input
                  v-model="password"
                  type="password"
                  required
                  minlength="6"
                  placeholder="至少 6 位密码"
                  class="w-full pl-10 pr-4 py-2.5 rounded-xl border border-line bg-ink/60 text-sm text-gray-800 focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all duration-200 placeholder:text-gray-400"
                />
              </div>
            </div>

            <!-- 邀请码（工单 #99）。注册必须带，登录不需要。
                 放在密码**之后**：既有挂载测试按 inputs()[0] / inputs()[1] 取
                 邮箱与密码，插在中间会让密码框的索引错位。 -->
            <div v-if="mode === 'register'">
              <label class="block text-sm font-medium text-gray-700 mb-1.5">
                邀请码 <span class="text-red-500">*</span>
                <span class="text-xs font-normal text-gray-400 ml-1">必填，向管理员获取</span>
              </label>
              <div class="relative">
                <svg
                  class="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <path
                    d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.78 7.78 5.5 5.5 0 017.78-7.78zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3"
                  />
                </svg>
                <input
                  v-model="inviteCode"
                  type="text"
                  aria-required="true"
                  autocomplete="off"
                  spellcheck="false"
                  placeholder="ABCD1234EFGH"
                  class="w-full pl-10 pr-4 py-2.5 rounded-xl border border-line bg-ink/60 text-sm text-gray-800 focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all duration-200 placeholder:text-gray-400"
                />
              </div>
            </div>

            <!-- 错误提示 -->
            <Transition name="error">
              <p
                v-if="error"
                class="flex items-center gap-2 text-sm text-red-500 bg-red-50 border border-red-500/20 rounded-lg px-4 py-2.5"
              >
                <svg
                  class="w-4 h-4 flex-shrink-0"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
                {{ error }}
              </p>
            </Transition>

            <button
              type="submit"
              :disabled="loading"
              class="w-full py-3 rounded-xl bg-blue text-on-primary font-medium text-sm hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-200 active:scale-[0.98]"
            >
              <span class="flex items-center justify-center gap-2">
                <span v-if="loading" class="flex items-end gap-[3px] h-4" aria-hidden="true">
                  <span class="w-1.5 h-1.5 rounded-[1px] bg-white animate-pixel-drop"></span>
                  <span class="w-1.5 h-1.5 rounded-[1px] bg-white animate-pixel-drop delay-1"></span>
                  <span class="w-1.5 h-1.5 rounded-[1px] bg-white animate-pixel-drop delay-2"></span>
                </span>
                {{ loading ? '处理中...' : mode === 'login' ? '登录' : '注册' }}
              </span>
            </button>
          </form>

          <div class="text-sm text-gray-500 text-center mt-6">
            {{ mode === 'login' ? '还没有账号？' : '已有账号？' }}
            <button
              @click="mode = mode === 'login' ? 'register' : 'login'"
              class="text-blue-400 hover:text-blue-700 font-medium transition-colors"
            >
              {{ mode === 'login' ? '去注册' : '去登录' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import axios from 'axios'
import { login, register } from '../api/auth.ts'
import PixelLogo from './PixelLogo.vue'

const props = defineProps({
  visible: Boolean,
  initialMode: { type: String, default: 'login' },
})
const emit = defineEmits(['close', 'success'])

const mode = ref('login')
const email = ref('')
const password = ref('')
// 邀请码（工单 #99）。注册必须带；登录用不到，所以登录模式不渲染这一格。
const inviteCode = ref('')
const loading = ref(false)
const error = ref('')

watch(
  () => props.visible,
  (v) => {
    if (v) {
      mode.value = props.initialMode
      email.value = ''
      password.value = ''
      inviteCode.value = ''
      error.value = ''
    }
  },
)

/**
 * 邀请码怎么进请求体（工单 #99）。
 *
 * `api/auth.ts` 的 register(email, password) 只发这两个字段，而那一层是**公共
 * 出口**，不该为了某一个表单去改它的签名。所以这里用**一次性请求拦截器**把
 * 邀请码塞进 body：
 *
 *  · axios 的请求拦截器在 dispatchRequest **之前**跑，那时 config.data 还是
 *    普通对象 {email, password}，还没被 JSON 序列化——所以能直接展开成
 *    {email, password, invite_code}。
 *  · 拦截器只在这一条请求的存续期内存在（finally 里 eject），不会漏到别的
 *    请求上：登录走的是另一条路，根本不注册。
 *
 * **为什么不在前端先挡一道「没填就不许提交」**：那会与后端那句话变成两个真相
 * 来源，而且它会拦下「填了但抄错」的真实反馈——后端分四类原因告诉用户该做什么
 * （无效 / 已作废 / 已过期 / 已用完），前端一句「不能为空」会把它们全盖住。
 * 权威判据在后端：400 + 中文「需要邀请码」。
 */
let pendingInviteInterceptor: number | null = null

async function registerWithInviteCode(email: string, password: string, code: string) {
  if (pendingInviteInterceptor !== null) {
    axios.interceptors.request.eject(pendingInviteInterceptor)
    pendingInviteInterceptor = null
  }
  pendingInviteInterceptor = axios.interceptors.request.use((config) => {
    const body = (config.data || {}) as Record<string, unknown>
    config.data = { ...body, invite_code: code }
    return config
  })
  try {
    return await register(email, password)
  } finally {
    axios.interceptors.request.eject(pendingInviteInterceptor)
    pendingInviteInterceptor = null
  }
}

async function handleSubmit() {
  loading.value = true
  error.value = ''
  try {
    const user =
      mode.value === 'login'
        ? await login(email.value, password.value)
        : await registerWithInviteCode(email.value, password.value, inviteCode.value.trim())
    emit('success', user)
    emit('close')
  } catch (err) {
    // 后端把失败原因放在 detail 里（登录失败 / 邮箱已注册 / 配额超限…）。
    // 这里只取这一段，取不到就回落成一句通用提示。
    //
    // 邀请码的四种失效原因（无效 / 已作废 / 已过期 / 已用完）也走这一条路：
    // 它们都是后端给的原文，用户据此知道该做什么——「已用完」去找管理员要
    // 新的，「无效」去检查自己有没有抄错。
    const e = err as { response?: { data?: { detail?: string } } }
    error.value = e.response?.data?.detail || '操作失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.modal-enter-active {
  transition: all 0.3s ease-out;
}
.modal-leave-active {
  transition: all 0.2s ease-in;
}
.modal-enter-from {
  opacity: 0;
}
.modal-enter-from > div:last-child {
  transform: scale(0.95);
}
.modal-leave-to {
  opacity: 0;
}
.modal-leave-to > div:last-child {
  transform: scale(0.95);
}

.error-enter-active {
  transition: all 0.3s ease-out;
}
.error-leave-active {
  transition: all 0.2s ease-in;
}
.error-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}
.error-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
