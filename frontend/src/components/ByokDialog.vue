<script setup>
/**
 * 自带 API Key 的集中配置弹窗。
 *
 * 挂在顶栏的额度面板里，而不是解析卡片或追问框——「用谁的额度」是**账号级**
 * 的决定，放在任何一个功能面板里，用户就得在两个地方各理解一次。
 *
 * ## 关于真值
 *
 * 本组件**从不持有**已保存的 key。它能看到的只有 `hasKey`（一个布尔）。
 * 输入框里的明文活到点「保存」为止，保存完立刻清空：
 * 留在 DOM 里会进浏览器自动填充，也留在「检查元素」里，
 * 比只待在内存里危险得多。
 *
 * 端点 / 厂商 / 模型是可以公开的，页面上显示出来对用户排查问题有用。
 */
import { ref, computed, watch, onMounted } from 'vue'
import {
  MODE_PLATFORM, getPublicState, save, updateConfig, clear, usePlatform, chooseProvider,
  validateBaseUrl,
} from '../lib/byok.js'
import { fetchPublicModelCatalog } from '../api/models.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
  /** 账号是否已登录。未登录时这个入口不该出现。 */
  loggedIn: { type: Boolean, default: true },
})
const emit = defineEmits(['close'])

const state = ref(getPublicState())
const apiKeyInput = ref('')
const error = ref('')

/**
 * 厂商清单**从服务端来**（工单 #13）。本组件持有它只是为了渲染下拉，
 * 不在任何地方再抄一份——抄的那份就是漂移。
 */
const catalog = ref([])
const catalogLoading = ref(false)
const catalogError = ref('')

async function loadCatalog() {
  catalogLoading.value = true
  catalogError.value = ''
  try {
    catalog.value = await fetchPublicModelCatalog().then((r) => r.items)
  } catch {
    // 清空而不是留着上一次的：清单变了还拿旧的接着渲染，用户会选到一个
    // 已经下架的厂商，保存下去服务端不认。
    catalog.value = []
    catalogError.value = '厂商清单没能加载出来，暂时选不了厂商。'
  } finally {
    catalogLoading.value = false
  }
}

onMounted(loadCatalog)

const provider = computed({
  get: () => state.value.provider,
  // 把已经拿在手里的那条记录一并交给 byok.js：那边不再有清单可查。
  set: (id) => {
    state.value = chooseProvider(id, catalog.value.find((p) => p.id === id))
    error.value = ''
  },
})

const baseUrl = computed({
  get: () => state.value.baseUrl,
  set: (v) => { state.value = { ...state.value, baseUrl: v }; error.value = '' },
})

const model = computed({
  get: () => state.value.model,
  set: (v) => { state.value = { ...state.value, model: v }; error.value = '' },
})

const currentHint = computed(
  () => catalog.value.find((p) => p.id === state.value.provider)?.hint || '',
)

/**
 * 上次选的厂商可能已经从清单里下架了。此时下拉里没有对应项，
 * 用户看到的是一个「什么都没选中」的下拉，而保存会把他那个已下架的 id
 * 悄悄存回去。提示与「不让存」都从这一个判断来。
 *
 * 清单没加载出来时不算数：这时候空清单说明的是「查不到」，不是「已下架」。
 * `platform` 也不算数：它是模式而不是厂商，本就不必然出现在清单里。
 */
const missingFromCatalog = computed(
  () => catalog.value.length > 0
    && state.value.provider !== MODE_PLATFORM
    && !catalog.value.some((p) => p.id === state.value.provider),
)

/** 打开时重置：上一次留下的半个 key 不该在下次打开时还挂在输入框里。 */
watch(() => props.visible, (open) => {
  if (!open) return
  state.value = getPublicState()
  apiKeyInput.value = ''
  error.value = ''
  // 上次拉失败（比如那时后端还没起来）就趁这次再试一次
  if (catalog.value.length === 0 && !catalogLoading.value) loadCatalog()
})

function saveAndClose() {
  const typed = apiKeyInput.value.trim()
  // 端点预检**只是**为了少发一次注定被拒的请求，不是安全边界。
  // 真正承重的是服务端 credentials.validate_base_url；这里抄一份是为了
  // 少一次往返，抄漏一条的代价只是「保存成功、解析时才报一句」——
  // 而那句话本身就说明了问题。已知抄不回来的形状：WHATWG 的 URL 解析器
  // 会把 https:///v1 规范化成 https://v1/，前端看不到「空主机」这个形状。
  const urlError = validateBaseUrl(baseUrl.value)
  if (urlError) { error.value = urlError; return }
  if (missingFromCatalog.value) {
    error.value = '原来选的厂商已不在可用清单里，请重新选一个再保存。'
    return
  }
  if (state.value.provider === 'platform' && typed) {
    error.value = '厂商选了「平台 Key」却又填了 key。选一个具体厂商，或把 key 清空。'
    return
  }
  if (typed) {
    save({ apiKey: typed, provider: provider.value, baseUrl: baseUrl.value, model: model.value })
  } else {
    // 留空 = 沿用已保存的那把（输入框占位符承诺的）。
    // 走 updateConfig 而不是 save({apiKey: ''})：后者的空 key 语义是「清除」，
    // 而占位符写的是「沿用」——用户只改了个模型名，key 却被静默清空了。
    updateConfig({ provider: provider.value, baseUrl: baseUrl.value, model: model.value })
  }
  // 明文只活到这里
  apiKeyInput.value = ''
  state.value = getPublicState()
  emit('close')
}

