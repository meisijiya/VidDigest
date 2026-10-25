#!/usr/bin/env node
/**
 * 变异验证：新测试是否真的在验。
 *
 * 全绿不等于在验 —— 只有「把功能破坏掉，测试转红」才证明它有鉴别力。
 * 每条变异改一处真实实现，跑一次 node --test，确认：
 *   1) 真的跑了测试（ran > 0，不把「0 条测试」当通过）
 *   2) 真的转红
 * 改完立刻还原，绝不留下变异体。
 */
import { readFileSync, writeFileSync, existsSync, copyFileSync, unlinkSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const FE = join(HERE, '..')
const BE = join(FE, '..', 'backend')

/** 每个变异：破坏什么、期望哪条测试转红 */
// 空白宽松匹配：模式串的每个字符之间都允许插入任意空白。
// 非空白字符仍要逐字相同——所以它不放宽判据，只是不怕 prettier 重排。
function looseFind(text, pattern) {
  // 在 text 里找 pattern，忽略空白差异，返回 [start, end] 或 null。
  //
  // 非空白字符仍要逐字相同、顺序不变，只允许它们之间多出空白（换行、缩进、
  // 逗号后加空格……）。这些是格式化工具造成的样子，不是代码变了。
  //
  // 刻意不用正则：把 \s* 挂在每个字符后面会让回溯指数爆炸，源文件一大
  // 就能把进程卡死。这里是双指针扫描，复杂度 O(n*m)，没有回溯。
  const chars = Array.from(pattern).filter((ch) => !/\s/.test(ch))
  if (chars.length === 0) return null
  const n = text.length
  for (let start = 0; start < n; start += 1) {
    if (text[start] !== chars[0]) continue
    let ti = start
    let ok = true
    for (const c of chars) {
      while (ti < n && /\s/.test(text[ti])) ti += 1
      if (ti >= n || text[ti] !== c) {
        ok = false
        break
      }
      ti += 1
    }
    if (ok) return [start, ti]
  }
  return null
}

function replaceLoose(src, from, to) {
  if (src.includes(from)) return { out: src.replace(from, to), matched: true }
  const span = looseFind(src, from)
  if (!span) return { out: src, matched: false }
  return { out: src.slice(0, span[0]) + to + src.slice(span[1]), matched: true }
}

const MUTANTS = [
  {
    name: 'M1 卡片退回 div+@click（点空白处没反应）',
    file: join(FE, 'src/components/CommunityPage.vue'),
    from: '<button type="button" @click="openDetail(item)"',
    to: '<div @click="openDetail(item)"',
    expect: /卡片不是 button|整块可点/,
  },
  {
    name: 'M2 卡片恢复两列（大屏留白）',
    file: join(FE, 'src/components/CommunityPage.vue'),
    from: '<ul v-else class="grid gap-4">',
    to: '<ul v-else class="grid gap-4 sm:grid-cols-2">',
    expect: /仍是两列/,
  },
  {
    name: 'M3 goHome 不清 videoData（点了像没反应）',
    file: join(FE, 'src/App.vue'),
    // ⚠️ 锚点必须匹配目标文件的**实际换行符**：App.vue 在 Windows 上是
    // CRLF，锚点写成 \n 会一个字符都匹配不上，装置静默报「锚点没命中」。
    from: "  videoData.value = null\r\n  currentUrl.value = ''",
    to: "  currentUrl.value = ''",
    expect: /解析结果区不会消失|goHome 没清掉/,
  },
  {
    name: 'M4 hasCommunityResult 默认 true（会白扣额度）',
    file: join(FE, 'src/components/VideoSummary.vue'),
    from: 'hasCommunityResult: { type: Boolean, default: false }',
    to: 'hasCommunityResult: { type: Boolean, default: true }',
    expect: /默认必须是 false/,
  },
  {
    name: 'M5 自动拉取条件去掉开关（无脑自动 = 会扣额度）',
    file: join(FE, 'src/components/VideoSummary.vue'),
    from: 'if (newUrl && (props.hasCommunityResult || props.regenerateRequested)) {',
    to: 'if (newUrl) {',
    expect: /没有自动发起|为真时自动发起/,
  },
  {
    name: 'M6 后端去掉 json_valid 守卫（一条坏记录打挂整个列表）',
    file: join(BE, 'database.py'),
    from: 'CASE WHEN json_valid(video_data)',
    to: 'CASE WHEN 1 = 1',
    expect: /缺 json_valid 守卫/,
  },
  {
    name: 'M7 错误分类器恒返回第一个分支（分类形同虚设）',
    file: join(FE, 'src/lib/errors.ts'),
    from: '  if (looksLikeThrottled(err)) {',
    to: '  if (false) {',
    expect: /限流时没说清/,
  },
  {
    name: 'M8 弹窗去掉 hint（只说失败不说怎么办）',
    file: join(FE, 'src/components/ErrorModal.vue'),
    from: '<div v-if="hint"',
    to: '<div v-if="false"',
    expect: /下一步该做什么/,
  },
  {
    name: 'M9 历史列表封面改回固定图标',
    file: join(FE, 'src/components/HistoryPage.vue'),
    from: '<img v-if="item.cover_url" :src="proxyThumbnail(item.cover_url)"',
    to: '<img v-if="false" :src="proxyThumbnail(item.cover_url)"',
    expect: /列表没渲染封面/,
  },
  {
    name: 'M10 额度面板只列一个计数器（拆分对用户不可见）',
    file: join(FE, 'src/components/AppHeader.vue'),
    from: "['chat', '追问'],",
    to: '',
    expect: /没有同时列出解析与追问两个额度/,
  },
  {
    name: 'M11 复用分支后移到扣额度之后（自动展示会扣额度）',
    file: join(BE, 'api_summarize.py'),
    from: '    if claim == "reuse":',
    to: '    if claim == "nonexistent-reuse-check":',
    expect: /consume_quota 出现在 reuse 分支之前/,
  },
  {
    name: 'M12 handleOpenRecord 不再查社区（历史页自动展示永不触发）',
    file: join(FE, 'src/App.vue'),
    from: 'async function handleOpenRecord(detail: HistoryDetail) {',
    to: 'async function handleOpenRecordUnused(detail: HistoryDetail) {',
    expect: /没找到|没有查社区/,
  },
  {
    // 真实 bug：点击请求都发出去了，但社区页没让开，用户看到的是「没反应」。
    name: 'M13 点卡片不切回首页（请求发出但页面不动）',
    file: join(FE, 'src/App.vue'),
    from: "  router.push('/')\n  handleParse(item.video_url)",
    to: '  handleParse(item.video_url)',
    expect: /没切回首页/,
  },
  {
    name: 'M14 抽掉一个色块的闪动（页头 Logo 少亮一块，肉眼可见的缺口）',
    file: join(FE, 'src/components/PixelLogo.vue'),
    from: 'fill="#EC4899" class="animate-pixel-blink delay-2"',
    to: 'fill="#EC4899"',
    expect: /闪动的色块数/,
  },
  {
    name: 'M15 错峰塌成两簇（delay-3 改成 delay-1，同时亮 = 没有母题）',
    file: join(FE, 'src/components/PixelLogo.vue'),
    from: 'class="animate-pixel-blink delay-3"',
    to: 'class="animate-pixel-blink delay-1"',
    expect: /错峰序列/,
  },
  {
    name: 'M16 删掉 .delay-4 的 CSS 定义（class 挂着但静默不生效）',
    file: join(FE, 'src/style.css'),
    from: '.delay-4 { animation-delay: 0.4s; }',
    to: '',
    expect: /delay-4 定义丢了/,
  },
  {
    name: 'M17 把像素网格搬回历史页空状态（母题被复制，三处一起闪）',
    file: join(FE, 'src/components/HistoryPage.vue'),
    from: "<p class=\"text-sm mb-1\">{{ hasFilters ? '没有匹配的记录' : '暂无解析历史' }}</p>",
    to: '<div class="grid grid-cols-3 gap-1 mb-5" aria-hidden="true"><span class="w-2.5 h-2.5 rounded-[2px] bg-violet/40 animate-pixel-blink"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-purple/40 animate-pixel-blink delay-1"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-pink/40 animate-pixel-blink delay-2"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-transparent"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-purple/40 animate-pixel-blink delay-3"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-cyan-500/40 animate-pixel-blink delay-4"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-transparent"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-transparent"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-cyan-500/40 animate-pixel-blink delay-5"></span></div><p class="text-sm mb-1">{{ hasFilters ? \'没有匹配的记录\' : \'暂无解析历史\' }}</p>',
    expect: /母题被复制了一份/,
  },
  {
    name: 'M18 并发分类器忽略 detail.code（退回只认中文文案）',
    file: join(FE, 'src/lib/errors.ts'),
    from: "  if (errorCodeOf(err) === 'concurrency_limit') return true",
    to: "  if (errorCodeOf(err) === '__mutant_never__') return true",
    expect: /文案换成完全无关的话/,
  },
  {
    name: 'M19 限流分类器忽略 detail.code（自家限流被说成平台风控）',
    file: join(FE, 'src/lib/errors.ts'),
    from: "  if (errorCodeOf(err) === 'rate_limit') return true",
    to: "  if (errorCodeOf(err) === '__mutant_never__') return true",
    expect: /不靠「服务器限流」四个字/,
  },
  {
    // 工单 #95：历史页没接登录入口。锚点带 `back: goHome,`，
    // 因为社区页与首页也各有一处一模一样的 'need-login' 接线 ——
    // 锚点太短会打在别人身上，变异就打偏了。
    name: 'M20 历史页不接 need-login（点了「去登录」登录框不开）',
    file: join(FE, 'src/App.vue'),
    from: "        back: goHome,\r\n        'need-login': () => showAuthModal('login'),",
    to: "        back: goHome,\r\n        'need-login-unused': () => showAuthModal('login'),",
    expect: /历史页没接登录入口/,
  },
  {
    name: 'M21 历史页模板里没有未登录这一支',
    file: join(FE, 'src/components/HistoryPage.vue'),
    from: 'v-else-if="needLogin"',
    to: 'v-else-if="neverNeedLogin"',
    expect: /模板里没有未登录这一支/,
  },
  {
    // 锚点只取**第一个** isUnauthorized —— load() 在 loadFacets() 前面，
    // 而 node --test 那条断言判的就是 load() 的函数体。
    name: 'M22 load() 不再把 401 翻译成 needLogin（错误码又甩回用户脸上）',
    file: join(FE, 'src/components/HistoryPage.vue'),
    from: '    if (isUnauthorized(e)) {',
    to: '    if (false) {',
    expect: /401 没被翻译成 needLogin/,
  },
  // ── 工单 #99：邀请码注册的前端接线 ──
  // 这几条破坏的是「注册要邀请码」「管理员要发码」这两处接线。
  // 断了都不报错：输入框没了只是少一格，邀请码没进请求体只是后端说无效。
  {
    name: 'M23 注册表单的邀请码输入框没了（少一格，后端只说「需要邀请码」）',
    file: join(FE, 'src/components/AuthModal.vue'),
    from: 'v-model="inviteCode"',
    to: 'v-model="email"',
    expect: /注册模式有一格邀请码输入/,
  },
  {
    name: 'M24 邀请码不进请求体（码填了也白填）',
    file: join(FE, 'src/components/AuthModal.vue'),
    from: 'invite_code: code',
    to: "invite_code: ''",
    expect: /请求体里多出 invite_code 这一键/,
  },
  {
    name: 'M25 注册走的还是不带邀请码那条路（后端永远说「需要邀请码」）',
    file: join(FE, 'src/components/AuthModal.vue'),
    from: 'registerWithInviteCode(email.value, password.value, inviteCode.value.trim())',
    to: 'register(email.value, password.value)',
    expect: /注册走的是带邀请码的那条路/,
  },
  {
    name: 'M26 失败提示不取后端 detail（四类失效原因被一句通用话盖住）',
    file: join(FE, 'src/components/AuthModal.vue'),
    from: 'e.response?.data?.detail',
    to: "'注册失败'",
    expect: /读的是后端给的 detail/,
  },
  {
    name: 'M27 管理后台发码请求打错地址（管理员点了没反应）',
    file: join(FE, 'src/api/admin.ts'),
    from: "post('/api/admin/invite-codes', {",
    to: "post('/api/admin/invite-codes-mutant', {",
    expect: /有发码入口/,
  },
  {
    name: 'M28 管理后台列表请求打错地址（列表永远是空的）',
    file: join(FE, 'src/api/admin.ts'),
    from: "get('/api/admin/invite-codes', {",
    to: "get('/api/admin/invite-codes-mutant', {",
    expect: /有列表入口/,
  },
  {
    name: 'M29 管理后台作废请求打错地址（点了作废，码还能用）',
    file: join(FE, 'src/api/admin.ts'),
    from: 'post(`/api/admin/invite-codes/${inviteId}/revoke`)',
    to: 'post(`/api/admin/invite-codes/${inviteId}/revoke-mutant`)',
    expect: /有作废入口/,
  },
  {
    name: 'M30 列表改成显示完整码（明文只给一次这条规矩破了）',
    file: join(FE, 'src/components/InviteCodesPage.vue'),
    from: '{{ row.codeMasked }}',
    to: '{{ row.code }}',
    expect: /列表只显示脱敏码/,
  },
  {
    name: 'M31 打开页面时不加载列表（页面是一片空白）',
    file: join(FE, 'src/components/InviteCodesPage.vue'),
    // 锚点带上 onMounted 的开头：`void loadCodes()` 在页面里出现三次
    // （翻页、换筛选、挂载），只锚这一句的话，装置改的是翻页那处，
    // 挂载那处照样在跑 —— 变异会「存活」，而它压根没碰到被测机制。
    from: 'onMounted(() => {\n  void loadCodes()',
    to: 'onMounted(() => {\n  void Promise.resolve()',
    expect: /挂载即加载列表/,
  },
  // ── 工单 #105：总结模板选择 ──
  // 这几条破坏的是「用户在下拉框里选的模板真的被用上」。
  // 断了都不报错：请求照样发出去、后端照样返回总结，
  // 症状是「选了精简摘要，拿回来的还是详尽总结」——用户只能靠字数去猜。
  {
    name: 'M32 选中的模板不进请求体（下拉框选了个寂寞）',
    file: join(FE, 'src/api/summarize.ts'),
    from: "summary_template: options.summaryTemplate || '',",
    to: "summary_template: '',",
    expect: /选中的模板没进请求体/,
  },
  {
    name: 'M33 组件不把下拉框的选择传出去（选了也不生效）',
    file: join(FE, 'src/components/VideoSummary.vue'),
    from: 'summaryTemplate: selectedTemplate.value,',
    to: "summaryTemplate: '',",
    expect: /组件没有把用户选的模板传给请求/,
  },
  {
    name: 'M34 总结正文里的表格没有排版（模型一输出表格就散架）',
    file: join(FE, 'src/style.css'),
    from: `.summary-content table {
  display: block;
  width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
  margin: 0.75rem 0;
  font-size: 0.9rem;
}`,
    to: '',
    expect: /总结正文里的表格没有排版/,
  },
]

let killed = 0
const survivors = []
const broken = []

for (const m of MUTANTS) {
  if (!existsSync(m.file)) {
    broken.push(`${m.name} —— 文件不存在: ${m.file}`)
    continue
  }
  const backup = m.file + '.mutbak'
  let src
  try {
    src = readFileSync(m.file, 'utf8')
  } catch (e) {
    broken.push(`${m.name} —— 读取失败: ${e.message}`)
    continue
  }
  // 锚点没命中**必须**算失败。静默跳过会让「这条变异没跑过」
  // 看起来像「这条变异存活了」，或者干脆消失在报告里 ——
  // 而这两件事的处置完全相反。
  // 但「重排版」（prettier 改了换行与缩进）不算代码变了：
  // 先精确匹配，再退到空白宽松（只改空白，非空白字符仍逐字相同）。
  const mutation = replaceLoose(src, m.from, m.to)
  if (!mutation.matched) {
    broken.push(`${m.name} —— 锚点没命中（代码已变或换行符不符？先更新变异脚本，别跳过）`)
    continue
  }

  copyFileSync(m.file, backup)
  try {
    writeFileSync(m.file, mutation.out, 'utf8')
    let out = ''
    let ran = -1
    try {
      out = execFileSync(
        process.execPath,
        // 必须用与 `npm test` 相同的 glob：传单个文件路径时 node --test
        // 不产出 TAP 计数，于是「变异后测试跑不起来」和「测试全过」长得
        // 一模一样，12 条变异会假性全部存活。
        ['--test', '--test-timeout=10000', 'tests/**/*.test.mjs'],
        { cwd: FE, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
      )
      ran = 0
    } catch (e) {
      out = (e.stdout || '') + (e.stderr || '')
      ran = 1
    }
    // 判据必须看**实际跑了多少条**：
    // 变异可能把源码改坏到测试导入失败，那不是「测试通过」。
    const mRan = out.match(/#\s*tests\s+(\d+)/) || out.match(/tests (\d+)/)
    const nRan = mRan ? Number(mRan[1]) : 0
    if (nRan === 0) {
      survivors.push(`${m.name} —— NO-TESTS-RAN（ran=0，判据无效；变异可能把源码改坏到无法导入）`)
      continue
    }
    if (ran === 0) {
      survivors.push(`${m.name} —— 存活（${nRan} 条测试全绿，测试没在验这一条）`)
    } else if (!out.includes('AssertionError')) {
      // 转红但不是断言失败：多半是变异把源码改坏到**导入不了**（语法错误之类）。
      // 那种红不证明任何东西——它证明的是「这个文件坏了」，不是「这条守卫在验这条约束」。
      // 所以它既不算杀、也不算存活：算变异体自坏。
      broken.push(`${m.name} —— 变异体自坏（转红但不是 AssertionError：源码可能被改坏到无法导入）`)
    } else {
      killed++
      const which = out.match(/✖ ([^\n(]+)/) || out.match(/^not ok \d+ - (.+)$/m)
      console.log(`KILLED  ${m.name}  (ran=${nRan})`)
      console.log(`        被杀于: ${which ? which[1].trim() : '(见输出)'}`)
    }
  } finally {
    copyFileSync(backup, m.file)
    unlinkSync(backup)
  }
}

console.log('='.repeat(72))
console.log(`变异结果：${killed}/${MUTANTS.length} 杀，${survivors.length} 存活，${broken.length} 装置故障`)
if (survivors.length) {
  console.log('--- 存活（测试没在验，或装置坏了）---')
  for (const s of survivors) console.log('  ' + s)
}
if (broken.length) {
  console.log('--- 装置故障 ---')
  for (const b of broken) console.log('  ' + b)
}
process.exit(survivors.length || broken.length ? 1 : 0)
