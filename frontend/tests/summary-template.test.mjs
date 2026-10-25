/**
 * 总结模板选择（工单 #105）的接口层与接线判据。运行：node --test tests/
 *
 * ## 这个文件守什么
 *
 * - 「选中的模板进了请求体」是**接口契约**：真调 `summarizeVideo`，看它发出的
 *   fetch body。真调用抓得住「键名拼错」「值被丢掉」这类改写，纯文本断言抓不住。
 * - 「模板清单从服务端读」同样是契约：真调 `fetchSummaryTemplates`，看它打的是
 *   哪个地址、拿回什么。前端硬编码一份名单的话，这一条立刻变红。
 * - 「组件把下拉框的选择接进了请求」判据的对象是**接线本身**：挂载层
 *   （`tests/summary-template.mount.spec.mjs`）验「用户在页面上选完真的生效」，
 *   这里守「组件里没有第二条把选择扔掉的路径」。
 * - Markdown 排版是**样式**判据：CSS 不参与 jsdom 的样式计算，唯一的出口就是
 *   样式表文本，所以这里读 `style.css`，并把作用域限定在 `.summary-content` 上
 *   ——`.prose` 里已有的 typography 样式不算数（总结正文那层用的是自己的类）。
 */
import { test, describe, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

import { summarizeVideo, fetchSummaryTemplates } from '../src/api/summarize.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
const FE = join(HERE, '..')
const read = (rel) => readFileSync(join(FE, rel), 'utf8')

/** 收集所有「选择器里带 .summary-content」的 CSS 规则正文。 */
function collectSummaryRules(css) {
  const out = []
  // 规则形如 `选择器 { … }`；这里不处理 @media 嵌套（本仓 style.css 里没有），
  // 只按最外层大括号配对，够用且不会误吞。
  const re = /([^{}]+)\{([^{}]*)\}/g
  let m
  while ((m = re.exec(css)) !== null) {
    const selector = m[1].trim()
    if (selector.includes('.summary-content')) out.push(`${selector} { ${m[2]} }`)
  }
  return out.join('\n')
}

let seen

beforeEach(() => {
  seen = []
  globalThis.localStorage = {
    _v: { auth_token: 'test-token' },
    getItem(k) {
      return this._v[k] ?? null
    },
    setItem(k, v) {
      this._v[k] = String(v)
    },
    removeItem(k) {
      delete this._v[k]
    },
  }
})

/** 假 fetch：记下请求，返回一个空流（读完即结束，不会挂住）。 */
function captureFetch(response) {
  globalThis.fetch = async (url, init) => {
    seen.push({ url, init })
    return response
  }
}

function emptyStreamResponse() {
  return new Response(
    new ReadableStream({
      start(c) {
        c.close()
      },
    }),
  )
}

const noopCallbacks = {
  onOwnership: () => {},
  onSubtitle: () => {},
  onSummary: () => {},
  onMindmap: () => {},
  onTags: () => {},
  onQuota: () => {},
  onError: () => {},
  onCancel: () => {},
  onDone: () => {},
}

describe('summarizeVideo 的模板字段', () => {
  test('选中的模板进了请求体', async () => {
    captureFetch(emptyStreamResponse())

    await summarizeVideo('u', 'zh', noopCallbacks, { summaryTemplate: 'brief' }).done

    assert.equal(seen.length, 1, 'summarizeVideo 必须真的发一次请求')
    const body = JSON.parse(seen[0].init.body)
    assert.equal(body.summary_template, 'brief', '选中的模板没进请求体，用户选了个寂寞')
  })

  test('没选模板时字段是空串（服务端用它自己的默认模板）', async () => {
    captureFetch(emptyStreamResponse())

    await summarizeVideo('u', 'zh', noopCallbacks).done

    const body = JSON.parse(seen[0].init.body)
    // 不传 = 走服务端默认。这里不能塞一个前端猜的默认值：
    // 猜错一次的症状是「用户看到的是 A 模板的总结，界面上却写着 B」。
    assert.equal(body.summary_template, '', '不选模板时应当发空串，而不是前端替服务端做决定')
  })

  test('对照组：加模板没有弄丢原有的请求字段', async () => {
    captureFetch(emptyStreamResponse())

    await summarizeVideo('u', 'zh', noopCallbacks, { summaryTemplate: 'analysis' }).done

    const body = JSON.parse(seen[0].init.body)
    assert.equal(body.url, 'u')
    assert.equal(body.language, 'zh')
    assert.equal(body.overwrite, false)
  })
})

describe('fetchSummaryTemplates', () => {
  test('清单从服务端读，前端不硬编码名单', async () => {
    const payload = {
      default: 'full',
      templates: [{ id: 'full', name: '详细总结', description: '完整分析。' }],
    }
    captureFetch(new Response(JSON.stringify(payload), { status: 200 }))

    const list = await fetchSummaryTemplates()

    assert.equal(seen[0].url, '/api/summary-templates', '模板清单必须来自服务端端点')
    assert.equal(seen[0].init.method, 'GET', '清单是只读的，不该用 POST')
    assert.deepEqual(list, payload, '服务端给了什么就原样拿什么')
  })

  test('取不到就抛错（组件据此降级，不假装拿到了）', async () => {
    captureFetch(new Response('boom', { status: 500 }))

    await assert.rejects(() => fetchSummaryTemplates(), /获取总结模板失败/)
  })
})

describe('组件接线（VideoSummary.vue）', () => {
  const vue = read('src/components/VideoSummary.vue')

  test('模板下拉框存在，且选项来自取回的清单', () => {
    assert.ok(vue.includes('v-model="selectedTemplate"'), '模板下拉框没接到选中状态上')
    assert.ok(vue.includes('v-for="tpl in templateOptions"'), '下拉框的选项不是从取回的清单里渲染的')
  })

  test('选中的模板进了 summarizeVideo 的选项', () => {
    assert.ok(
      vue.includes('summaryTemplate: selectedTemplate.value,'),
      '组件没有把用户选的模板传给请求——选了也不会生效',
    )
  })

  test('反向对照：组件里没有硬编码模板名单', () => {
    // 名单的真值在服务端（prompts/templates.json）。前端再抄一份，
    // 症状是「服务端加了模板，界面上下拉框里没有」——而那要等到用户投诉才发现。
    for (const id of ['brief', 'tutorial', 'analysis']) {
      assert.ok(!vue.includes(`'${id}'`) && !vue.includes(`"${id}"`), `组件里硬编码了模板 id「${id}」`)
    }
  })
})

describe('Markdown 排版（style.css）', () => {
  const css = read('src/style.css')
  const summaryCss = collectSummaryRules(css)

  test('表格、引用、代码块、分隔线都在 .summary-content 作用域内', () => {
    assert.ok(/\.summary-content\s+table\b/.test(summaryCss), '总结正文里的表格没有排版')
    assert.ok(/\.summary-content\s+thead\b/.test(summaryCss), '表头没有排版')
    assert.ok(/\.summary-content\s+th\b/.test(summaryCss), '表头单元格没有排版')
    assert.ok(/\.summary-content\s+td\b/.test(summaryCss), '表格单元格没有排版')
    assert.ok(/\.summary-content\s+blockquote\b/.test(summaryCss), '引用块没有排版')
    assert.ok(/\.summary-content\s+pre\b/.test(summaryCss), '代码块没有排版')
    assert.ok(/\.summary-content\s+hr\b/.test(summaryCss), '分隔线没有排版')
  })

  test('反向对照：同样的规则挂在别的作用域上，判据不会误判通过', () => {
    // 这条证明上面那条判据真的在看作用域，而不是在全文里搜「table」三个字母。
    const fake = collectSummaryRules('.prose table { color: red; }\n.blockquote { color: red; }')
    assert.ok(!/\.summary-content\s+table\b/.test(fake), '判据越权：把别的作用域的规则算进来了')
  })
})
