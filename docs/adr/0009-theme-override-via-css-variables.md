---
status: accepted
date: 2026-10-03
---

# 亮暗双主题用 CSS 变量按主题覆盖实现，不新建语义 token

全站提供暗色（默认）与明亮两套主题。切换时在根元素上写 `data-theme="dark" | "light"`，
两套主题共用**同一批** Tailwind 令牌名，值不同。

## 依据：Tailwind v4 的 `@theme` 编译产物就是 CSS 变量

`@theme` 里的 `--color-gray-50` 等会被编译到 `:root` 上，工具类 `bg-gray-50` 编译成
`background-color: var(--color-gray-50)`。因此在 `[data-theme="light"]` 下重写同名变量，
659 处色类**零迁移**自动跟随。这是本决策成立的全部技术前提。

## Consequences

**`gray-50` 继续名不副实，这是有意保留的债。** 既有约定里 `gray-50` 是「最深的背景」而非「浅灰 50」
（`style.css` 原注释：「数值越小越深，适配深色面板语义」）。这个反向语义在结构上**排除了**
按数值直觉使用色阶，但它同时也是让两套主题共用一套类名成为可能的前提。
新建 `bg/surface/line/muted` 等真名 token 并迁移 659 处是更诚实的做法，代价是全量回归风险；
本轮选择保留债，并在 `@theme` 处以注释显式标注，不让它变成「没人知道为什么」的隐式约定。

**7 个深色专属块必须成对分叉**：`body` 背景与网格线、`.prose` 全块、`.summary-content` 全块、
`.skeleton`/`.animate-shimmer`、`.card-hover:hover`、自定义滚动条、`::selection`。
它们直接写裸色值，不走令牌，是两套主题分叉的主要工作量。

**首屏防闪需要内联脚本。** 主题存 localStorage，若等 Vue 挂载后才应用，暗色用户会看到一帧白底。
因此 `index.html` 内放一段同步内联脚本在 `<head>` 里读 localStorage 并写 `data-theme`。

## Considered Options

- 新建语义 token 并全量迁移 659 处：命名诚实、无遗留债，但 diff 巨大、回归面覆盖全站。
- 反转 `gray` 语义让 `gray-50` 真的是浅灰：最彻底，但要重排全部 206 处 `gray/slate` 用法的明暗含义。
- 亮色另起一套独立类名：暗色不动、改动小，但两套并行长期必然漂移，且违背 UI 风格统一的要求。
