<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="visible"
        class="fixed inset-0 z-[100] flex items-center justify-center px-4"
        @click.self="$emit('close')">
        <div class="absolute inset-0 bg-black/60 backdrop-blur-sm"></div>

        <div role="alertdialog" aria-modal="true" :aria-labelledby="titleId"
          class="relative bg-panel border border-line rounded-2xl shadow-2xl shadow-black/50
                 w-full max-w-md animate-scale-in">
          <div class="p-8 pb-6">
            <!-- 图标 -->
            <div class="flex justify-center mb-4">
              <div class="w-14 h-14 rounded-2xl bg-red-50 border border-red-500/20 flex items-center justify-center">
                <svg class="w-7 h-7 text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
              </div>
            </div>

            <h2 :id="titleId" class="text-xl font-bold text-gray-900 text-center">{{ title }}</h2>
            <p class="text-sm text-gray-500 mt-2 text-center leading-relaxed break-words">{{ message }}</p>

            <!-- 补充说明：把「用户该做什么」讲清楚，而不是只说哪里错了 -->
            <div v-if="hint" class="mt-4 rounded-xl bg-blue-50 border border-blue-100 px-4 py-3">
              <p class="text-xs text-blue-700 leading-relaxed">{{ hint }}</p>
            </div>
          </div>

          <div class="px-8 pb-8">
            <button @click="$emit('close')"
              class="w-full py-2.5 rounded-xl bg-violet text-white font-medium text-sm
                     hover:bg-blue-600 transition-all duration-200 active:scale-95">
              {{ actionText }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
/**
 * 统一错误弹窗。
 *
 * 为什么不继续用 alert()：原生弹窗无视本仓视觉语言（暗色主题、
 * 像素风），文案也没法给出「下一步该做什么」；而且 alert 出现在
 * 什么位置由浏览器决定，用户看完记不住哪一步失败了。
 *
 * 关键约束：**每条错误都必须能被用户据以行动**。只说「解析失败」
 * 是不够的——说清是链接不对、要重新粘贴，还是网络问题，
 * 用户才知道自己该做什么。所以 hint 是这个组件的核心，不是装饰。
 */
import { computed } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  title: { type: String, default: '出错了' },
  message: { type: String, default: '' },
  hint: { type: String, default: '' },
  actionText: { type: String, default: '知道了' },
})

defineEmits(['close'])

const titleId = computed(() => 'err-modal-title')
</script>

<style scoped>
.animate-scale-in {
  animation: errScaleIn 0.2s ease-out;
}
@keyframes errScaleIn {
  from { opacity: 0; transform: scale(0.96) translateY(8px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}
.modal-enter-active { transition: opacity 0.2s ease; }
.modal-leave-active { transition: opacity 0.15s ease; }
.modal-enter-from, .modal-leave-to { opacity: 0; }
</style>
