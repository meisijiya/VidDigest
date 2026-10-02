#!/usr/bin/env node
/**
 * 变异验证：本分支的 nav-wall 用例是否真的在验。
 *
 * 全绿不等于在验 —— 只有「把功能破坏掉，测试转红」才证明它有鉴别力。
 * 每条变异改一处真实实现，跑一次 node --test，确认：
 *   1) 真的跑了测试（ran > 0，不把「0 条测试」当通过）
 *   2) 真的转红
 * 改完立刻还原，绝不留下变异体。
 *
 * 沿用 mutation-ui-fixes.mjs 的两条纪律：node --test 必须传 glob
 * （传单个文件路径不产出 TAP 计数，「跑不起来」和「全过」长得一样）；
 * 锚点没命中必须算装置故障，不能静默跳过。
 */
import { readFileSync, writeFileSync, existsSync, copyFileSync, unlinkSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const FE = join(HERE, '..')

const HEADER = join(FE, 'src/components/AppHeader.vue')
const APP = join(FE, 'src/App.vue')
const HERO = join(FE, 'src/components/HeroSection.vue')

/** 每个变异：破坏什么、期望哪条测试转红 */
const MUTANTS = [
  // ── 导航社区入口 ──────────────────────────────────────
  {
    name: 'N1 defineEmits 漏掉 open-community（点击看着正常，父组件收不到）',
    file: HEADER,
    pairs: [["'open-community', ", '']],
    expect: /defineEmits/,
  },
  {
    name: 'N2 App.vue 不监听 open-community（点了社区页面不动）',
    file: APP,
    pairs: [['@open-community="openCommunity"', '@open-history="openHistory"']],
    expect: /没监听 open-community/,
  },
  {
    name: 'N3 openCommunity 不切页',
    file: APP,
    pairs: [["  currentPage.value = 'community'\n  window.scrollTo({ top: 0 })\n}",
      "  window.scrollTo({ top: 0 })\n}"]],
    expect: /没有把 currentPage 切到 community/,
  },
  {
    name: 'N4 社区按钮不随当前页高亮',
    file: HEADER,
    pairs: [["page === 'community' ? 'text-blue-400 bg-blue-50 font-medium'",
      "page === 'never-matches' ? 'text-blue-400 bg-blue-50 font-medium'"]],
    expect: /社区页上「社区」不高亮/,
  },
  {
    name: 'N5 首页那个孤立的「浏览社区 →」按钮被加回来（两个入口）',
    file: APP,
    pairs: [['<FeatureSection />',
      '<button @click="currentPage = \'community\'">浏览社区 →</button><FeatureSection />']],
    expect: /首页营销区还留着/,
  },
  {
    name: 'N6 社区按钮不发事件（整个入口是死的）',
    file: HEADER,
    pairs: [[`@click="$emit('open-community')"`, '@click="noop"']],
    expect: /没有 emit open-community/,
  },

  // ── 城墙：海浪 ────────────────────────────────────────
  {
    name: 'N7 wall-wave 上下位移改成左右（城墙整体被推着走，不是浪）',
    file: HERO,
    pairs: [['50%      { transform: translateY(-6px); }', '50%      { transform: translateX(-6px); }']],
    expect: /混进了 translateX/,
  },
  {
    name: 'N8 错峰延迟全部相同（各自乱浮，连不成波）',
    file: HERO,
    pairs: [['delay: 0.32, line:', 'delay: 0.00, line:']],
    expect: /delay 没有比前一段大/,
  },
  {
    name: 'N9 错峰跨度超过动画周期（浪会回卷）',
    file: HERO,
    pairs: [['delay: 1.76, line:', 'delay: 2.60, line:']],
    expect: /浪往回走/,
  },
  {
    name: 'N10 顶线被移出 <g>（波浪一走位线就脱节）',
    file: HERO,
    pairs: [[`          <template v-if="s.line">
            <rect :x="s.x" :y="s.y" width="120" height="3" :fill="s.line" opacity="0.55"/>
            <rect :x="s.x" :y="s.y" width="120" height="3" :fill="\`url(#wall-shine-\${s.x})\`"/>
          </template>
        </g>`,
      `        </g>
        <g v-if="s.line">
          <rect :x="s.x" :y="s.y" width="120" height="3" :fill="s.line" opacity="0.55"/>
          <rect :x="s.x" :y="s.y" width="120" height="3" :fill="\`url(#wall-shine-\${s.x})\`"/>
        </g>`]],
    expect: /彩色顶线不在块的 <g> 内/,
  },
  {
    name: 'N11 wall-step 挂丢（凸起不动了）',
    file: HERO,
    pairs: [['<g v-for="s in wallSteps" :key="s.x"\n           class="wall-step"',
      '<g v-for="s in wallSteps" :key="s.x"\n           class="wall-static"']],
    expect: /wall-step 不在 v-for/,
  },

  // ── 城墙：扫光 ────────────────────────────────────────
  {
    name: 'N12 wall-shine 横向位移改成上下（不是从左到右）',
    file: HERO,
    pairs: [['from { transform: translateX(0); }', 'from { transform: translateY(0); }'],
      ['to   { transform: translateX(120px); }', 'to   { transform: translateY(120px); }']],
    expect: /扫光方向不对|没有 translateX/,
  },
  {
    name: 'N13 扫光位移 120 -> 60（扫不到线头）',
    file: HERO,
    pairs: [['to   { transform: translateX(120px); }', 'to   { transform: translateX(60px); }']],
    expect: /扫不到头/,
  },
  {
    name: 'N14 渐变 id 与 rect 引用对不上（扫光层静默渲染成空）',
    file: HERO,
    pairs: [[':id="`wall-shine-${s.x}`"', ':id="`wall-shine-g-${s.x}`"']],
    expect: /对不上时扫光层渲染成空/,
  },
  {
    name: 'N15 三条顶线扫光延迟相同（同时亮，看不出往右走）',
    file: HERO,
    pairs: [["line: '#EC4899', shineDelay: 0.8", "line: '#EC4899', shineDelay: 0.0"]],
    expect: /扫光延迟相同/,
  },
  {
    name: 'N16 波浪与扫光不同周期（看着像卡带）',
    file: HERO,
    pairs: [['animation: wall-shine 2.4s linear infinite', 'animation: wall-shine 3.1s linear infinite']],
    expect: /看着像卡带/,
  },
  {
    name: 'N17 带顶线的两段 x 相同（渐变 id 撞车）',
    file: HERO,
    pairs: [["{ x: 720, y: 36,", '{ x: 240, y: 36,']],
    expect: /渐变 id 会撞车|带顶线的段数/,
  },
]

/**
 * 按字节保真地应用若干对替换。
 *
 * 先归一化行尾符再替换、替换完还原成原文件那一种：App.vue / HeroSection.vue
 * 在 Windows 上是 CRLF，模式串若直接按 \n 匹配会一个字符都命中不了 ——
 * 而「锚点没命中」和「测试守不住」在报告里长得一模一样。
 * （早期版本沿用 mutation-ui-fixes.mjs 的做法、把 \r\n 直接写进锚点，
 *  那等于把「这份文件是 CRLF」这个事实抄了 17 遍，改一次就有一处失配。）
 * 归一化只在内存里做，写回时还原，磁盘上的文件不会被动到。
 */
function applyPairs(src, pairs) {
  const crlf = src.includes('\r\n')
  let text = crlf ? src.replace(/\r\n/g, '\n') : src
  for (const [from, to] of pairs) {
    if (!text.includes(from)) return null
    text = text.split(from).join(to)
  }
  if (crlf) text = text.replace(/\n/g, '\r\n')
  return text
}

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

  const mutated = applyPairs(src, m.pairs)
  if (mutated === null || mutated === src) {
    // 锚点没命中**必须**算失败：静默跳过会让「这条变异没跑过」看起来像
    // 「这条变异存活了」，而这两件事的处置完全相反。
    broken.push(`${m.name} —— 锚点没命中（代码已变？先更新变异脚本，别跳过）`)
    continue
  }

  copyFileSync(m.file, backup)
  try {
    writeFileSync(m.file, mutated, 'utf8')
    let out = ''
    let ok = false
    try {
      out = execFileSync(
        process.execPath,
        // 必须用与 `npm test` 相同的 glob：传单个文件路径时 node --test
        // 不产出 TAP 计数，「跑不起来」与「全绿」长得一模一样。
        ['--test', '--test-timeout=10000', 'tests/**/*.test.mjs'],
        { cwd: FE, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
      )
      ok = true
    } catch (e) {
      out = (e.stdout || '') + (e.stderr || '')
      ok = false
    }
    // 判据必须看**实际跑了多少条**：变异可能把源码改坏到测试导入失败，
    // 那不是「测试通过」。
    const mRan = out.match(/#\s*tests\s+(\d+)/) || out.match(/tests (\d+)/)
    const nRan = mRan ? Number(mRan[1]) : 0
    if (nRan === 0) {
      survivors.push(`${m.name} —— NO-TESTS-RAN（ran=0，判据无效；变异可能把源码改坏到无法导入）`)
      continue
    }
    if (ok) {
      survivors.push(`${m.name} —— 存活（${nRan} 条测试全绿，这组用例没在验它）`)
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
