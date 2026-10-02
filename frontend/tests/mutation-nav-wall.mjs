#!/usr/bin/env node
/**
 * 变异验证：本分支的 nav-wall 用例是否真的在验。
 *
 * 全绿不等于在验 —— 只有「把功能破坏掉，测试转红」才证明它有鉴别力。
 * 每条变异改一处真实实现，跑一次 node --test，确认：
 *   1) 真的跑了测试（ran > 0，不把「0 条测试」当通过）
 *   2) 真的转红
 * 改完立刻按字节还原，绝不留下变异体。
 *
 * 三条纪律（前两轮各自踩过一次）：
 *   · node --test 必须传 glob —— 传单个文件路径不产出 TAP 计数，
 *     「跑不起来」和「全过」长得一模一样。
 *   · 锚点没命中必须算装置故障 —— 静默跳过会让「这条变异没跑过」
 *     看起来像「这条变异存活了」，而这两件事的处置完全相反。
 *   · 锚点必须匹配目标文件实际的行尾符 —— HeroSection.vue / AppHeader.vue
 *     在 Windows 上是 CRLF。下面用 applyPairs 归一化行尾、替换完再还原回去，
 *     磁盘上的文件不会被动到。
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

/** 多行锚点拼起来：写成数组比在字符串里手写 \n 更好读，
 *  也顺带躲开了模板字符串对 ${} 的插值（源码里有 `url(#wall-shine-${s.id})`）。 */
const L = (...lines) => lines.join('\n')

