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
    from: 'if (newUrl && props.hasCommunityResult) {',
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
    file: join(FE, 'src/lib/errors.js'),
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
    from: "[['parse', '解析'], ['chat', '追问']]",
    to: "[['parse', '解析']]",
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
    from: 'async function handleOpenRecord(detail) {',
    to: 'async function handleOpenRecordUnused(detail) {',
    expect: /没找到|没有查社区/,
  },
  {
    // 真实 bug：点击请求都发出去了，但社区页没让开，用户看到的是「没反应」。
    name: 'M13 点卡片不切回首页（请求发出但页面不动）',
    file: join(FE, 'src/App.vue'),
    from: "  currentPage.value = 'home'\r\n  handleParse(item.video_url)",
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
    from: '<p class="text-sm mb-1">暂无解析历史</p>',
    to: '<div class="grid grid-cols-3 gap-1 mb-5" aria-hidden="true"><span class="w-2.5 h-2.5 rounded-[2px] bg-violet/40 animate-pixel-blink"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-purple/40 animate-pixel-blink delay-1"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-pink/40 animate-pixel-blink delay-2"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-transparent"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-purple/40 animate-pixel-blink delay-3"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-cyan-500/40 animate-pixel-blink delay-4"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-transparent"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-transparent"></span><span class="w-2.5 h-2.5 rounded-[2px] bg-cyan-500/40 animate-pixel-blink delay-5"></span></div><p class="text-sm mb-1">暂无解析历史</p>',
    expect: /母题被复制了一份/,
  },
]

let killed = 0
const survivors = []
const broken = []

for (const m of MUTANTS) {
  if (!existsSync(m.file)) { broken.push(`${m.name} —— 文件不存在: ${m.file}`); continue }
  const backup = m.file + '.mutbak'
  let src
  try {
    src = readFileSync(m.file, 'utf8')
  } catch (e) {
    broken.push(`${m.name} —— 读取失败: ${e.message}`); continue
  }
  if (!src.includes(m.from)) {
    // 锚点没命中**必须**算失败。静默跳过会让「这条变异没跑过」
    // 看起来像「这条变异存活了」，或者干脆消失在报告里 ——
    // 而这两件事的处置完全相反。
    broken.push(`${m.name} —— 锚点没命中（代码已变或换行符不符？先更新变异脚本，别跳过）`)
    continue
  }

  copyFileSync(m.file, backup)
  try {
    writeFileSync(m.file, src.replace(m.from, m.to), 'utf8')
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
    } else {
      killed++
      const which = out.match(/✖ ([^\n(]+)/)
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
