/**
 * VideoResult.vue 真挂载（工单 #19 第 2 项 · 工单 #23）。
 *
 * ## 为什么这 12 条从 text 迁到 mount
 *
 * `video-formats.test.mjs` 里有 12 条断言的是**函数返回值**（把 computed 的箭头
 * 函数抠出来 `new Function` 真跑），不是源码文本。它们已经是真跑，不是字符串匹配——
 * 迁到挂载不会变弱，而且**多覆盖一整类失败**：
 *
 * 模板结构是「外层 `<div v-if="video.formats?.length">` 包两个
 * `<template v-if>`」。而文本版的四组块内断言都只在**块内切片**：
 *
 *     sliceBetween(template, '<template v-if="videoFormats.length">', '</template>')
 *
 * 所以把外层写成 `v-if="!video.formats?.length"`（取反了），**块内的断言全部照常绿**，
 * 而界面上一个格式都没有——今天没有任何东西守着这一类失败。挂载版能红。
 *
 * 反过来，挂载版抓不到「模板里不许出现某个字面量」那类判据，所以那 5 条模板断言
 * 留在 text 那边（其中 `emit('download', format_id)` 那条被本文件替代，因为验行为
 * 比验代码文本更强）。两层并存，不互相替代。
 *
 * ## 判据只走「用户看到什么」
 *
 * `<script setup>` 不暴露内部状态，DOM 是唯一出口。所以本文件不 import 也不调用
 * `videoFormats` / `audioFormats` / `downloadLabel`——那些是组件的实现细节，而用户
 * 关心的是「音频选项出现在哪一块」「按钮此刻写着什么」。
 *
 * ## 两个块的分界就是两个 <label>
 *
 * `<template v-if>` 编译后塌陷，`<label>` 与 `.grid` div 都成为外层 div 的直接子
 * 元素。所以「某块里的按钮」= 该 label 之后、下一个 label 之前的按钮。
 * 用「该块不存在」表达「这一块没渲染」——只渲染音频时画质块**连 label 都没有**，
 * 这比断言「有个空块」更贴近用户看到的东西。
 */
