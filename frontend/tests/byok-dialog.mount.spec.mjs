/**
 * ByokDialog.vue 真挂载（工单 #19 第 2 项的真空）。
 *
 * ## 这个文件补的是**行为**，不是源码文本
 *
 * 此前 `byok.test.mjs` 里对这个组件的判据全是「源码里不许出现什么」
 * （切片的函数体、模板里的插值）。于是这条最要紧的链路一次都没被走过：
 *
 *   填 key → 选厂商 → 点保存 → 弹窗关闭 + **真落盘** + DOM 里不再有明文
 *
 * 三个后果各自有独立的失效形状，且**都不报错**：
 *   - 不落盘 → 用户下次打开发现 key 没了，凭据静默消失
 *   - 不关闭 → 点完弹窗还开着，用户以为没生效又点一次
 *   - DOM 留明文 → 进浏览器自动填充，也进「检查元素」与截图
 *
 * ## 为什么**不能** mock lib/byok.js
 *
 * 本文件的判据对象恰恰是「真的写进了 localStorage」。mock 掉 byok 就等于
 * 把判据对象本身拿走，剩下的是一个对着空壳断言的空壳。所以这里只 mock
 * 厂商清单的来源（服务端），凭据链路走真实现 —— 与 `byok-center.test.mjs`
 * 同一套路。
 *
 * ## 为什么 localStorage 要用 vi.hoisted
 *
 * `lib/byok.js` 顶层就有 `let state = read()`，而 ESM 的 import 全部先于模块体
 * 执行 —— 写在模块顶层的 `globalThis.localStorage = …` 会**晚一步**，
 * 真值读到的是 jsdom 那个空的。`vi.hoisted` 是 vitest 提供的唯一
 * 「早于 import」的时机。
 *
 * ## 判据只走「用户看到什么」与「盘上留下什么」
 *
 * `<script setup>` 不暴露内部状态，DOM 与 localStorage 是仅有的两个出口。
 * 本文件不 import 也不调用 `save` / `usePlatform` / `clear` ——
 * 那些是被验对象，不是判据。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const { ls } = vi.hoisted(() => {
  const s = {}
  const impl = {
    getItem: (k) => s[k] ?? null,
    setItem: (k, v) => { s[k] = String(v) },
    removeItem: (k) => { delete s[k] },
    clear: () => { for (const k of Object.keys(s)) delete s[k] },
    _dump: () => ({ ...s }),
  }
  // 必须走 stubGlobal 而不是 `globalThis.localStorage = impl`：
  // jsdom 的 window.localStorage 是**只读 getter**，直接赋值抛
  // "Cannot set property localStorage of [object Window] which has only a getter"，
  // 而那是在 suite 加载期抛的 —— 一条测试都没注册，形态正是 AGENTS.md 记的
  // 「describe 体抛异常是隐形的」的同款（这里更早，连 describe 都没进）。
  // byok-center.test.mjs 能直接赋值是因为它走 node --test，根本没有 jsdom。
  vi.stubGlobal('localStorage', impl)
  return { ls: impl }
})

vi.mock('../src/api/models.js', () => ({
  fetchPublicModelCatalog: vi.fn(),
}))

import ByokDialog from '../src/components/ByokDialog.vue'
import { fetchPublicModelCatalog } from '../src/api/models.js'
// clear 是**被验对象的函数**，不是判据。这里用它只是为了把 byok 的模块闭包归零，
// 见 beforeEach 的注释 —— 理由比「顺手清一下」重要得多。
import { clear } from '../src/lib/byok.js'

const SENTINEL = 'sk-byok-dialog-ZZUNIQUEZZ'
const CATALOG = [
  {
    id: 'deepseek', label: 'DeepSeek', baseUrl: 'https://api.deepseek.com',
    defaultModel: 'deepseek-chat', hint: '国产模型',
  },
  {
    id: 'moonshot', label: 'Moonshot', baseUrl: 'https://api.moonshot.cn/v1',
    defaultModel: 'kimi', hint: '',
  },
]

beforeEach(() => {
  // reset 而不是 clear：clear 只清调用历史、不清实现，漏设前提的用例会默默
  // 继承上一条的种子，然后断言了另一个场景还照样绿。
  vi.resetAllMocks()
  // ⚠️ 两步都要，且顺序不能反。
  //
  // `ls.clear()` 只清**盘上的**数据，而 `lib/byok.js` 的模块闭包 `state`
  // 仍然攥着上一条用例留下的 apiKey —— 它从外部没有任何办法重置。
  // 于是下一条用例只要碰一次 `chooseProvider`（它会 `save({ apiKey: state.apiKey })`），
  // 上一条的 key 就被**顺手写回盘上**，而那条用例正想断言「盘上不该有 key」。
  // 实测症状：`expected 'sk-byok-dialog-ZZUNIQUEZZ' to be undefined`，
  // 且报错的用例与真正做错事的那条完全无关 —— 排查方向会整个跑偏。
  //
  // 先 clear()（归零闭包，它也会写一次盘）再 ls.clear()（清盘），
  // 两边一起归零，顺序反过来又会留残留。
  clear()
  ls.clear()
  fetchPublicModelCatalog.mockResolvedValue({ items: CATALOG })
})

async function mountDialog(props = {}) {
  // teleport 要 stub：组件把整块 Teleport 到 body，不 stub 的话 wrapper 里找不到。
  const w = mount(ByokDialog, {
    props: { visible: true, loggedIn: true, ...props },
    global: { stubs: { teleport: true } },
  })
  await flushPromises()
  return w
}

const buttonByText = (w, text) => w.findAll('button').find((b) => b.text().includes(text))

async function pickProvider(w, id) {
  await w.find('#byok-mode').setValue(id)
  await flushPromises()
}

async function typeKey(w, value) {
  const input = w.find('#byok-key')
  expect(input.exists(), '选具体厂商后应该出现 key 输入框').toBe(true)
  await input.setValue(value)
  return input
}

// ── 可见性与清单 ───────────────────────────────────────────

describe('ByokDialog 挂载 · 可见性与清单', () => {
  test('visible 为假时什么都不渲染', async () => {
    // 默认值就是 false（`props.visible`），所以「不传」是真实形状。
    // 只写这条会恒真吗？不会 —— 下面每一条都显式传 true 并断言有内容，
    // 两者合起来才证明 v-if 真在起作用。
    const w = await mountDialog({ visible: false })
    expect(w.html()).not.toContain('API Key 与调用端点')
    expect(w.find('[role="dialog"]').exists()).toBe(false)
    expect(w.find('#byok-key').exists()).toBe(false)
  })

  test('visible 为真时渲染出对话框，带 dialog 语义', async () => {
    const w = await mountDialog()
    expect(w.find('[role="dialog"]').exists(), '弹窗没有 role="dialog"，屏幕阅读器读不出来').toBe(true)
    expect(w.find('[aria-modal="true"]').exists()).toBe(true)
    expect(w.html()).toContain('API Key 与调用端点')
  })

  test('厂商清单来自服务端：挂载即拉一次', async () => {
    await mountDialog()
    expect(fetchPublicModelCatalog).toHaveBeenCalledTimes(1)
  })

  test('清单拉不到时说清「暂时选不了厂商」，不是静默一个空下拉', async () => {
    fetchPublicModelCatalog.mockRejectedValue(new Error('boom'))
    const w = await mountDialog()
    expect(w.html()).toContain('厂商清单没能加载出来')
  })

  test('清单里的厂商出现在下拉里', async () => {
    const w = await mountDialog()
    const opts = w.findAll('#byok-mode option').map((o) => o.text())
    expect(opts.join(',')).toContain('DeepSeek')
  })
})

// ── 保存这条主链路 ─────────────────────────────────────────

describe('ByokDialog 挂载 · 保存', () => {
  test('填 key → 保存 → 关闭 + 真落盘 + DOM 不再留明文', async () => {
    const w = await mountDialog()
    await pickProvider(w, 'deepseek')
    await typeKey(w, SENTINEL)

    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(w.emitted('close'), '保存后没有 emit close —— 用户以为没生效，会再点一次').toBeTruthy()
    expect(ls._dump().viddigest_user_api_key, '保存后 key 没落到 localStorage').toBe(SENTINEL)
    expect(ls._dump().viddigest_byok_config, '配置也没落盘').toBeTruthy()
    // 明文必须从 DOM 上消失。
    //
    // ⚠️ 判据要查 input 的 **property** 而不是 `w.html()`：`html()` 序列化的是
    // attribute，而 `setValue` 写的是 `element.value` 这个 property，
    // Vue 的 v-model 之后也不再把它回写成 attribute（值没变，不触发重渲染）。
    // 所以 `w.html().not.toContain(SENTINEL)` **恒为真** —— 变异删掉
    // `apiKeyInput.value = ''` 之后它照样绿，实测 SURVIVED 过一条。
    // 真正会留在 DOM 上的是 input 当前的 value property。
    const keyInput = w.find('#byok-key')
    expect(keyInput.exists(), '保存后弹窗不该自己关掉，这里仍能看到输入框').toBe(true)
    expect(keyInput.element.value, 'key 明文还留在输入框里 —— 会进浏览器自动填充与「检查元素」')
      .toBe('')
  })

  test('留空 = 沿用已保存的那把，不是清空', async () => {
    const w = await mountDialog()
    await pickProvider(w, 'deepseek')
    await typeKey(w, SENTINEL)
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    // 再开一次，只改模型名，key 留空
    const w2 = await mountDialog()
    await pickProvider(w2, 'moonshot')
    await buttonByText(w2, '保存').trigger('click')
    await flushPromises()

    expect(ls._dump().viddigest_user_api_key, '只改厂商就把 key 静默清空了 —— 占位符承诺的是「沿用」')
      .toBe(SENTINEL)
    expect(ls._dump().viddigest_byok_config).toContain('moonshot')
  })

  test('平台模式下不显示 key 输入框（选了平台就不该有填 key 的地方）', async () => {
    const w = await mountDialog()
    await pickProvider(w, 'deepseek')
    expect(w.find('#byok-key').exists()).toBe(true)
    await buttonByText(w, '改用平台 Key').trigger('click')
    await flushPromises()
    const w2 = await mountDialog()
    expect(w2.find('#byok-key').exists(), '平台模式下还在显示 key 输入框').toBe(false)
  })

  test('「改用平台 Key」切走 mode，但**不清**盘上的 key', async () => {
    const w = await mountDialog()
    await pickProvider(w, 'deepseek')
    await typeKey(w, SENTINEL)
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    const w2 = await mountDialog()
    await buttonByText(w2, '改用平台 Key').trigger('click')
    await flushPromises()

    expect(JSON.parse(ls._dump().viddigest_byok_config).provider).toBe('platform')
    expect(ls._dump().viddigest_user_api_key, '切到平台模式把 key 删了 —— 切回来还得重填')
      .toBe(SENTINEL)
  })
})

// ── 拒绝保存的三种情形 ─────────────────────────────────────

describe('ByokDialog 挂载 · 什么时候不许存', () => {
  test('端点非法时既不保存也不关闭，并说清哪里不对', async () => {
    const w = await mountDialog()
    await pickProvider(w, 'deepseek')
    await typeKey(w, SENTINEL)
    await w.find('#byok-base').setValue('不是链接')
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(w.emitted('close'), '端点非法居然还关闭了弹窗').toBeFalsy()
    expect(ls._dump().viddigest_user_api_key, '端点非法居然还写了 key').toBeUndefined()
    expect(w.html()).toContain('端点地址格式不对')
  })

  test('选「平台 Key」却又填了 key时拦下，并说清冲突在哪', async () => {
    // 先在平台模式下直接填 key：key 输入框本来不该出现，
    // 所以这条走的是「厂商下拉被换成 platform 之前就填了」的路径。
    const w = await mountDialog()
    await pickProvider(w, 'deepseek')
    await typeKey(w, SENTINEL)
    // 绕过 UI 直接把 provider 设成 platform：模拟下拉切换与输入框之间的时间差
    await w.find('#byok-mode').setValue('platform')
    await flushPromises()
    await buttonByText(w, '保存').trigger('click')
    await flushPromises()

    expect(w.html(), '平台 + 填 key 的冲突没有提示').toContain('却又填了 key')
    expect(ls._dump().viddigest_user_api_key, '冲突状态下居然写了 key').toBeUndefined()
  })
})