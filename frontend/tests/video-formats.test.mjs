/**
 * VideoResult.vue 的**模板结构**判据（工单 #19 第 2 项 · 工单 #23）。
 *
 * ## 为什么从 17 条削到 4 条
 *
 * 原有 17 条里，**12 条断言的是函数返回值**——它们把 computed 的箭头函数抠出来
 * 用 `new Function` 真执行（`runComputed` / `formatTitle`），本来就是真跑，
 * 不是字符串匹配。这 12 条已迁到 `video-result.mount.spec.mjs` 真挂载。
 *
 * 迁移不是改写，是**多覆盖一整类失败**。模板结构是：
 *
 *     <div v-if="video.formats?.length">        ← 外层
 *       <template v-if="videoFormats.length"> ... </template>
 *       <template v-if="audioFormats.length"> ... </template>
 *     </div>
 *
 * 而下面四条断言都只在**块内切片**（`sliceBetween` 从 `<template v-if=` 到
 * `</template>`）。于是把外层写成 `v-if="!video.formats?.length"`（取反了），
 * **这四条全部照常绿**，而界面上一个格式都没有——今天没有任何东西守着这一类失败。
 * 挂载版能红。
 *
 * 留在这里的是「模板里不许出现什么」那一类，它们的对象**就是模板文本**。
 * 挂载后看不到模板源码，迁过去会退化成「界面上没出现那串字」，而模板写了、
 * 只是没渲染到，恰恰是要抓的回归——判据见 `vitest.config.js` 顶部的说明。
 *
 * `emit('download', selectedFormat.value.format_id)` 那条也删了：它验的是
 * 代码文本，而 `video-result.mount.spec.mjs` 验的是「点下去真的 emit 出一个
 * 字符串」。同一个失败形态，后者更强。
 *
 * ## 判别法
 *
 * 把两个 `<template v-if>` 互换位置（或让其中一块恒不渲染），对应用例必须转红。
 * 「这一块整个不渲染」在挂载版里表现为 `{ exists: false }`，在这里表现为
 * `sliceBetween` 断言失败——两种形状都算响。
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

/**
 * 顶层 eager 读，**不要**挪进 `describe` 体。
 *
 * 模块加载失败是**响**的（退出码 1），describe 体抛异常是**哑**的（退出码 0，
 * runner 还会报 `tests N / pass 0 / fail 0`，真正的 ENOENT 埋在摘要下面，而
 * `init.sh` 只看退出码）。工单 #19 第 2 项实测过：把 VideoResult.vue 改名，
 * 读法在顶层则退出码 1，在 describe 体则退出码 0 而用例数悄悄变少。
 */
const raw = read('../src/components/VideoResult.vue')

/** <template> 段。lastIndexOf：模板里有 <template v-if> 这种同名标签。 */
const template = (() => {
  const end = raw.lastIndexOf('</template>')
  assert.ok(end > 0, 'VideoResult.vue 找不到顶层 </template>')
  return raw.slice(0, end)
})()

function sliceBetween(haystack, startNeedle, endNeedle) {
  const i = haystack.indexOf(startNeedle)
  assert.ok(i > 0, `找不到 ${startNeedle}`)
  const j = haystack.indexOf(endNeedle, i)
  assert.ok(j > i, `${startNeedle} 之后找不到 ${endNeedle}`)
  return haystack.slice(i, j)
}

/**
 * 惰性地取两块模板。
 *
 * ⚠️ 不要在 `describe` 体里就算出这两块：node 对 **describe 体里抛出的异常**
 * 给出的退出码是 0，runner 还会报 `tests 0 / pass 0 / fail 0` —— 测试一条没
 * 注册、没运行，断言错误被埋在摘要下面，而门禁只看退出码。
 * 实测（变异 F1）：把 `<template v-if="audioFormats.length">` 改成 `v-if="false"`，
 * 整份文件报「12 passed / 0 failed」、退出码 0，而那个 suite 的 5 条测试根本没跑。
 * 对照：模块顶层抛异常、测试体抛异常都给 1，只有 describe 体这一处是隐形的。
 */
const videoBlock = () =>
  sliceBetween(template, '<template v-if="videoFormats.length">', '</template>')
const audioBlock = () =>
  sliceBetween(template, '<template v-if="audioFormats.length">', '</template>')

describe('模板把两块分开渲染', () => {

  test('画质块里是画质选项，音频块里是音频选项', () => {
    assert.ok(videoBlock().includes('v-for="fmt in videoFormats"'))
    assert.ok(audioBlock().includes('v-for="fmt in audioFormats"'))
  })

  test('「选择画质」在画质块里，「纯音频」在音频块里', () => {
    // 位置判据：标签串到了另一块，用户就会在「纯音频」标题下挑画质，
    // 或者在画质列表里找音频 —— 两者都还是「看不到音频选项」。
    assert.ok(videoBlock().includes('选择画质'))
    assert.ok(!audioBlock().includes('选择画质'))
    assert.ok(audioBlock().includes('纯音频'))
    assert.ok(!videoBlock().includes('纯音频'))
  })

  test('两块都用了同一个 formatTitle 助手，不各写一套标题逻辑', () => {
    assert.equal(template.split('{{ formatTitle(fmt) }}').length - 1, 2)
  })

  test('按钮文案取自 downloadLabel，模板里不硬编码「下载视频」', () => {
    assert.ok(template.includes('downloadLabel'))
    assert.ok(!template.includes("'下载视频'"), '模板里又出现硬编码的下载视频')
    // 「下载中...」还在，那是进行态，与选中项无关
    assert.ok(template.includes('下载中...'))
  })
})