import { describe, test, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import VideoResult from '../src/components/VideoResult.vue'

// ── 造数据 ─────────────────────────────────────────────────
//
// `label` 字段在每条里都唯一，所以它就是 DOM 上的身份标识——模板把它渲染在
// formatTitle 之下（`{{ formatTitle(fmt) }}` / `{{ fmt.label }}`），所以
// `button.textContent` 同时含标题与 label，断言用 label 子串即可。
// :key 是 format_id 但 key 不渲染到 DOM，拿不到。

const VIDEO = { format_id: 'v-1080', kind: 'video', resolution: '1920x1080', label: '1080p MP4' }
const VIDEO_MERGED = { format_id: 'bestvideo+bestaudio/best', kind: 'video', resolution: '1920x1080', label: '最佳' }
const AUDIO = { format_id: 'a-320', kind: 'audio', abr: 320, ext: 'm4a', resolution: '', label: '320kbps M4A' }
const AUDIO_DOUYIN = { format_id: 'douyin_audio', kind: 'audio', abr: null, ext: 'mp3', resolution: '', label: '纯音频 MP3' }
/** kind 缺失的旧格式：反向缺省是刻意的，最坏是归错块而不是凭空消失。 */
const LEGACY = { format_id: 'old', resolution: '640x360', label: '360p' }

const VIDEO_LABEL = '选择画质'
const AUDIO_LABEL = '纯音频'

function mountWith(formats) {
  return mount(VideoResult, {
    props: {
      video: {
        title: '一个标题',
        uploader: '作者',
        platform: 'B站',
        duration_string: '10:00',
        thumbnail: '',
        formats,
      },
    },
  })
}

/**
 * 取某个块里的按钮。块不存在时返回 `{ exists: false }`。
 *
 * 返回原生元素而不是 wrapper 是为了让点击走 `el.click()`——Vue 用
 * addEventListener 挂监听，原生 click 照样触发；但那样就没有 `trigger` 的
 * 事件名与选项了，这个组件只监听 click，够用。
 */
function blockOf(wrapper, labelText) {
  const labels = [...wrapper.element.querySelectorAll('label')]
  const start = labels.findIndex((l) => l.textContent.trim() === labelText)
  if (start < 0) return { exists: false, texts: [], elements: [] }

  const end = start + 1 < labels.length ? labels[start + 1] : null
  const texts = []
  const elements = []
  for (let node = labels[start].nextElementSibling; node && node !== end; node = node.nextElementSibling) {
    for (const btn of node.querySelectorAll('button')) {
      texts.push(btn.textContent)
      elements.push(btn)
    }
  }
  return { exists: true, texts, elements }
}

/** 块里有某个 label 标识的选项吗 */
const has = (block, label) => block.texts.some((t) => t.includes(label))

/** 下载按钮：正文含「下载视频 / 下载音频 / 下载中...」的那个。 */
function downloadButton(wrapper) {
  const hit = wrapper.findAll('button').find((b) => /下载(视频|音频|中)/.test(b.text()))
  if (!hit) throw new Error(`界面上没有下载按钮，只有：${wrapper.text()}`)
  return hit
}

const clickAndSettle = async (el) => {
  el.click()
  await nextTick()
}

// ── 分组 ───────────────────────────────────────────────────

describe('VideoResult 把音频和视频分成两块', () => {
  test('音频选项在音频块、画质选项在画质块，两块各归各位', () => {
    const w = mountWith([VIDEO, AUDIO, VIDEO_MERGED])
    const video = blockOf(w, VIDEO_LABEL)
    const audio = blockOf(w, AUDIO_LABEL)

    expect(has(video, '1080p MP4')).toBe(true)
    expect(has(video, '最佳')).toBe(true)
    expect(has(audio, '320kbps M4A')).toBe(true)
    // 反向：音频绝不出现在画质块里，视频绝不出现在音频块里。
    // 标签串到另一块，用户就会在「纯音频」标题下挑画质 —— 形态与
    // 「看不到音频选项」完全一样，所以这条与文本版的位置断言是同一个失败。
    expect(has(video, '320kbps M4A')).toBe(false)
    expect(has(audio, '1080p MP4')).toBe(false)
    expect(has(audio, '最佳')).toBe(false)
  })

  test('没有任何选项被吞掉：两块加起来等于原始列表', () => {
    // 分组写错最常见的形态不是归错块，是某个选项**凭空消失**，
    // 而那正是当初让「看不到音频」看不见的原因。
    const formats = [VIDEO, AUDIO, VIDEO_MERGED, AUDIO_DOUYIN, LEGACY]
    const w = mountWith(formats)
    const all = [...blockOf(w, VIDEO_LABEL).texts, ...blockOf(w, AUDIO_LABEL).texts]

    expect(all).toHaveLength(formats.length)
    for (const f of formats) {
      expect(all.some((t) => t.includes(f.label)), `选项「${f.label}」凭空消失了`).toBe(true)
    }
  })

  test('自报了 kind 缺失的旧格式仍留在画质块里', () => {
    const w = mountWith([LEGACY, AUDIO])
    expect(has(blockOf(w, VIDEO_LABEL), '360p')).toBe(true)
    expect(has(blockOf(w, AUDIO_LABEL), '360p')).toBe(false)
    expect(has(blockOf(w, AUDIO_LABEL), '320kbps M4A')).toBe(true)
  })

  test('只有音频可选时，画质块整个不存在（不硬凑一个假画质）', () => {
    const w = mountWith([AUDIO, AUDIO_DOUYIN])
    // 「不存在」而不是「空块」：一个空标题下面挂一个空列表，界面上就是一段
    // 没有内容的框，而那正是「看不到音频」那类问题的近亲。
    expect(blockOf(w, VIDEO_LABEL).exists).toBe(false)
    const audio = blockOf(w, AUDIO_LABEL)
    expect(audio.exists).toBe(true)
    expect(audio.texts).toHaveLength(2)
  })

  test('formats 缺失 / 为空 / 为 null 时都不炸，也不渲染两个块', () => {
    for (const formats of [undefined, null, []]) {
      const w = mountWith(formats)
      expect(blockOf(w, VIDEO_LABEL).exists, `formats=${formats}`).toBe(false)
      expect(blockOf(w, AUDIO_LABEL).exists, `formats=${formats}`).toBe(false)
      // 下载按钮永远在：它是组件的常驻部分，不该跟着 formats 消失
      expect(downloadButton(w).exists()).toBe(true)
    }
  })

  test('「选择画质」在「纯音频」之前', () => {
    const w = mountWith([VIDEO, AUDIO])
    const labels = [...w.element.querySelectorAll('label')]
    const vi = labels.findIndex((l) => l.textContent.trim() === VIDEO_LABEL)
    const ai = labels.findIndex((l) => l.textContent.trim() === AUDIO_LABEL)
    expect(vi).toBeGreaterThanOrEqual(0)
    expect(ai).toBeGreaterThan(vi)
  })
})

// ── 标题 ───────────────────────────────────────────────────

describe('格式卡片的标题', () => {
  test('视频选项显示分辨率', () => {
    const w = mountWith([VIDEO, VIDEO_MERGED])
    const texts = blockOf(w, VIDEO_LABEL).texts
    expect(texts.every((t) => t.includes('1920x1080'))).toBe(true)
  })

  test('音频选项显示码率，而不是一个空白的分辨率', () => {
    const w = mountWith([AUDIO])
    expect(blockOf(w, AUDIO_LABEL).texts.join()).toContain('320 kbps')
  })

  test('码率与容器名都拿不到时，退到「纯音频」而不是空白', () => {
    // 抖音的 mp3 有码率，只是我们没去查。空白标题比「不知道」更糟：
    // 它看起来像一个渲染坏了的卡片。
    const bare = { format_id: 'bare', kind: 'audio', abr: null, ext: '', label: '某条无码率音频' }
    const w = mountWith([bare])
    const text = blockOf(w, AUDIO_LABEL).texts.join()
    expect(text).toContain('纯音频')
    // 不是空白：标题区与 label 区合起来必须有可见文字
    expect(text.trim().length).toBeGreaterThan(0)
  })
})

// ── 下载按钮文案 ───────────────────────────────────────────

describe('下载按钮文案跟着选中项走', () => {
  test('选中音频时说「下载音频」', async () => {
    const w = mountWith([VIDEO, AUDIO])
    await clickAndSettle(blockOf(w, AUDIO_LABEL).elements[0])
    expect(downloadButton(w).text()).toContain('下载音频')
  })

  test('选中视频时说「下载视频」', async () => {
    const w = mountWith([VIDEO_MERGED, AUDIO])
    await clickAndSettle(blockOf(w, VIDEO_LABEL).elements[0])
    expect(downloadButton(w).text()).toContain('下载视频')
    // 换回来也成立：按钮文案要跟着**当前**选中项走，而不是「点过一次就定了」
    await clickAndSettle(blockOf(w, AUDIO_LABEL).elements[0])
    expect(downloadButton(w).text()).toContain('下载音频')
  })

  test('什么都没选时是「下载视频」且按钮禁用', () => {
    const w = mountWith([VIDEO, AUDIO])
    const btn = downloadButton(w)
    expect(btn.text()).toContain('下载视频')
    expect(btn.attributes('disabled')).toBeDefined()
  })
})

// ── 下载回传 ───────────────────────────────────────────────

describe('下载回传 format_id，不是整条格式对象', () => {
  test('点音频选项后 emit 出去的是字符串 id', async () => {
    // 文本版只能验「代码里写了 `emit('download', …format_id)`」；
    // 这里验的是**点下去真的 emit 出一个字符串**——同一个失败形态，更强。
    const w = mountWith([VIDEO, AUDIO])
    await clickAndSettle(blockOf(w, AUDIO_LABEL).elements[0])
    await clickAndSettle(downloadButton(w).element)

    const emitted = w.emitted('download')
    expect(emitted, '点了下载却没有任何 download 事件').toBeTruthy()
    expect(emitted[0][0]).toBe('a-320')
    expect(typeof emitted[0][0]).toBe('string')
  })

  test('什么都没选时点下载不 emit', async () => {
    // 按钮是 disabled 的，但 handleDownload 自己也有守卫（`:160`）。
    // 断言的是「不 emit」——多发一次会让上层白跑一遍真实下载。
    const w = mountWith([VIDEO, AUDIO])
    const btn = downloadButton(w)
    await clickAndSettle(btn.element)
    expect(w.emitted('download')).toBeFalsy()
  })
})