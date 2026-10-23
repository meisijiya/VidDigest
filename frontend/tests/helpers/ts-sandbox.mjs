/**
 * 文本沙箱跑 TypeScript 源码时的**转译层**。
 *
 * ## 为什么需要这一层
 *
 * 本仓有一类测试的测法是：把 `.vue` 的 `<script setup>` 或 `.ts` 模块里的
 * 函数**抠出来当字符串**，放进 `with (scope)` 沙箱真跑一遍
 * （见 community-tags-ui.test.mjs 的 withScope、admin-ui.test.mjs 的
 * makeSaveSandbox）。这样跑的是源码里那一份，不是测试里重写一遍的近似实现。
 *
 * 前端迁到 TypeScript 之后，这层沙箱就撞上了：`new Function` 按 **JavaScript**
 * 解析参数，而抠出来的源码里有 `as`、类型标注、泛型 —— 于是
 * `SyntaxError: Unexpected identifier 'as'`。
 *
 * ## 为什么不在测试里改断言
 *
 * 撞上的是**沙箱的语言**，不是被测行为。把断言删掉或改弱，等于让「组件状态
 * 层面真的对」这件事从此无人看守。所以这里做的是：抽出来的源码先经 esbuild
 * 从 TS 转成 JS，再送进沙箱 —— 断言一个字不动，跑的仍然是源码里那一份。
 *
 * ## 边界
 *
 * - 只处理**语法**（类型标注、`as`、泛型）。转译不改变运行时行为，
 *   所以「转译后行为变了」不是这套工具的已知风险面。
 * - esbuild 之前只是 vite 的传递依赖。直接 import 一个没写进
 *   package.json 的传递依赖，等于让测试在某个版本升级后突然红掉 ——
 *   所以它被显式写进 devDependencies。
 * - 真挂载那一侧（*.spec.mjs，vitest + @vue/test-utils）不经过这一层：
 *   那里由 vite 编译 .vue，不需要手工转译。
 */
import { transformSync } from 'esbuild'

/**
 * 把一段 TypeScript 源码转成等价的 JavaScript 源码。
 *
 * @param {string} code 抠出来的源码片段（函数声明、函数体、表达式都行）
 * @param {string} label 失败信息里用的名字，一般是被抠的源文件名
 * @returns {string} 可直接交给 `new Function` 的 JavaScript 源码
 */
export function transpileTs(code, label = 'sandbox.ts') {
  const result = transformSync(code, {
    loader: 'ts',
    target: 'es2022',
    sourcefile: label,
  })
  return result.code
}