/** 每个变异：破坏什么、期望哪条断言转红 */
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

  // ── 城墙：底边固定 + 上下浮 ───────────────────────────
  {
    name: 'W1 wall-bob 的上下位移改成左右（凸起斜着晃，不成浪）',
    file: HERO,
    pairs: [['50%      { transform: translateY(-5px); }',
      '50%      { transform: translateX(-5px); }']],
    expect: /混进了 translateX/,
  },
  {
    name: 'W2 浮动延迟反向（浪往左推，不是往右）',
    file: HERO,
    pairs: [['delay: i * WALL_STEP_DELAY', 'delay: (11 - i) * WALL_STEP_DELAY']],
    expect: /浪会往\*\*左\*\*走/,
  },
  {
    name: 'W3 错峰步进调大（相位跨度超过浮动周期，浪往回卷）',
    file: HERO,
    pairs: [['const WALL_STEP_DELAY = 0.2', 'const WALL_STEP_DELAY = 0.5']],
    expect: /空窗|浪往回走/,
  },
  {
    name: 'W4 <g> 上的 animationDelay 绑定删掉（数据里的 delay 全白算）',
    file: HERO,
    pairs: [[`:style="{ animationDelay: s.delay + 's' }"`, ':style="{}"']],
    expect: /animationDelay 绑定/,
  },
  {
    name: 'W5 wall-step class 挂丢（凸起不动了）',
    file: HERO,
    pairs: [['class="wall-step"', 'class="wall-static"']],
    expect: /wall-step 不在 v-for|出现次数异常/,
  },
  {
    name: 'W6 凸起高度全改成一样（城墙是平的，没有起伏）',
    file: HERO,
    pairs: [['y: 54', 'y: 40'], ['y: 56', 'y: 40'], ['y: 52', 'y: 40'],
      ['y: 34', 'y: 40'], ['y: 32', 'y: 40'], ['y: 26', 'y: 40'],
      ['y: 30', 'y: 40'], ['y: 28', 'y: 40']],
    expect: /城墙是平的|并不比最深的凹/,
  },
  {
    name: 'W7 某一段的 x 错开一格（段距不等宽，底部露参差的口子）',
    file: HERO,
    pairs: [['{ x: 240,  y: 32,', '{ x: 260,  y: 32,']],
    expect: /段距不等宽/,
  },
  {
    name: 'W8 顶线被移出块的 <g>（一浮动顶线就脱节）',
    file: HERO,
    pairs: [[L(
      '            <template v-if="s.line">',
      '              <rect :x="s.x" :y="s.y" width="120" height="3" :fill="s.line" opacity="0.55"/>',
      '              <rect :x="s.x" :y="s.y" width="120" height="3" :fill="`url(#wall-shine-${s.id})`"/>',
      '            </template>',
      '          </g>',
    ), L(
      '          </g>',
      '          <template v-if="s.line">',
      '            <rect :x="s.x" :y="s.y" width="120" height="3" :fill="s.line" opacity="0.55"/>',
      '            <rect :x="s.x" :y="s.y" width="120" height="3" :fill="`url(#wall-shine-${s.id})`"/>',
      '          </template>',
    )]],
    expect: /只有 1 个 rect|没有挂在「只有凸起才画」/,
  },
  {
    name: 'W8b 顶线不再限定只有凸起（凹也被画上一道线）',
    file: HERO,
    pairs: [['<template v-if="s.line">', '<template v-if="true">']],
    expect: /没有挂在「只有凸起才画」/,
  },
  {
    name: 'W8c 某个凸起没有颜色（垛口变秃，而且和前一段连成两个凹）',
    file: HERO,
    pairs: [["{ x: 240,  y: 32, line: '#a78bfa' },", "{ x: 240,  y: 32 },"]],
    expect: /是两个凹挨着|凸起有 \d+ 段|凹有 \d+ 段|没有合法颜色/,
  },
  {
    name: 'W8d 某个凹比凸还高（高低交错，城墙退化成随机锯齿）',
    file: HERO,
    pairs: [['{ x: 840,  y: 56 },', '{ x: 840,  y: 20 },']],
    expect: /并不比最深的凹|随机锯齿/,
  },

  // ── 城墙：向右漂移 ────────────────────────────────────
  {
    name: 'W9 漂移距离不等于一个周期宽（循环接缝横向跳一下）',
    file: HERO,
    pairs: [['to   { transform: translateX(1440px); }', 'to   { transform: translateX(1200px); }']],
    expect: /接缝/,
  },
  {
    name: 'W10 漂移方向反过来（城墙往左滑）',
    file: HERO,
    pairs: [['to   { transform: translateX(1440px); }', 'to   { transform: translateX(-1440px); }']],
    expect: /方向反了/,
  },
  {
    name: 'W11 漂移改成缓动（每轮循环点明显顿一下）',
    file: HERO,
    pairs: [['animation: wall-drift 12s linear infinite', 'animation: wall-drift 12s ease-in-out infinite']],
    expect: /缓动/,
  },
  {
    name: 'W12 CSS 里的漂移周期和 WALL_DRIFT_SEC 各写各的（改一处没人会发现）',
    file: HERO,
    pairs: [['animation: wall-drift 12s linear infinite', 'animation: wall-drift 10s linear infinite']],
    expect: /同一件事两个数/,
  },
  {
    name: 'W13 漂移和浮动改成并列两个 <g>（transform 不相乘，不会边上下边向右）',
    file: HERO,
    pairs: [[L('        <g class="wall-drift">', '          <g v-for="s in wallSteps" :key="s.id"'),
      L('        <g class="wall-drift"></g>', '          <g v-for="s in wallSteps" :key="s.id"')]],
    expect: /并列的两个 <g>/,
  },
  {
    name: 'W14 .wall-drift 的 animation 简写里混进第二条动画（后一条把前一条顶掉）',
    file: HERO,
    pairs: [['animation: wall-drift 12s linear infinite;',
      'animation: wall-drift 12s linear infinite, wall-bob 2.4s linear infinite;']],
    expect: /顶掉/,
  },
  {
    name: 'W15 第二份副本不减 WALL_WIDTH（两份叠一起，左边空右边重影）',
    file: HERO,
    pairs: [['x: s.x - WALL_WIDTH,', 'x: s.x,']],
    expect: /两份副本叠在同一处/,
  },
  {
    name: 'W16 只拼一份副本（带子滑出去就再也补不回来）',
    file: HERO,
    pairs: [[L(
      '  ...wallBase.map((s, i) => ({',
      '    ...s, id: `b${i}`, delay: i * WALL_STEP_DELAY, x: s.x - WALL_WIDTH,',
      '  })),',
    ), '']],
    expect: /应为两份副本各一份|右侧会滑空/,
  },
  {
    name: 'W17 两份副本用不同 delay（接缝那一段会突然换动作）',
    file: HERO,
    pairs: [['delay: i * WALL_STEP_DELAY, x: s.x - WALL_WIDTH,',
      'delay: (i + 6) * WALL_STEP_DELAY, x: s.x - WALL_WIDTH,']],
    expect: /两份副本的 delay 不一样|delay 表达式是/,
  },
  {
    name: 'W18 两份副本 id 前缀撞车（Vue key 重复，丢掉一半）',
    file: HERO,
    pairs: [['`b${i}`', '`a${i}`']],
    expect: /id 前缀/,
  },

  // ── 城墙：底边不露缝 ──────────────────────────────────
  {
    name: 'W19 块底只伸到 viewBox 边缘（凸起一上浮底下就空）',
    file: HERO,
    pairs: [[':height="100 - s.y"', ':height="80 - s.y"']],
    expect: /块底刚好等于 viewBox|余量不够/,
  },
  {
    name: 'W20 svg 不裁溢出（超出的一截画到 section 外面）',
    file: HERO,
    pairs: [['<svg class="w-full h-14 sm:h-20 overflow-hidden"', '<svg class="w-full h-14 sm:h-20"']],
    expect: /overflow-hidden|没有 viewBox/,
  },

  // ── 城墙：扫光 ────────────────────────────────────────
  {
    name: 'W21 扫光横向改成纵向（不是从左到右）',
    file: HERO,
    pairs: [[L(
      '@keyframes wall-shine {',
      '  from { transform: translateX(0); }',
      '  to   { transform: translateX(120px); }',
      '}',
    ), L(
      '@keyframes wall-shine {',
      '  from { transform: translateY(0); }',
      '  to   { transform: translateY(120px); }',
      '}',
    )]],
    expect: /扫光会变成上下|扫光方向不对/,
  },
  {
    name: 'W22 扫光位移 120 -> 60（扫不到线头）',
    file: HERO,
    pairs: [[L('@keyframes wall-shine {', '  from { transform: translateX(0); }',
      '  to   { transform: translateX(120px); }', '}'),
      L('@keyframes wall-shine {', '  from { transform: translateX(0); }',
        '  to   { transform: translateX(60px); }', '}')]],
    expect: /扫不到头/,
  },
  {
    name: 'W23 渐变 id 与 rect 的 url 引用对不上（扫光层静默渲染成空）',
    file: HERO,
    pairs: [[':id="`wall-shine-${s.id}`"', ':id="`wall-shine-g-${s.id}`"']],
    expect: /对不上时扫光层渲染成空|扫光 rect 没有 url/,
  },
  {
    name: 'W24 扫光渐变被移出 <defs>（直接当可见图形画出来）',
    file: HERO,
    pairs: [['        <defs>\n', ''], ['        </defs>\n', '']],
    expect: /没有 <defs>|不在 <defs> 内/,
  },
  {
    name: 'W25 相邻两个凸起同色（两座垛口看成一根长条）',
    file: HERO,
    pairs: [["line: '#a78bfa' },", "line: '#7c3aed' },"]],
    expect: /并排同色/,
  },
  {
    name: 'W26 循环接缝那对凸起同色（末尾与开头撞色）',
    file: HERO,
    pairs: [["line: '#06b6d4' },", "line: '#7c3aed' },"]],
    expect: /循环接缝/,
  },
  {
    name: 'W26b 某个颜色不是 @theme 里的（露出不属于本站色系的杂色）',
    file: HERO,
    pairs: [["line: '#ec4899' },", "line: '#00ff00' },"]],
    expect: /@theme 里不存在/,
  },
  {
    name: 'W26c 某个品牌色族被换掉（四族少一族，色谱断了）',
    file: HERO,
    pairs: [["line: '#a855f7' },", "line: '#c4b5fd' },"]],
    expect: /没有 violet|没有 purple|没有 pink|没有 cyan|色谱断了/,
  },
  {
    name: 'W26d 扫光渐变不再按凸起过滤（给凹也生成一条没人用的扫光带）',
    file: HERO,
    pairs: [['v-for="s in wallSteps.filter(w => w.line)"', 'v-for="s in wallSteps"']],
    expect: /没有按凸起遍历|没有按 line 过滤/,
  },
  {
    name: 'W26e 扫光又有了独立延迟（高光和起伏对不上拍）',
    file: HERO,
    pairs: [['delay: i * WALL_STEP_DELAY })),',
      'delay: i * WALL_STEP_DELAY, shineDelay: i * WALL_STEP_DELAY })),']],
    expect: /又有 shineDelay/,
  },
  {
    name: 'W27 浮动与扫光不同周期（两个动画周期性错拍，像卡带）',
    file: HERO,
    pairs: [['animation: wall-shine 2.4s linear infinite', 'animation: wall-shine 3.1s linear infinite']],
    expect: /看着像卡带/,
  },
  {
    name: 'W28 两段 x 相同（段距不等宽，底部露参差的口子）',
    file: HERO,
    pairs: [["{ x: 720,  y: 34, line: '#ec4899'", "{ x: 240,  y: 34, line: '#ec4899'"]],
    expect: /没有递增|段距不等宽/,
  },
]

