<template>
  <div class="min-h-[calc(100vh-4rem)]">
    <div class="max-w-6xl mx-auto px-4 sm:px-6 py-8">
      <!-- 页头 -->
      <div class="flex items-center gap-3 mb-6">
        <button
          type="button"
          @click="emit('back')"
          aria-label="返回管理后台"
          class="w-9 h-9 rounded-xl bg-panel border border-line flex items-center justify-center text-gray-500 hover:text-gray-800 hover:border-gray-300 transition-colors"
        >
          <svg
            class="w-4 h-4"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M19 12H5" />
            <path d="M12 19l-7-7 7-7" />
          </svg>
        </button>
        <div>
          <h1 class="text-xl font-bold text-gray-900">邀请码管理</h1>
          <p class="text-xs text-gray-400 mt-0.5">
            注册要邀请码，码在这里发。库里只存摘要，明文只在发放那一刻给一次（工单 #104 / ADR 0025）
          </p>
        </div>
      </div>

      <!-- 批量发放 -->
      <section class="rounded-2xl border border-line bg-panel p-5">
        <h2 class="text-sm font-medium text-gray-800">批量发放</h2>
        <p class="text-xs text-gray-400 mt-0.5">
          一次最多 20 枚。每枚码的明文只在这次响应里给一次，列表里永远只有脱敏码。
        </p>
        <form class="mt-4 flex flex-wrap items-end gap-3" @submit.prevent="issueCodes">
          <div>
            <label for="invite-count" class="block text-xs text-gray-500 mb-1">发放数量</label>
            <input
              id="invite-count"
              v-model="draft.count"
              type="number"
              min="1"
              max="20"
              class="w-28 px-3 py-2 rounded-xl bg-panel border border-line text-sm text-gray-800"
            />
          </div>
          <div>
            <label for="invite-max" class="block text-xs text-gray-500 mb-1">可用次数</label>
            <input
              id="invite-max"
              v-model="draft.maxUses"
              type="number"
              min="1"
              class="w-28 px-3 py-2 rounded-xl bg-panel border border-line text-sm text-gray-800"
            />
          </div>
          <div>
            <label for="invite-hours" class="block text-xs text-gray-500 mb-1">有效小时数</label>
            <input
              id="invite-hours"
              v-model="draft.expiresInHours"
              type="number"
              min="1"
              placeholder="留空 = 永不过期"
              class="w-44 px-3 py-2 rounded-xl bg-panel border border-line text-sm text-gray-800 placeholder-gray-400 focus:border-blue-200 focus:outline-none"
            />
          </div>
          <div>
            <label for="invite-note" class="block text-xs text-gray-500 mb-1">备注</label>
            <input
              id="invite-note"
              v-model="draft.note"
              type="text"
              placeholder="给谁"
              class="w-48 px-3 py-2 rounded-xl bg-panel border border-line text-sm text-gray-800 placeholder-gray-400 focus:border-blue-200 focus:outline-none"
            />
          </div>
          <button
            type="submit"
            :disabled="issuing"
            class="px-4 py-2 rounded-xl bg-blue text-on-primary text-sm font-medium hover:bg-blue-600 transition-colors disabled:opacity-50"
          >
            {{ issuing ? '发放中…' : '发放邀请码' }}
          </button>
        </form>
        <span aria-live="polite" :class="['block text-xs mt-2', feedbackClass(issueFeedback.kind)]">
          {{ issueFeedback.text }}
        </span>
      </section>

      <!-- 刚发放的明文码：只给这一次 -->
      <section v-if="issuedCodes.length" class="mt-6 rounded-2xl border border-line bg-panel-2 p-5">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-sm font-medium text-gray-800">刚发放的码</h2>
          <p class="text-xs text-gray-400">明文只给这一次。关掉这一块就只剩脱敏码了。</p>
          <button
            type="button"
            class="ml-auto px-3 py-1.5 rounded-lg border border-line text-xs text-gray-500 hover:border-blue-200 hover:text-blue-600 transition-colors"
            @click="issuedCodes = []"
          >
            我已保存
          </button>
        </div>
        <ul class="mt-3 space-y-2">
          <li v-for="item in issuedCodes" :key="item.code" class="flex items-center gap-2">
            <code class="font-pixel text-sm text-gray-800 break-all">{{ item.code }}</code>
            <button
              type="button"
              class="px-2.5 py-1 rounded-lg border border-line text-xs text-gray-500 hover:border-blue-200 hover:text-blue-600 transition-colors"
              @click="copyCode(item.code)"
            >
              复制
            </button>
          </li>
        </ul>
      </section>

      <!-- 列表 -->
      <section class="mt-6 rounded-2xl border border-line bg-panel p-5">
        <div class="flex flex-wrap items-center gap-2">
          <h2 class="text-sm font-medium text-gray-800">邀请码</h2>
          <label for="invite-status" class="sr-only">按状态筛选</label>
          <select
            id="invite-status"
            v-model="status"
            class="px-2.5 py-1 rounded-lg bg-panel border border-line text-xs text-gray-600"
            @change="onFilterChange"
          >
            <option value="">全部状态</option>
            <option value="active">可用</option>
            <option value="exhausted">已用完</option>
            <option value="expired">已过期</option>
            <option value="revoked">已作废</option>
          </select>
          <span class="ml-auto text-xs text-gray-400">共 {{ total }} 枚</span>
        </div>

        <!-- 列表：只显示脱敏码（后端在列表出口就把完整码拿掉了）。 -->
        <div class="mt-3 overflow-x-auto rounded-2xl border border-line bg-panel">
          <table class="w-full text-sm">
            <caption class="sr-only">
              邀请码列表，只显示脱敏码
            </caption>
            <thead>
              <tr class="text-left text-xs text-gray-400 border-b border-line">
                <th scope="col" class="px-4 py-3 font-medium">码</th>
                <th scope="col" class="px-4 py-3 font-medium">状态</th>
                <th scope="col" class="px-4 py-3 font-medium">用量</th>
                <th scope="col" class="px-4 py-3 font-medium">过期</th>
                <th scope="col" class="px-4 py-3 font-medium">备注</th>
                <th scope="col" class="px-4 py-3 font-medium">发放人</th>
                <th scope="col" class="px-4 py-3 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in rows" :key="row.id" class="border-b border-line last:border-b-0">
                <td class="px-4 py-3 font-pixel text-xs text-gray-800">{{ row.codeMasked }}</td>
                <td class="px-4 py-3 text-xs text-gray-600">{{ statusLabel(row.status) }}</td>
                <td class="px-4 py-3 text-xs text-gray-600">{{ row.usedCount }} / {{ row.maxUses }}</td>
                <td class="px-4 py-3 text-xs text-gray-400">{{ row.expiresAt || '永不过期' }}</td>
                <td class="px-4 py-3 text-xs text-gray-400">{{ row.note || '—' }}</td>
                <td class="px-4 py-3 text-xs text-gray-400">{{ row.createdByEmail || '—' }}</td>
                <td class="px-4 py-3">
                  <button
                    type="button"
                    :disabled="row.revoked === 1 || revokingId === row.id"
                    class="px-2.5 py-1 rounded-lg border border-line text-xs text-gray-500 hover:border-blue-200 hover:text-blue-600 transition-colors disabled:opacity-40"
                    @click="revoke(row.id)"
                  >
                    {{ row.revoked === 1 ? '已作废' : '作废' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!rows.length" class="px-4 py-6 text-center text-xs text-gray-400">没有符合条件的邀请码</p>
        </div>
        <span aria-live="polite" :class="['block text-xs mt-2', feedbackClass(listFeedback.kind)]">
          {{ listFeedback.text }}
        </span>

        <div class="mt-3 flex items-center gap-2">
          <button
            type="button"
            :disabled="page <= 1 || loading"
            class="px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500 hover:border-gray-300 transition-colors disabled:opacity-40"
            @click="goPage(page - 1)"
          >
            上一页
          </button>
          <span class="text-xs text-gray-400">第 {{ page }} 页 / 共 {{ totalPages }} 页</span>
          <button
            type="button"
            :disabled="page >= totalPages || loading"
            class="px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500 hover:border-gray-300 transition-colors disabled:opacity-40"
            @click="goPage(page + 1)"
          >
            下一页
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 邀请码管理页（工单 #104 / ADR 0025）。
 *
 * ## 为什么是独立路由，而不是管理后台里的第四个页签
 *
 * 用户拍板的形态是「独立页 `/admin/invite-codes`」。后台那三个页签是
 * 「同一批数据的不同视图」，邀请码是另一件事；挂成页签会给「页签 key /
 * 每页一份状态槽」那份冻结契约再加一份，也会让管理员在后台里翻页时
 * 反复经过一个跟用户/社区无关的视图。
 *
 * ## 明文只给一次
 *
 * 库里只存 sha256 摘要（工单 #103），后端在创建响应里给一次明文，之后
 * 任何出口都拿不回来。所以「刚发放的码」单独占一块，复制走它；页面刷新
 * 之后那份明文就没了——这是设计，不是丢数据。
 *
 * ## 数据从哪来
 *
 * **只走 api/admin.ts**：snake_case → camelCase 的翻法只有 api 层有一份，
 * 组件一次都不自己翻（与 AdminPage 同纪律）。
 */
import { ref, reactive, computed, onMounted } from 'vue'
import {
  fetchAdminInviteCodes,
  createAdminInviteCodes,
  revokeAdminInviteCode,
} from '../api/admin'

/** 一行邀请码。类型从 api 层的出口推出来，不另写一份形状。 */
type InviteRow = Awaited<ReturnType<typeof fetchAdminInviteCodes>>['items'][number]

const emit = defineEmits(['back'])

const PAGE_SIZE = 20

const rows = ref<InviteRow[]>([])
const total = ref(0)
const page = ref(1)
const status = ref('')
const loading = ref(false)
const issuing = ref(false)
const revokingId = ref<number | null>(null)
const draft = reactive({ count: '1', maxUses: '1', expiresInHours: '', note: '' })
const issueFeedback = reactive({ kind: '', text: '' })
const listFeedback = reactive({ kind: '', text: '' })
/** 刚发放的明文码：每枚一份，只在这一次响应里拿得到。 */
const issuedCodes = ref<{ code: string }[]>([])

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

/** 状态 → 给管理员看的中文。与后端 `invite_code_status` 的取值一一对应。 */
function statusLabel(value: string): string {
  if (value === 'revoked') return '已作废'
  if (value === 'expired') return '已过期'
  if (value === 'exhausted') return '已用完'
  return '可用'
}

function feedbackClass(kind: string): string {
  if (kind === 'error') return 'text-red-500'
  if (kind === 'ok') return 'text-green-600'
  return 'text-gray-400'
}

/** 后端的错误体形状不止一种，抽出人能读的那句。 */
function messageOf(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'error' in detail) {
    const msg = (detail as { error?: unknown }).error
    if (typeof msg === 'string') return msg
  }
  return e?.message || '未知错误'
}

async function loadCodes(): Promise<void> {
  loading.value = true
  try {
    const res = await fetchAdminInviteCodes({
      page: page.value,
      pageSize: PAGE_SIZE,
      status: status.value,
    })
    rows.value = res.items
    total.value = res.total
  } catch (err) {
    rows.value = []
    listFeedback.kind = 'error'
    listFeedback.text = `邀请码列表取不到：${messageOf(err)}`
  } finally {
    loading.value = false
  }
}

/** 发码。次数与有效期在**本地**先判一道，不为显然错的输入跑一趟网络。 */
async function issueCodes(): Promise<void> {
  if (issuing.value) return
  const count = Number(draft.count)
  if (!Number.isInteger(count) || count < 1 || count > 20) {
    issueFeedback.kind = 'error'
    issueFeedback.text = '发放数量要在 1 到 20 之间'
    return
  }
  const maxUses = Number(draft.maxUses)
  if (!Number.isInteger(maxUses) || maxUses < 1) {
    issueFeedback.kind = 'error'
    issueFeedback.text = '可用次数至少是 1'
    return
  }
  // number 输入框的 v-model 给的是**数字**（Vue 对 type="number" 自动转型），
  // 空的时候才是空串。所以这里一律先转成字符串再 trim，
  // 不然用户填了小时数就会在 trim 上炸掉。
  const hoursRaw = String(draft.expiresInHours ?? '').trim()
  const hours = hoursRaw ? Number(hoursRaw) : null
  if (hours !== null && (!Number.isInteger(hours) || hours < 1)) {
    issueFeedback.kind = 'error'
    issueFeedback.text = '有效小时数至少是 1；留空 = 永不过期'
    return
  }
  issuing.value = true
  issueFeedback.kind = ''
  issueFeedback.text = ''
  try {
    const res = await createAdminInviteCodes({
      count,
      maxUses,
      expiresInHours: hours,
      note: String(draft.note ?? '').trim(),
    })
    issuedCodes.value = res.items
      .map((item) => ({ code: item.code ?? '' }))
      .filter((item) => item.code !== '')
    draft.note = ''
    issueFeedback.kind = 'ok'
    issueFeedback.text = `已发放 ${issuedCodes.value.length} 枚。明文只给这一次，列表里只看得见脱敏码。`
    page.value = 1
    await loadCodes()
  } catch (err) {
    issueFeedback.kind = 'error'
    issueFeedback.text = `没发放：${messageOf(err)}`
  } finally {
    issuing.value = false
  }
}

/** 作废。作废之后这个码不能再注册，但已经用它建好的号不动。 */
async function revoke(id: number): Promise<void> {
  if (revokingId.value !== null) return
  revokingId.value = id
  try {
    await revokeAdminInviteCode(id)
    await loadCodes()
    listFeedback.kind = 'ok'
    listFeedback.text = '已作废。这个码不能再注册了，已经用它建好的号不动。'
  } catch (err) {
    listFeedback.kind = 'error'
    listFeedback.text = `没作废：${messageOf(err)}`
  } finally {
    revokingId.value = null
  }
}

/** 复制刚发放的明文码。剪贴板不可用时给一句能照做的提示，而不是静默失败。 */
async function copyCode(code: string): Promise<void> {
  if (!code) return
  try {
    await navigator.clipboard?.writeText(code)
    listFeedback.kind = 'ok'
    listFeedback.text = '已复制。把它发给对方——明文只给这一次。'
  } catch {
    listFeedback.kind = 'error'
    listFeedback.text = '复制不了：手动选中上面那串码复制。'
  }
}

function goPage(next: number): void {
  if (next < 1 || next > totalPages.value) return
  page.value = next
  void loadCodes()
}

/** 换筛选：回到第 1 页，否则可能停在一个已经没有内容的页码上。 */
function onFilterChange(): void {
  page.value = 1
  void loadCodes()
}

onMounted(() => {
  void loadCodes()
})
</script>
