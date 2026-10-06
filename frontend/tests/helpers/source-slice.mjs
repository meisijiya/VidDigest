/**
 * 源码断言的**边界工具**。
 *
 * ## 为什么零守卫的切片是错的
 *
 * `src.slice(src.indexOf(anchor), src.indexOf(other))` 在锚点找不到时不会报错，
 * 它返回 `-1`，而 `slice` 把负数当成「从末尾倒数」：
 *
 * - 起点 -1 → 从**最后一个字符**开始切
 * - 终点 -1 → 切到**倒数第二个字符**为止
 *
 * 两种情况都不是空串，而是**把观察范围悄悄放大到整个文件**。
 * 而整个文件里几乎总有那几行，于是断言恒真 —— 它看起来一直在守着，
 * 实际什么都拦不住，而且**永远绿**。
 *
 * 触发它的恰恰是最平常的重构：函数改名、锚点挪位置、块被合并。
 * 一次正常的重命名就能让一条守卫静默失效，且没有任何东西会变红。
 *
 * 同族：`const after = src.slice(at); after.slice(0, after.indexOf('</g>'))`
 * 在 `</g>` 全部消失时同样退化成「切到末尾」，嵌套断言一起恒真。
 *
 * ## 两条硬规矩
 *
 * 1. **两端都必须显式断言找到**。找不到时让这条测试红，
 *    而不是让它观察一个更大的范围然后继续绿。
 * 2. **按「每条用例断言的对象」选边界**，不要按「这段代码大概在哪」。
 *    只查全文子串的断言（本文件之外的常见写法）等价于没有函数体范围：
 *    同一个串出现在别的函数里，照样绿。
 */
import assert from 'node:assert/strict'

/**
 * 两端都带守卫的切片：**包含** `startAnchor`，**排除** `endAnchor`。
 *
 * @param {string} src        已归一化行尾的源码
 * @param {string} startAnchor 起始锚点（必须是 src 里真实存在的串）
 * @param {string} endAnchor   结束锚点，必须出现在 startAnchor **之后**
 * @param {string} label       给人看的名字，失败信息里用
 */
export function sliceBetween(src, startAnchor, endAnchor, label) {
  const start = src.indexOf(startAnchor)
  assert.ok(start >= 0, `${label}：找不到起始锚点 ${JSON.stringify(startAnchor)} —— `
    + '锚点多半是重命名/挪位置造成的，改判据前先确认这条要守的行为还在')
  const from = start + startAnchor.length
  const end = src.indexOf(endAnchor, from)
  assert.ok(end >= 0, `${label}：起始锚点之后找不到结束锚点 ${JSON.stringify(endAnchor)} —— `
    + '要么它被挪到了起始锚点之前，要么这块的写法整体变了')
  return src.slice(start, end)
}

/**
 * 用大括号配对抽出整个函数体。
 *
 * 比 `sliceBetween` 强在**它不依赖结束锚点**：块结束了就自然结束，
 * 不必猜「下一个函数声明在哪」。`extractFn(src, 'deleteHistory')` 对
 * `export async function deleteHistory(...)` 同样有效。
 *
 * **必须先配平参数列表**，再找函数体开头。直接找第一个 `{` 是个洞：
 * 参数里本来就可以有花括号 —— 解构（`{ force = false }`）或默认值
 * （`params = {}`）——配对从那里开始会立刻归零，于是抽出来的是
 * `function deleteHistory(id, { force = false }` 这种**看着像函数、
 * 其实缺了半个函数体**的片段。断言对象通常不在里面，于是这条断言
 * 恒红或恒绿，而两种表现都被误读成「被测代码有问题」。
 * 这个洞不报任何错，它只会安静地抽错。
 *
 * 大括号不配对时抛错而不是返回半截 —— 半截会让后续断言在一个
 * 「看起来像函数体」的片段上判，而那个片段可能根本不含断言对象。
 *
 * @param {string} src   已归一化行尾的源码
 * @param {string} name  函数名（不带 `function ` 前缀）
 */
export function extractFn(src, name) {
  const at = src.indexOf(`function ${name}(`)
  assert.ok(at > 0, `抽不出 ${name} —— 函数改名了？`)
  const paren = src.indexOf('(', at)
  let pDepth = 0
  let bodyStart = -1
  for (let i = paren; i < src.length; i += 1) {
    if (src[i] === '(') pDepth += 1
    else if (src[i] === ')') {
      pDepth -= 1
      if (pDepth === 0) {
        bodyStart = src.indexOf('{', i)
        break
      }
    }
  }
  assert.ok(bodyStart > 0, `${name} 的参数列表没配平，或找不到函数体起始的 {`)
  let depth = 0
  for (let j = bodyStart; j < src.length; j += 1) {
    if (src[j] === '{') depth += 1
    else if (src[j] === '}') {
      depth -= 1
      if (depth === 0) return src.slice(at, j + 1)
    }
  }
  throw new Error(`${name} 的大括号不配对`)
}