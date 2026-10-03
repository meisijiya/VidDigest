<template>
  <!-- 品牌像素 Logo：与 favicon 同构的方块下坠箭头（平涂，无渐变）

       **底板与描边跟随主题，6 个色块保持品牌原色。** 这两半的处置不同，
       理由也完全不同：

       · 色块（紫/紫/粉/紫/青/青）是写死的字面量。品牌识别依赖的是这一坨颜色
         在用户心里的固定形象，而 favicon 出现在浏览器标签、书签、磁贴上
         **不读 CSS 变量、也不跟随 data-theme**。如果方块跟着主题变，明亮主题下
         它变成蓝橙方块、标签页上还是紫粉青 —— 同一枚 logo 两处对不上。
       · 底板是**装饰**，不是识别符号。深色主题下它是深蓝底，明亮主题下就该是
         浅灰底，否则一枚深蓝方块浮在白底上，像没做完。所以底板与描边走令牌。

       favicon 保持深底 + 品牌色块：标签页在浏览器 chrome 里，底色本来就该是
       深色才有对比度，与页面主题无关。两边**色块必须一致，底板不要求一致**。
       见 ADR 0008「品牌标记豁免」一节。
       改配色前先读那一节 —— 这个文件已经被「顺手改成新配色」动过一次了。

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
    <rect width="64" height="64" rx="14" fill="var(--color-panel-2)" />
    <rect x="0.5" y="0.5" width="63" height="63" rx="13.5" stroke="var(--color-line)" />
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
