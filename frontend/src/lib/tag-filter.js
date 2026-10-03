/**
 * 标签筛选的纯逻辑。刻意不放进 .vue：
 *
 * 1. `<script setup>` 里不允许 `export`，写在组件里编译直接报错。
 * 2. 更重要的：node --test 只能直接 import 普通模块。把纯函数放进 SFC，
 *    测试就只能靠正则把它抠出来再 `new Function` 跑一遍 —— 而那条抠取
 *    路径本身是脆的（改个缩进就抠不出来，且失败形态是「断言恒真」）。
 *    独立模块直接 import，跑的就是真正上线的那份代码。
 */

/**
 * 多选 / 单选下的标签切换。
 *
 * 单选时点已选中的项 = 取消选择（点第二次回到全量）；多选时同理。
 * 写成两个独立分支而不是「统一取反」：单选下用取反会把 selected 变成
 * [tag, tag]，界面上看起来没反应，而数据已经脏了。
 *
 * @param {string[]} selected 已选中的标签
 * @param {string} tag 被点击的标签
 * @param {boolean} multiple true = 多选
 * @returns {string[]} 新的选中集合（**总是新数组**，便于 v-model 触发更新）
 */
export function toggleTag(selected, tag, multiple = true) {
  const current = Array.isArray(selected) ? selected : []
  if (!multiple) {
    return current.length === 1 && current[0] === tag ? [] : [tag]
  }
  return current.includes(tag)
    ? current.filter((t) => t !== tag)
    : [...current, tag]
}

/**
 * 收起态下这一行到底装不装得下。
 *
 * 用真实的 scrollWidth/clientWidth 判断，而不是「标签数 > N」：标签名长短
 * 不一，写死 N 要么留一大片空白，要么把某个标签从中间切开。
 *
 * +1 是留给亚像素的：等宽时 scrollWidth 常常比 clientWidth 大零点几，
 * 不加这一像素会在刚好放得下的时候凭空冒出「展开 N 个标签」。
 *
 * **这里只管收起态。** 「展开态要不要留按钮」是另一条规则，由
 * shouldShowToggle 拥有 —— 两条规则分散在两个函数里，历史上就出过一次
 * 「measure 把 overflowing 抹成 false，按钮跟着消失，用户再也收不回去」。
 */
export function needsExpand(scrollWidth, clientWidth) {
  return scrollWidth > clientWidth + 1
}

/**
 * 展开键该不该出现。
 *
 * `expanded` 为真时**必须**出现：那是「收起」的唯一入口。少了这一条，
 * 点开之后按钮自己消失，行已经换行了却收不回去 —— 真浏览器里点一次就
 * 能复现，静态断言看不出来。
 *
 * @param {number} tagCount 标签总数
 * @param {boolean} overflowing 收起态下实测是否溢出
 * @param {boolean} expanded 当前是否已展开
 */
export function shouldShowToggle(tagCount, overflowing, expanded) {
  if (tagCount <= 1) return false
  return Boolean(expanded) || Boolean(overflowing)
}

/**
 * 展开键上那个 N 是**真的被藏起来的个数**，只能量、不能算。
 *
 * 早先写的是 `tags.length - 1`，于是 6 个标签只溢出 2 个时也照样显示
 * 「展开其余 5 个标签」—— 按钮点得开，数字在骗人。
 *
 * 收 rights（每个 chip 的右边缘）而不是收 DOM：DOM 读数留在组件里，
 * 判断逻辑留在这里，node --test 就能拿真数据把两种实现区分开。
 *
 * @param {number[]} rights 每个标签右边缘相对视口的 x
 * @param {number} limit 标签行可视区域的右边缘
 * @returns {number} 被裁掉（即右边缘越过 limit）的标签个数
 */
export function countHiddenChips(rights, limit) {
  if (!Array.isArray(rights)) return 0
  return rights.filter((r) => r > limit).length
}

/**
 * 多个标签之间的筛选参数。
 *
 * **多个标签是并集（命中任一即列出），不是交集。** 交集在标签很少共现时
 * 直接返回空，界面上与「筛选坏了」完全一样；并集永远给得出东西，
 * 两个页面也都在 UI 上明写了「取并集」。
 *
 * 这里只做「多标签 → 查询串」这一件事，**不做**「空数组 = 不带 tag 参数」
 * 之外的任何判断：那个判断留在调用方，因为两个页面对「未选中」的处理
 * 理由不同（社区要退回公开列表，历史要回到全量）。
 */
export function tagsToQuery(selected) {
  const list = Array.isArray(selected) ? selected : []
  return list.length === 1 ? list[0] : list.join(',')
}

/** 解析回单值 / 多值。后端按逗号分隔传，兼容单值。 */
export function parseTagQuery(raw) {
  if (!raw) return []
  return String(raw).split(',').map((s) => s.trim()).filter(Boolean)
}
