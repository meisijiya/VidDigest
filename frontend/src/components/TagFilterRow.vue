<template>
  <div class="relative" ref="rootEl">
    <div
      ref="rowEl"
      class="flex items-center gap-2"
      :class="expanded ? 'flex-wrap' : 'flex-nowrap overflow-hidden'"
      :aria-label="label"
    >
      <!-- 全部：清空选择。放在最前且永远可见，否则选中之后就没法一键回到全量。 -->
      <button
        v-if="allLabel"
        type="button"
        @click="clearAll"
        :aria-pressed="selected.length === 0"
        :class="chipClass(selected.length === 0)"
      >
        {{ allLabel }}
      </button>

      <button
        v-for="t in tags"
        :key="t.tag"
        type="button"
        data-tag-chip
        @click="toggle(t.tag)"
        :aria-pressed="selected.includes(t.tag)"
        :class="chipClass(selected.includes(t.tag))"
      >
        <span>{{ t.tag }}</span>
        <span v-if="showCount" class="opacity-60">{{ t.count }}</span>
      </button>
    </div>

    <!-- 收起态：右侧渐隐，提示「后面还有」而不是硬切一刀。
         渐隐是必要的——没有它，最后一个被裁的标签看起来就像渲染坏了。 -->
    <div
      v-if="showToggle && !expanded"
      class="pointer-events-none absolute inset-y-0 right-0 w-12 flex items-center justify-end
             bg-gradient-to-l from-panel to-transparent"
      aria-hidden="true"
    ></div>

    <button
      v-if="showToggle"
      type="button"
      @click="expanded = !expanded"
      :aria-expanded="expanded"
      class="mt-1 px-1.5 py-0.5 text-[11px] font-pixel text-gray-400
             hover:text-gray-700 transition-colors"
    >
      {{ expanded ? '收起标签' : `展开其余 ${hiddenCount} 个标签` }}
    </button>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { countHiddenChips, needsExpand, shouldShowToggle, toggleTag } from '../lib/tag-filter.js'

const props = defineProps({
  /** [{ tag, count }] —— 由服务端给，不从当前页汇总。 */
  tags: { type: Array, default: () => [] },
  /** 已选中的标签名数组。 */
  selected: { type: Array, default: () => [] },
  /** true = 多选；false = 单选（点新的就顶掉旧的）。 */
  multiple: { type: Boolean, default: true },
  showCount: { type: Boolean, default: false },
  allLabel: { type: String, default: '' },
  label: { type: String, default: '标签筛选' },
})
const emit = defineEmits(['update:selected'])

const rowEl = ref(null)
const rootEl = ref(null)
const expanded = ref(false)
const overflowing = ref(false)

/**
 * 展开键上那个 N 说的是**真的被藏起来的个数**，所以只能量、不能算。
 * 早先写的是 `tags.length - 1`，于是 6 个标签只溢出 2 个时也照样写
 * 「展开其余 5 个标签」——按钮能点开，但数字在骗人。
 */
const hiddenCount = ref(0)
const showToggle = computed(() => shouldShowToggle(
  props.tags.length, overflowing.value, expanded.value,
))

function measure() {
  const el = rowEl.value
  if (!el) return
  // 展开态下直接返回，**不碰** overflowing 与 hiddenCount。
  //
  // 这里曾经写成 `overflowing.value = needsExpand(...)`：展开态下
  // needsExpand 恒为 false，于是 overflowing 被抹成 false，showToggle
  // 随之消失——行已经换行了，「收起标签」却不见了，用户再也收不回去。
  // 真浏览器里点一次展开就复现；假 DOM 的单测当时还把这个错误行为
  // 写成了断言（见 community-tags-ui.test.mjs 里「展开态」那条）。
  if (expanded.value) return
  overflowing.value = needsExpand(el.scrollWidth, el.clientWidth)
  if (!overflowing.value) {
    hiddenCount.value = 0
    return
  }
  const limit = el.getBoundingClientRect().right + 1
  // 只数**标签** chip，不数最前面那个「全部」按钮——按钮上写的是
  // 「展开其余 N 个标签」，把「全部」算进去的话 N 与文案就对不上了。
  // 今天不产生可观察偏差（最左的 chip 永远不是被裁的那个），但那是
  // 碰巧，不是设计。
  const chipRights = Array.from(el.children)
    .filter((c) => c.hasAttribute && c.hasAttribute('data-tag-chip'))
    .map((c) => c.getBoundingClientRect().right)
  hiddenCount.value = countHiddenChips(chipRights, limit)
}

function chipClass(active) {
  return [
    'shrink-0 px-3 py-1.5 rounded-lg text-xs font-pixel transition-colors border',
    active
      ? 'bg-blue text-on-primary border-blue'
      : 'bg-panel text-gray-500 border-line hover:border-gray-300',
  ]
}

/**
 * 多选/单选切换。逻辑住在 lib/tag-filter.js，这里只负责把 DOM 事件翻译成
 * 一次 update:selected —— 组件里**不要再存一份副本**，否则测试 import 的
 * 那个函数和真正上线的那份就会各走各的，测绿了也证明不了界面行为。
 */
function toggle(tag) {
  emit('update:selected', toggleTag(props.selected, tag, props.multiple))
}

function clearAll() {
  emit('update:selected', [])
}

let ro = null
onMounted(async () => {
  await nextTick()
  measure()
  if (typeof ResizeObserver !== 'undefined' && rootEl.value) {
    ro = new ResizeObserver(measure)
    ro.observe(rootEl.value)
  } else {
    window.addEventListener('resize', measure)
  }
})
onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  else window.removeEventListener('resize', measure)
})

// 标签集变了要重量一次：切筛选后行宽变了，展开键的显隐可能跟着变。
watch(() => props.tags, async () => {
  await nextTick()
  measure()
}, { deep: true })
watch(expanded, async () => {
  await nextTick()
  measure()
})
</script>
