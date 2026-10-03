/**
 * VideoResult.vue：纯音频选项要真的看得见、点得中、说得出话。
 *
 * 这个组件之前零测试覆盖。三处判据分别钉三个不同的失败形态：
 *
 *  1. **分组**（真跑，不做字符串匹配）：把 `videoFormats` / `audioFormats`
 *     两个 computed 的函数体抽出来，用 `new Function` 真执行。
 *     只断模板里出现了 `audioFormats` 这个名字毫无意义 —— 名字在、过滤逻辑
 *     写反了，界面上就是一个音频都不剩。
 *  2. **不丢选项**：两条桶的并集必须等于原始 formats。分组写错最常见的
 *     后果不是归错块，是某个选项**凭空消失**，而那正是原来「看不到音频」
 *     的形态。
 *  3. **按钮文案**：选中音频时按钮必须说「下载音频」。写着「下载音频」却
 *     递回一个带声的 mp4，不报错、文件也确实下来了 —— 这是最难查的一类。
 *
 * ⚠️ 判别法（AGENTS.md）：把下载请求整体打断（emit 的 format_id 恒为空、
 * 分组恒返回空数组），对应用例必须转红。所以下面每条判据是独立的 test()。
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

/**
 * 剥注释。`//` **只**整行剥 —— 行尾一刀切会把 URL 之类的字面量从中间砍断，
 * 而那往往正是断言的对象（工单 #13 就这么被吃掉过一条厂商地址判据）。
 */
function stripComments(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((l) => (l.trim().startsWith('//') ? '' : l))
    .join('\n')
}

const raw = read('../src/components/VideoResult.vue')
const src = stripComments(raw)

/** <template> 段。lastIndexOf：模板里有 <template v-if> 这种同名标签。 */
const template = (() => {
  const end = raw.lastIndexOf('</template>')
  assert.ok(end > 0, 'VideoResult.vue 找不到顶层 </template>')
  return raw.slice(0, end)
})()

const script = src.slice(src.indexOf('<script setup>'))

/** 抽出 `function NAME(...) {...}` 的完整源码。 */
function extractFn(code, name) {
  const at = code.indexOf(`function ${name}(`)
  assert.ok(at > 0, `抽不出 ${name}`)
  const i = code.indexOf('{', at)
  let depth = 0
  for (let j = i; j < code.length; j += 1) {
    if (code[j] === '{') depth += 1
    else if (code[j] === '}') {
      depth -= 1
      if (depth === 0) return code.slice(at, j + 1)
    }
  }
  throw new Error(`${name} 的大括号不配对`)
}

/** 抽出 `computed(...)` 的**参数**（配对小括号），即 `() => expr`。 */
function extractComputedBody(code, name) {
  const at = code.indexOf(`const ${name} = computed(`)
  assert.ok(at > 0, `抽不出 computed ${name}`)
  const open = code.indexOf('(', at + `const ${name} = `.length)
  let depth = 0
  for (let j = open; j < code.length; j += 1) {
    if (code[j] === '(') depth += 1
    else if (code[j] === ')') {
      depth -= 1
      if (depth === 0) return code.slice(open + 1, j)
    }
  }
  throw new Error(`${name} 的小括号不配对`)
}

/**
 * 真跑一个 computed：把它的箭头函数拿出来，当场调用。
 *
 * `computed(...)` 的参数是**一个函数**（`() => expr`），不是表达式本身。
 * 直接 `return ${body}` 会把那个函数本身返回出去 —— 断言拿到的是函数，
 * 不是分组结果，而它照样不报错。`(${fn})` 那对括号就是干这个的。
 */
function runComputed(code, name, argName, argValue) {
  const fn = extractComputedBody(code, name)
  // eslint-disable-next-line no-new-func
  return new Function(argName, `return (${fn})(${argName})`)(argValue)
}

function sliceBetween(haystack, startNeedle, endNeedle) {
  const i = haystack.indexOf(startNeedle)
  assert.ok(i > 0, `找不到 ${startNeedle}`)
  const j = haystack.indexOf(endNeedle, i)
  assert.ok(j > i, `${startNeedle} 之后找不到 ${endNeedle}`)
  return haystack.slice(i, j)
}

const videoFormatsOf = (props) => runComputed(src, 'videoFormats', 'props', props)
const audioFormatsOf = (props) => runComputed(src, 'audioFormats', 'props', props)
const labelOf = (selectedFormat) =>
  runComputed(src, 'downloadLabel', 'selectedFormat', selectedFormat)

// 真函数（同样抽出来跑，不做字符串匹配）
const formatTitle = new Function(
  'fmt',
  `${extractFn(src, 'formatTitle')}; return formatTitle`
)()

// ── 造数据 ────────────────────────────────────────────────