function usePlatformMode() {
  usePlatform()
  apiKeyInput.value = ''
  state.value = getPublicState()
  emit('close')
}

function clearAll() {
  clear()
  apiKeyInput.value = ''
  state.value = getPublicState()
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/40" @click="emit('close')"></div>

      <div role="dialog" aria-modal="true" aria-label="API Key 设置"
        class="relative w-full max-w-md rounded-2xl bg-panel border border-line shadow-2xl p-5 space-y-4">
        <header class="flex items-start justify-between gap-3">
          <div>
            <h2 class="text-base font-semibold text-gray-900">API Key 与调用端点</h2>
            <p class="text-xs text-gray-500 mt-1 leading-relaxed">
              解析与追问共用这一份设置。选「使用自己的 Key」后两者都不消耗平台额度。
            </p>
          </div>
          <button type="button" @click="emit('close')" aria-label="关闭"
            class="p-1 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors">
            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </header>

        <!-- 模式切换：用户要的就是这一个下拉 -->
        <div>
          <label class="block text-xs font-medium text-gray-600 mb-1.5" for="byok-mode">调用方式</label>
          <select id="byok-mode" v-model="provider" :disabled="catalogLoading"
            class="w-full px-3 py-2 rounded-xl border border-line bg-ink/60 text-sm text-gray-800
                   focus:ring-2 focus:ring-blue-500 outline-none transition-all">
            <option v-for="p in catalog" :key="p.id" :value="p.id">{{ p.label }}</option>
          </select>
          <p v-if="catalogError" class="text-[11px] text-red-500 mt-1.5">{{ catalogError }}</p>
          <p v-else-if="missingFromCatalog" class="text-[11px] text-gray-400 mt-1.5">
            保存的厂商已不在当前清单里，请重新选一个。
          </p>
          <p v-else-if="currentHint" class="text-[11px] text-gray-400 mt-1.5">{{ currentHint }}</p>
        </div>

        <div v-if="state.provider !== 'platform'">
          <label class="block text-xs font-medium text-gray-600 mb-1.5" for="byok-key">API Key</label>
          <input id="byok-key" v-model="apiKeyInput" type="password" autocomplete="off" spellcheck="false"
            :placeholder="state.hasKey ? '已保存一把，留空则沿用它' : 'sk-...'"
            class="w-full px-3 py-2 rounded-xl border border-line bg-ink/60 text-sm text-gray-800
                   focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all" />
          <p v-if="state.hasKey" class="text-[11px] text-emerald-600 mt-1.5">
            已保存一把 API Key。留空即沿用。
          </p>
        </div>

        <div v-if="state.provider !== 'platform'" class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1.5" for="byok-base">Base URL</label>
            <input id="byok-base" v-model="baseUrl" type="text" autocomplete="off" spellcheck="false"
              placeholder="留空则用服务端默认"
              class="w-full px-3 py-2 rounded-xl border border-line bg-ink/60 text-xs text-gray-800
                     focus:ring-2 focus:ring-blue-500 outline-none transition-all" />
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1.5" for="byok-model">模型</label>
            <input id="byok-model" v-model="model" type="text" autocomplete="off" spellcheck="false"
              placeholder="留空则用服务端默认"
              class="w-full px-3 py-2 rounded-xl border border-line bg-ink/60 text-xs text-gray-800
                     focus:ring-2 focus:ring-blue-500 outline-none transition-all" />
          </div>
        </div>

        <p v-if="error" class="text-xs text-red-500 leading-relaxed">{{ error }}</p>

        <p class="text-[11px] text-gray-400 leading-relaxed">
          Base URL 支持 http 与 https：自建推理服务（Ollama / vLLM / LM Studio）
          通常只监听本机 http。服务端只放行 http/https，且不允许在地址里夹带用户名或密码。
          <br />
          隐私承诺：Key 只存在这台设备的浏览器与单次请求的内存里。服务端不保存、不建表、
          不写日志，请求结束即丢弃；本页与报错信息都不会回显它。
        </p>

        <footer class="flex flex-wrap items-center gap-2 pt-1">
          <button type="button" @click="saveAndClose"
            class="px-4 py-2 rounded-xl bg-blue text-on-primary text-sm font-medium
                   hover:bg-blue-600 transition-all duration-200 active:scale-95">
            保存
          </button>
          <button type="button" @click="usePlatformMode"
            class="px-3 py-2 rounded-xl text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 transition-colors">
            改用平台 Key
          </button>
          <button type="button" v-if="state.hasKey" @click="clearAll"
            class="px-3 py-2 rounded-xl text-sm text-gray-400 hover:text-red-500 transition-colors">
            清除已保存的 Key
          </button>
        </footer>
      </div>
    </div>
  </Teleport>
</template>