/**
 * 按字节保真地应用若干对替换。
 *
 * 先归一化行尾符再替换、替换完还原成原文件那一种：模式串若直接按 \n 去匹配
 * 一个 CRLF 文件，会一个字符都命中不了 —— 而「锚点没命中」和「测试守不住」
 * 在报告里长得一模一样。归一化只在内存里做，磁盘上的文件不会被动到。
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

/**
 * 取第一条失败：用例标题 + 断言消息。
 *
 * 只看标题是不够的 —— 标题说的是「哪条用例挂了」，断言消息才是「它为什么挂」。
 * expect 写的是针对某个具体失效的断言消息，所以两段都得喂给它。
 *
 * 而且必须从 spec reporter 的 `✖ failing tests:` 汇总段往后找：汇总里是平铺的
 * 用例行，而正文里同名的还有一层 describe（`✖ 城墙 · … (2.6ms)`），
 * 从头扫第一个 ✖ 拿到的是 describe，整份报告的 ⚠ 就全成了噪声。
 */
function firstFailure(out) {
  const lines = out.split('\n')
  const marker = lines.findIndex((l) => /^✖ failing tests:/.test(l))
  const start = marker >= 0 ? marker + 1 : 0
  for (let i = start; i < lines.length; i++) {
    const m = lines[i].match(/^✖ (.+?) \([\d.]+ms\)$/)
    if (!m) continue
    const buf = []
    for (let j = i + 1; j < lines.length; j++) {
      if (/^✖ /.test(lines[j]) || /^#/.test(lines[j])) break
      if (/^\s+at /.test(lines[j])) break
      buf.push(lines[j])
    }
    const e = buf.join('\n').match(/AssertionError[^\n]*:\s*([\s\S]*)$/)
    return { title: m[1].trim(), text: `${m[1].trim()}\n${e ? e[1].trim() : '(没抓到断言消息)'}` }
  }
  return { title: '(输出里没找到失败项)', text: out.slice(0, 400) }
}

let killed = 0
const survivors = []
const broken = []
const offTarget = []

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
      const f = firstFailure(out)
      const hit = m.expect.test(f.text)
      killed++
      if (hit) {
        console.log(`KILLED  ${m.name}  (ran=${nRan})`)
      } else {
        // 杀是杀了，但挂的是另一条用例 —— 说明这条变异顺带弄坏的东西
        // 比它声称要弄坏的多，或者指定来守它的那条断言其实没参与。
        offTarget.push(`${m.name}\n        挂在: ${f.title}\n        期望匹配: ${m.expect}`)
      }
      console.log(`        首个失败: ${f.title}`)
      if (!hit) console.log(`        ⚠ 杀它的是另一条断言，不是这条变异指定的那条`)
    }
  } finally {
    copyFileSync(backup, m.file)
    unlinkSync(backup)
  }
}

console.log('='.repeat(72))
console.log(`变异结果：${killed}/${MUTANTS.length} 杀，${survivors.length} 存活，`
  + `${broken.length} 装置故障，${offTarget.length} 杀错用例`)
if (offTarget.length) {
  console.log('--- 杀了但挂错用例（指定来守它的那条断言没参与）---')
  for (const o of offTarget) console.log('  ' + o)
}
if (survivors.length) {
  console.log('--- 存活（测试没在验，或装置坏了）---')
  for (const s of survivors) console.log('  ' + s)
}
if (broken.length) {
  console.log('--- 装置故障 ---')
  for (const b of broken) console.log('  ' + b)
}
process.exit(survivors.length || broken.length || offTarget.length ? 1 : 0)
