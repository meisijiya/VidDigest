<template>
  <!-- 品牌像素 Logo：与 favicon 同构的方块下坠箭头（平涂，无渐变）

       ⚠ 这里刻意**不引用任何主题令牌**，色值是写死的字面量。
       理由：Logo 是品牌标记，不是界面装饰。品牌识别依赖的是「这一坨紫色粉
       色青色的方块」在用户心里的固定形象，而品牌窗口/书签/浏览器标签上的
       favicon 不读 CSS 变量、也不跟随 data-theme —— 如果这里的方块跟着主题
       变，明亮主题下它就变成蓝橙方块，而标签页上还是紫粉青，两处对不上。
       所以：全站 UI 走石墨+湖蓝，唯独这 6 个方块（和底板）保持品牌原色。
       见 ADR 0008「品牌标记豁免」一节。
       改配色前先读那一节 —— 这个文件已经被「顺手改成新配色」过一次了。

       六个色块按「阅读顺序」错峰闪动（delay-0..5），让 logo 本身就是
       「像素母题」在跳，而不是在它旁边再摆一个会跳的装饰。
       闪动做在组件里而不是各调用处：三处复用（页头/页脚/登录弹窗）共享同一份。 -->

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
    <rect x="14" y="14" width="12" height="12" rx="2" fill="#7C3AED" class="animate-pixel-blink" />
    <rect x="26" y="14" width="12" height="12" rx="2" fill="#A855F7" class="animate-pixel-blink delay-1" />
    <rect x="38" y="14" width="12" height="12" rx="2" fill="#EC4899" class="animate-pixel-blink delay-2" />
    <rect x="26" y="26" width="12" height="12" rx="2" fill="#A855F7" class="animate-pixel-blink delay-3" />
    <rect x="38" y="26" width="12" height="12" rx="2" fill="#06B6D4" class="animate-pixel-blink delay-4" />
    <rect x="38" y="38" width="12" height="12" rx="2" fill="#06B6D4" class="animate-pixel-blink delay-5" />
  </svg>
</template>

<script setup>
defineProps({
  size: { type: [Number, String], default: 32 },
})
</script>