const VIDEO = { format_id: 'v-1080', kind: 'video', resolution: '1920x1080', label: '1080p MP4' }
const VIDEO_MERGED = { format_id: 'bestvideo+bestaudio/best', kind: 'video', resolution: '1920x1080', label: '最佳' }
const AUDIO = { format_id: 'a-320', kind: 'audio', abr: 320, ext: 'm4a', resolution: '', label: '320kbps M4A (仅音频, 9.1MB)' }
const AUDIO_DOUYIN = { format_id: 'douyin_audio', kind: 'audio', abr: null, ext: 'mp3', resolution: '', label: '纯音频 MP3 (只下音频, 未知大小)' }

const propsOf = (formats) => ({ video: { formats } })

// ── 分组 ──────────────────────────────────────────────────

describe('VideoResult 把音频和视频分成两块', () => {
  test('音频选项进入音频块，不混进画质块', () => {
    const props = propsOf([VIDEO, AUDIO, VIDEO_MERGED])
    assert.deepEqual(videoFormatsOf(props).map((f) => f.format_id), ['v-1080', 'bestvideo+bestaudio/best'])
    assert.deepEqual(audioFormatsOf(props).map((f) => f.format_id), ['a-320'])
  })

  test('没有任何选项被吞掉：两块的并集等于原始列表', () => {
    const formats = [VIDEO, AUDIO, VIDEO_MERGED, AUDIO_DOUYIN]
    const props = propsOf(formats)
    const seen = [...videoFormatsOf(props), ...audioFormatsOf(props)]
    assert.equal(seen.length, formats.length, '有选项凭空消失了')
    assert.deepEqual(
      seen.map((f) => f.format_id).sort(),
      formats.map((f) => f.format_id).sort()
    )
  })

  test('音频块里不得混进视频选项', () => {
    const props = propsOf([VIDEO, AUDIO])
    for (const f of audioFormatsOf(props)) assert.equal(f.kind, 'audio')
  })

  test('自报了 kind 缺失的旧格式仍留在画质块里', () => {
    // 反向缺省是刻意的：后端哪天回归漏了 kind，用户最坏看到归错块的选项，
    // 而不是凭空消失的选项。「每条格式都自报 kind」由后端测试钉住。
    const legacy = { format_id: 'old', resolution: '640x360', label: '360p' }
    const props = propsOf([legacy, AUDIO])
    assert.deepEqual(videoFormatsOf(props).map((f) => f.format_id), ['old'])
    assert.deepEqual(audioFormatsOf(props).map((f) => f.format_id), ['a-320'])
  })

  test('只有音频可选时，画质块为空（不硬凑一个假画质）', () => {
    const props = propsOf([AUDIO, AUDIO_DOUYIN])
    assert.equal(videoFormatsOf(props).length, 0)
    assert.equal(audioFormatsOf(props).length, 2)
  })

  test('formats 缺失时不炸', () => {
    for (const props of [{}, { video: {} }, { video: { formats: null } }]) {
      assert.deepEqual(videoFormatsOf(props), [])
      assert.deepEqual(audioFormatsOf(props), [])
    }
  })
})

// ── 标题 ──────────────────────────────────────────────────

describe('格式卡片的标题', () => {
  test('视频选项显示分辨率', () => {
    assert.equal(formatTitle(VIDEO), '1920x1080')
    assert.equal(formatTitle(VIDEO_MERGED), '1920x1080')
  })

  test('音频选项显示码率，而不是一个空白的分辨率', () => {
    assert.equal(formatTitle(AUDIO), '320 kbps')
    // 抖音的 mp3 有码率，只是我们没去查。空白标题比"不知道"更糟：
    // 它看起来像一个渲染坏了的卡片。
    assert.equal(formatTitle(AUDIO_DOUYIN), 'MP3')
  })

  test('音频标题永远不为空', () => {
    for (const f of [AUDIO, AUDIO_DOUYIN, { kind: 'audio', abr: null, ext: '' }]) {
      assert.notEqual(formatTitle(f).trim(), '')
    }
  })
})

// ── 下载按钮文案 ──────────────────────────────────────────

describe('下载按钮文案跟着选中项走', () => {
  test('选中音频时说下载音频', () => {
    assert.equal(labelOf({ value: AUDIO }), '下载音频')
  })

  test('选中视频时说下载视频', () => {
    assert.equal(labelOf({ value: VIDEO }), '下载视频')
    assert.equal(labelOf({ value: VIDEO_MERGED }), '下载视频')
  })

  test('什么都没选时是下载视频（按钮此刻是禁用的）', () => {
    assert.equal(labelOf({ value: null }), '下载视频')
    assert.equal(labelOf({ value: undefined }), '下载视频')
  })
})

// ── 模板结构 ──────────────────────────────────────────────

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

  test('下载仍然回传 format_id，不是整条格式对象', () => {
    assert.ok(src.includes("emit('download', selectedFormat.value.format_id)"))
  })
})
