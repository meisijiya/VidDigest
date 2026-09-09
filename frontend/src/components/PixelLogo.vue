<template>
  <!-- 品牌像素 Logo：与 favicon 同构的方块下坠箭头（平涂，无渐变） -->
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 64 64"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
    class="flex-shrink-0 transition-transform group-hover:scale-105"
  >
    <rect width="64" height="64" rx="14" fill="#161F36" />
    <rect x="0.5" y="0.5" width="63" height="63" rx="13.5" stroke="#263152" />
    <rect x="14" y="14" width="12" height="12" rx="2" fill="#7C3AED" />
    <rect x="26" y="14" width="12" height="12" rx="2" fill="#A855F7" />
    <rect x="38" y="14" width="12" height="12" rx="2" fill="#EC4899" />
    <rect x="26" y="26" width="12" height="12" rx="2" fill="#A855F7" />
    <rect x="38" y="26" width="12" height="12" rx="2" fill="#06B6D4" />
    <rect x="38" y="38" width="12" height="12" rx="2" fill="#06B6D4" />
  </svg>
</template>

<script setup>
defineProps({
  size: { type: [Number, String], default: 32 },
})
</script>
