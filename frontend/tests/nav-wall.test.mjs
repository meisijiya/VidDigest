/**
 * 分支 feat/nav-community-pixel-wall 的回归测试。
 *
 * 三件事：
 *   1. 社区入口从首页那个孤立的「浏览社区 →」按钮，搬进顶部导航（常驻）
 *   2. 底部城墙：整条带子**向右**滑（凸起横向走），每段再**上下**浮（凸起纵向走）
 *   3. 底边固定：凸起浮起来时底下不露缝
 *
 * 为什么单独一个文件：这处是**纯视觉**改动，原有用例对它全绿——删掉漂移、删掉
 * 波浪、删掉底边补偿，没有一条会转红。下面每条都按「把这个特性整个删掉，
 * 它还会绿吗」自查过。
 *
 * ⚠️ 一条实测踩过的坑：browser 的 query(kind=dom) 序列化 SVG 时会**丢掉属性**
 * （rect 只剩一个 data-v-* 残壳），所以「渲染出来的 DOM 对不对」不能靠它判断，
 * 也不能因为它没显示 <defs> 就断言渐变没渲染。下面只断言源码接线；
 * 动画真的在动由 Chrome DevTools 实测（见本文件末尾注释）。
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

/** 连 HTML 注释一起去掉。
 *  城墙那段模板上方有大段 <!-- --> 注释，里面写着 `.wall-drift 整条带子向右平移`
 *  之类的话；不剥掉的话，断言「源码里有没有 .wall-drift」会命中注释里的字，
 *  而那个特性真的被删掉时测试照样绿。 */
function stripComments(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    // 协议里的 // 不是注释：只剥「前面不是冒号」的那种，否则
        // `https://x.com` 会被削成 `https:`，域名连同后面整行一起消失，
        // 扫源码的断言于是永远看不到它 —— 这条判据会变成死的。
    .map((l) => l.replace(/(^|[^:])\/\/.*$/, '$1'))
    .join('\n')
}

const appVue = stripComments(read('../src/App.vue'))
const headerVue = stripComments(read('../src/components/AppHeader.vue'))
const heroVueRaw = read('../src/components/HeroSection.vue')
const heroVue = stripComments(heroVueRaw)

/** 抠出一个数值常量。城墙的周期、宽度全靠这几个数，改了得能从这里看见。 */
function constOf(name) {
  const m = heroVue.match(new RegExp(`const ${name} = ([\\d.]+)`))
  assert.ok(m, `HeroSection 里没有常量 ${name} —— 城墙的数值又散回字面量了？`)
  return Number(m[1])
}

const WALL = {
  width: constOf('WALL_WIDTH'),
  driftSec: constOf('WALL_DRIFT_SEC'),
  bobSec: constOf('WALL_BOB_SEC'),
  stepDelay: constOf('WALL_STEP_DELAY'),
}

/** wallBase：12 段基础阶梯，y 决定凸起高度，line 是顶线颜色（每段都有） */
function wallBase() {
  const start = heroVue.indexOf('const wallBase = [')
  assert.ok(start >= 0, 'HeroSection 里没有 wallBase —— 城墙退回手写 rect 了？')
  const end = heroVue.indexOf('\n]', start)
  assert.ok(end > start, 'wallBase 数组没有正常闭合')
  return heroVue.slice(start, end)
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.startsWith('{ x:'))
    .map((l) => ({
      x: Number(l.match(/x:\s*(\d+)/)[1]),
      y: Number(l.match(/y:\s*(\d+)/)[1]),
      line: parseColor(l),
    }))
}

/** 从一行字面量里取 line 的值。返回 null 或 6 位 hex（不带引号）——
 *  带着引号返回的话，下游和 @theme 对账会全线失配，而失配长得像「配色不合规」。 */
function parseColor(lineSrc) {
  const m = lineSrc.match(/line:\s*(null|'#[0-9A-Fa-f]{6}')/)
  if (!m) return null
  return m[1] === 'null' ? null : m[1].slice(1, -1).toLowerCase()
}

/** style.css 的 @theme 里声明过的全部 hex。
 *  「颜色风格跟 UI 统一」这句话写进注释不算数 —— 只有把每个城墙颜色
 *  拿去和这里对账，才是真的锁住了：随手填个 #00ff00 会当场转红。 */
function themeHexes() {
  const css = read('../src/style.css')
  const theme = css.slice(css.indexOf('@theme {'), css.indexOf('\n}', css.indexOf('@theme {')))
  return new Set((theme.match(/#[0-9a-fA-F]{6}/g) || []).map((h) => h.toLowerCase()))
}

/** @theme 里每个 hex 属于哪个色族。用来判断城墙是「色谱」还是「同族深浅」。
 *  从 @theme 反查而不是在测试里再写死一份色值：写死的那份迟早会过期，
 *  过期之后它锁的就不是当前设计，而是历史遗留。 */
function themeFamilies() {
  const css = read('../src/style.css')
  const theme = css.slice(css.indexOf('@theme {'), css.indexOf('\n}', css.indexOf('@theme {')))
  const map = new Map()
  for (const m of theme.matchAll(/--color-([a-z]+)-(\d{2,3}):\s*(#[0-9a-fA-F]{6})/g)) {
    map.set(m[3].toLowerCase(), m[1])
  }
  return map
}

function wallStepsSrc() {
  const start = heroVue.indexOf('const wallSteps = [')
  assert.ok(start >= 0, 'HeroSection 里没有 wallSteps')
  const end = heroVue.indexOf('\n]', start)
  assert.ok(end > start, 'wallSteps 没有正常闭合')
  return heroVue.slice(start, end)
}

/** 取某个 @keyframes 的函数体 */
function keyframeBody(name) {
  const m = heroVueRaw.match(new RegExp(`@keyframes ${name}\\s*\\{([\\s\\S]*?)\\n\\}`))
  assert.ok(m, `没有 @keyframes ${name}`)
  return m[1]
}

/** 0.1s 量级的秒数在二进制里是近似值：12 × 0.2 算出来是 2.4000000000000004。
 *  直接 assert.equal 拿去比 2.4 会红，而红的原因跟城墙没关系。 */
const EPS = 1e-9
function near(actual, expected) {
  return Math.abs(actual - expected) < EPS
}

/** 抠出一个 delay 表达式的值。分隔符要一起排除：
 *  `delay: i * WALL_STEP_DELAY })),` 里若只按逗号切，会把后面的 `}))` 一起吃进来。 */
function delayExprs() {
  return [...wallStepsSrc().matchAll(/delay:\s*([^,}\n]+)/g)].map((m) => m[1].trim())
}

/** 取某条 class 规则的声明块 */
function cssRule(selector) {
  const m = heroVue.match(new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`))
  assert.ok(m, `样式里没有 ${selector} 这条规则`)
  return m[1]
}

/** 城墙 svg 的 viewBox。
 *  ① 必须按 overflow-hidden 认领 —— 同一文件里还有三个装饰用的小 svg
 *     （那个 64×64 的像素箭头），认错对象的话下面「块底超出多少」的判断
 *     就是在拿 64 跟 100 比，结论看着成立、其实什么都没验。
 *  ② viewBox 是**四个**数 minX minY width height，只抓前两个会永远匹配不上。 */
function viewBox() {
  const m = heroVue.match(/<svg class="[^"]*overflow-hidden[^"]*" viewBox="([\d.\s-]+)"/)
  assert.ok(m, '城墙 svg 上没有 viewBox')
  const n = m[1].trim().split(/\s+/).map(Number)
  assert.equal(n.length, 4,
    `viewBox 是「${m[1]}」，应当是 minX minY width height 四个数`)
  return { minX: n[0], minY: n[1], w: n[2], h: n[3] }
}

/** 块的绑定属性：宽度、底边表达式 */
function blockSpec() {
  const m = heroVue.match(/<rect :x="s\.x" :y="s\.y" width="(\d+)" :height="([^"]+)"/)
  assert.ok(m, '城墙的块没写成 :x/:y/:width/:height 绑定的形式')
  return { width: Number(m[1]), heightExpr: m[2] }
}


// ─────────────────────────────────────────────────────────
// 导航里的社区入口
// ─────────────────────────────────────────────────────────
describe('社区入口 · 在顶部导航里常驻', () => {
  test('AppHeader 有「社区」按钮，且在 nav 里', () => {
    assert.match(headerVue, /<nav[\s\S]*?社区[\s\S]*?<\/nav>/,
      '导航里没有「社区」项')
  })

  test('点击社区会 emit open-community', () => {
    const m = headerVue.match(/<button[^>]*@click="\$emit\('open-community'\)"[^>]*>/)
    assert.ok(m, '社区按钮没有 emit open-community')
    assert.match(m[0], /社区|community/i, '这个按钮不是社区入口')
  })

  test('open-community 在 defineEmits 里（漏声明 = 点击静默失效）', () => {
    const m = headerVue.match(/defineEmits\(\[([^\]]*)\]\)/)
    assert.ok(m, '找不到 defineEmits')
    assert.match(m[1], /['"]open-community['"]/,
      "open-community 没进 defineEmits —— Vue 会对未声明的事件名发警告，而点击**看起来正常**，只是父组件永远收不到")
  })

  test('App.vue 接上了这个事件', () => {
    assert.match(appVue, /@open-community="openCommunity"/,
      'App.vue 没监听 open-community —— 点了社区，页面不动')
  })

  test('openCommunity 真的切页', () => {
    const m = appVue.match(/function openCommunity\(\)\s*\{([\s\S]*?)\n\}/)
    assert.ok(m, 'App.vue 里没有 openCommunity 函数')
    assert.match(m[1], /currentPage\.value\s*=\s*'community'/,
      'openCommunity 没有把 currentPage 切到 community')
  })

  test('当前页高亮：社区页上「社区」要有激活样式', () => {
    const m = headerVue.match(/@click="\$emit\('open-community'\)"[\s\S]*?:class="\[[\s\S]*?\]"/)
    assert.ok(m, '社区按钮的 class 绑定没找到')
    assert.match(m[0], /page\s*===\s*'community'/,
      '社区页上「社区」不高亮 —— 和首页 / 历史不一致，用户不知道自己在哪')
  })

  test('首页那个孤立的「浏览社区 →」按钮已下线（否则是两个入口）', () => {
    assert.doesNotMatch(appVue, /浏览社区/,
      '首页营销区还留着「浏览社区 →」按钮 —— 入口重复，用户会不知道该点哪个')
  })

  test('入口是常驻的：AppHeader 挂在 <main> 之外，没有被条件渲染包住', () => {
    // 不做括号配对（template 嵌套 + v-else 链会让「最后一个 v-if 有没有闭合」
    // 变成一个需要真解析器的判断）。直接断言真正要保证的东西：
    // AppHeader 是根节点下的直接子节点，且和 <main> 之间没有任何 v-if / v-else。
    const headerIdx = appVue.indexOf('<AppHeader')
    const mainIdx = appVue.indexOf('<main')
    assert.ok(headerIdx >= 0, 'App.vue 里找不到 AppHeader')
    assert.ok(mainIdx > headerIdx, 'AppHeader 排到了 <main> 之后')

    const between = appVue.slice(headerIdx, mainIdx)
    assert.doesNotMatch(between, /v-if=|v-else/,
      'AppHeader 与 <main> 之间隔着条件渲染 —— 社区入口不是常驻的，'
      + '而这正是「解析出视频之后社区就没了」那个毛病的形态')
  })
})


// ─────────────────────────────────────────────────────────
// 城墙：底边固定，凸起上下浮
// ─────────────────────────────────────────────────────────
describe('城墙 · 底边固定，凸起上下浮', () => {
  test('12 段阶梯，x 严格递增', () => {
    const xs = wallBase().map((s) => s.x)
    assert.equal(xs.length, 12, `阶梯段数是 ${xs.length}，不是 12`)
    for (let i = 1; i < xs.length; i++) {
      assert.ok(xs[i] > xs[i - 1], `x 在第 ${i} 段没有递增：${xs[i - 1]} -> ${xs[i]}`)
    }
  })

  test('每段刚好铺满一块宽（段距 = 块宽，不留缝也不重叠）', () => {
    const base = wallBase()
    const { width } = blockSpec()
    for (let i = 1; i < base.length; i++) {
      const gap = base[i].x - base[i - 1].x
      assert.equal(gap, width,
        `第 ${i} 段离前一段 ${gap}，块宽是 ${width} —— 段距不等宽，底部会露出参差的口子`)
    }
  })

  test('12 段正好铺满一个周期宽（多一格循环点会跳，少一格右边会空）', () => {
    const base = wallBase()
    const { width } = blockSpec()
    const span = base[base.length - 1].x + width
    assert.equal(span, WALL.width,
      `12 段铺出来是 ${span}，周期宽是 ${WALL.width} —— 循环时第二份接不上第一份`)
  })

  test('每段凸起高度不一（全是同一个 y 就是一道平墙，不是浪）', () => {
    const ys = wallBase().map((s) => s.y)
    assert.ok(new Set(ys).size >= 4,
      `凸起高度只有 ${new Set(ys).size} 种 —— 城墙是平的，没有起伏`)
  })

  test('delay 随段号递增，浪才是往右推的', () => {
    const exprs = delayExprs()
    assert.equal(exprs.length, 2, `wallSteps 里解析出 ${exprs.length} 份 delay，应为两份副本各一份`)
    for (const e of exprs) {
      assert.match(e, /^i\s*\*\s*WALL_STEP_DELAY$/,
        `delay 表达式是「${e}」—— 写成 (n-1-i) 之类的话，浪会往**左**走`)
    }
    assert.ok(WALL.stepDelay > 0, 'WALL_STEP_DELAY 不是正数，所有段同相位，连不成波')
  })

  test('错峰总跨度小于浮动周期（第 12 段不能追上第 1 段）', () => {
    const n = wallBase().length
    const spread = (n - 1) * WALL.stepDelay
    assert.ok(spread < WALL.bobSec,
      `错峰跨度 ${spread}s >= 周期 ${WALL.bobSec}s —— 浪往回走`)
  })

  test('段数 × 步进 = 浮动周期（波是连续推过去的，中间没有全员静止的空窗）', () => {
    const n = wallBase().length
    assert.ok(near(n * WALL.stepDelay, WALL.bobSec),
      `${n} × ${WALL.stepDelay}s = ${n * WALL.stepDelay}s，浮动周期是 ${WALL.bobSec}s ——`
      + '要么有一段「所有块都停在原位」的空窗，要么相位首尾接不回、循环点会跳')
  })

  test('wall-bob 只做上下位移（浪是上下，不是左右）', () => {
    const body = keyframeBody('wall-bob')
    assert.match(body, /translateY\(/, 'wall-bob 没有 translateY，凸起不会浮')
    assert.doesNotMatch(body, /translateX\(/,
      'wall-bob 里混进了 translateX —— 和外层的漂移叠在一起，凸起会斜着晃')
  })

  test('wall-step class 真的挂在 v-for 的 <g> 上（漏挂一段，那段就是块死砖）', () => {
    const groups = heroVue.match(/class="wall-step"/g) || []
    assert.equal(groups.length, 1,
      '模板里 wall-step 出现次数异常（应当靠 v-for 挂到每一段上）')
    assert.match(heroVue, /<g v-for="s in wallSteps"[\s\S]*?class="wall-step"/,
      'wall-step 不在 v-for 的 <g> 上 —— 浮动只作用在一段上，或者根本没作用')
  })

  test('浮动延迟真的绑到了每个 <g> 上（数据里有 delay，模板没绑 = 全员同相位）', () => {
    assert.match(heroVue, /<g v-for="s in wallSteps"[\s\S]*?:style="\{ animationDelay: s\.delay \+ 's' \}"/,
      'wall-step 的 <g> 上没有 animationDelay 绑定 —— 每段 delay 都白算了')
  })

  test('块和它顶上的渐变线在同一个 <g> 里（分开写线，一浮动线就脱节）', () => {
    // 切片止于第一个 </g>，所以「顶线还在不在这个 <g> 里」直接数 rect 就够：
    // 两个（块 / 顶线）。写成 <g v-if> 包一层就变成 1 个。
    const g = heroVue.match(/<g v-for="s in wallSteps"[\s\S]*?<\/g>/)
    assert.ok(g, '找不到城墙的 <g>')
    const rects = g[0].match(/<rect/g) || []
    assert.equal(rects.length, 2,
      `块的 <g> 里只有 ${rects.length} 个 rect —— 顶线跑到 <g> 外面去了，一浮动就脱节`)
    assert.match(g[0], /<template v-if="s\.line">/,
      '顶线没有挂在「只有凸起才画」的条件上 —— 凹也会被画上一道线')
    assert.match(g[0], /url\(#/, '顶线不是渐变填充')
  })
})


// ─────────────────────────────────────────────────────────
// 城墙：整条带子向右滑
// ─────────────────────────────────────────────────────────
describe('城墙 · 凸起像海浪一样向右滑', () => {
  test('漂移终点位移正好一个周期宽（这是接缝不跳的唯一条件）', () => {
    const m = keyframeBody('wall-drift').match(/to\s*\{[^}]*translateX\((-?[\d.]+)px\)/)
    assert.ok(m, 'wall-drift 的终点没有 translateX —— 带子不会滑')
    assert.equal(Math.abs(Number(m[1])), WALL.width,
      `漂移 ${Math.abs(m[1])}px ≠ 一个周期 ${WALL.width}px —— `
      + '带子是由两份拼的，滑不到一整圈，第二份就补不进第一份让出的位置，循环点会横向跳一下')
  })

  test('漂移方向是向右（终点位移为正）', () => {
    const m = keyframeBody('wall-drift').match(/to\s*\{[^}]*translateX\((-?[\d.]+)px\)/)
    assert.ok(Number(m[1]) > 0,
      `终点位移是 ${m[1]}px —— 城墙在往**左**滑，方向反了`)
  })

  test('漂移起点是 0（起点不为 0 会凭空少一截/多一截）', () => {
    // 起点写的是 translateX(0)，没有 px 单位 —— 正则里 px 必须可选，
    // 否则这条永远匹配不上，而它本该守的东西一次都没验。
    const m = keyframeBody('wall-drift').match(/from\s*\{[^}]*translateX\((-?[\d.]+)(?:px)?\)/)
    assert.ok(m, 'wall-drift 的起点没有 translateX')
    assert.equal(Number(m[1]), 0, `起点是 ${m[1]}px，不该有偏移`)
  })

  test('漂移是匀速 + 无限循环（ease 会每轮顿一下再起步）', () => {
    assert.match(cssRule('.wall-drift'), /animation:\s*wall-drift\s+[\d.]+s\s+linear\s+infinite/,
      '漂移不是「匀速 + 无限循环」—— 缓动会让城墙在循环点上明显顿一下')
  })

  test('CSS 里的漂移周期 = WALL_DRIFT_SEC（两处各写一份，改一处就永久错拍）', () => {
    const css = Number(cssRule('.wall-drift').match(/wall-drift\s+([\d.]+)s/)[1])
    assert.equal(css, WALL.driftSec,
      `CSS 写 ${css}s、常量写 ${WALL.driftSec}s —— 同一件事两个数，改一处没人会发现`)
  })

  test('漂移周期是浮动周期的整数倍（两层动画才不会周期性错拍）', () => {
    const ratio = WALL.driftSec / WALL.bobSec
    // 12 / 2.4 在浮点里是 4.999999999999999，同样不能直接比整数。
    assert.ok(near(ratio, Math.round(ratio)),
      `漂移 ${WALL.driftSec}s ÷ 浮动 ${WALL.bobSec}s = ${ratio}，不是整数 —— `
      + '两层动画各自循环，公共周期被拉长到 lcm，看着会像卡带')
  })

  test('漂移和浮动分属**嵌套两层** <g>（同一个元素上写两条 animation 只会活一条）', () => {
    const driftIdx = heroVue.indexOf('<g class="wall-drift">')
    assert.ok(driftIdx >= 0, '模板里没有 <g class="wall-drift">')
    const after = heroVue.slice(driftIdx)
    const inner = after.slice(0, after.indexOf('</g>'))
    assert.match(inner, /v-for="s in wallSteps"/,
      'wall-drift 是空的 —— 漂移和浮动是并列的两个 <g> 而不是嵌套，'
      + '那两条 transform 不相乘，凸起不会「边上下边向右」')
  })

  test('漂移那条规则里没有混进别的动画（animation 简写会互相覆盖）', () => {
    const rule = cssRule('.wall-drift')
    assert.match(rule, /animation:\s*wall-drift\s/, '.wall-drift 上没有 wall-drift 动画')
    assert.doesNotMatch(rule, /wall-bob|wall-shine/,
      '.wall-drift 的 animation 简写里混进了别的动画名 —— 一条规则只能有一条 animation，后一条把前一条顶掉')
  })

  test('带子由两份 wallBase 拼成（只有一份 = 滑出去就再也补不回来）', () => {
    const n = wallBase().length
    const maps = wallStepsSrc().match(/\.\.\.wallBase\.map\(/g) || []
    assert.equal(maps.length, 2,
      `wallSteps 里只有 ${maps.length} 份 wallBase —— 右侧会滑空`)
    assert.equal(n * 2, 24, `两份拼起来应当是 ${n * 2} 段`)
  })

  test('第二份整体左移一个周期宽（不偏移就两份叠在同一处）', () => {
    assert.match(wallStepsSrc(), /x:\s*s\.x\s*-\s*WALL_WIDTH/,
      '第二份没有减掉 WALL_WIDTH —— 两份副本叠在同一处，左边会空、右边会重影')
  })

  test('两份副本的 id 前缀不同（key 撞车，Vue 会丢掉一半）', () => {
    const src = wallStepsSrc()
    assert.match(src, /id:\s*`a\$\{i\}`/, '第一份副本的 id 前缀没了')
    assert.match(src, /id:\s*`b\$\{i\}`/, '第二份副本的 id 前缀没了')
    assert.match(heroVue, /:key="s\.id"/, '<g> 上没有 :key="s.id"')
  })

  test('两份副本共用同一份 delay（否则同一段墙在两处不同相位，循环点上会跳）', () => {
    const exprs = delayExprs()
    assert.equal(exprs.length, 2, `解析出 ${exprs.length} 份 delay`)
    assert.equal(exprs[0], exprs[1],
      `两份副本的 delay 不一样（${exprs[0]} vs ${exprs[1]}）—— 同一段墙在左右两处`
      + '会做不同动作，带子滑到接缝时那一段会突然「换动作」')
  })
})


// ─────────────────────────────────────────────────────────
// 城墙：底边不露缝
// ─────────────────────────────────────────────────────────
describe('城墙 · 底边固定，浮起来不露缝', () => {
  test('块底伸出到 viewBox 之外，且余量 ≥ 最大上浮量', () => {
    const vb = viewBox()
    const spec = blockSpec()
    const hm = spec.heightExpr.match(/^(\d+)\s*-\s*s\.y$/)
    assert.ok(hm, `块高表达式是「${spec.heightExpr}」，认不出块底在哪`)

    const ys = [...keyframeBody('wall-bob').matchAll(/translateY\((-?[\d.]+)px\)/g)]
      .map((m) => Number(m[1]))
    const maxUp = Math.abs(Math.min(0, ...ys))

    const bottom = Number(hm[1])
    assert.ok(bottom >= vb.h + maxUp,
      `块底在 y=${bottom}，viewBox 高 ${vb.h}、最大上浮 ${maxUp} —— `
      + '余量不够，凸起浮到最高点时底边会从缝里透出页面背景')
  })

  test('块底是算出来的，不是写死的 y（写死 80 就等于齐边，一浮就露）', () => {
    const spec = blockSpec()
    assert.match(spec.heightExpr, /s\.y/,
      `块高是「${spec.heightExpr}」，没有跟着 s.y 变 —— `
      + '不同高度的段会齐着同一条底，底边就不再是「底边固定的一堵墙」')
    assert.notEqual(Number(spec.heightExpr.match(/^(\d+)/)[1]), viewBox().h,
      '块底刚好等于 viewBox 高度 —— 凸起一上浮，底下就空了')
  })

  test('svg 裁掉溢出部分（没有 overflow-hidden，超出的一块会盖到下面的内容上）', () => {
    assert.match(heroVue, /<svg class="[^"]*overflow-hidden[^"]*" viewBox=/,
      '城墙 svg 没有 overflow-hidden —— 伸到 viewBox 之外的那截会画到 section 外面')
  })
})


// ─────────────────────────────────────────────────────────
// 城墙：顶线渐变显现
// ─────────────────────────────────────────────────────────
describe('城墙 · 顶线渐变显现', () => {
  test('凸 / 凹 严格交替（两个凸挨着就没有「垛口」可言了）', () => {
    const base = wallBase()
    for (let i = 1; i < base.length; i++) {
      assert.notEqual(!!base[i].line, !!base[i - 1].line,
        `x=${base[i - 1].x} 与 x=${base[i].x} 是${base[i].line && base[i - 1].line ? '两个凸' : '两个凹'}挨着 —— `
        + '城墙要求一凸一凹交替，两凸相连看不出垛口，两凹相连中间少了一座')
    }
  })

  test('凹一律比凸矮（分不出高低就没有凸起的形状）', () => {
    const base = wallBase()
    const towerY = base.filter((s) => s.line).map((s) => s.y)
    const notchY = base.filter((s) => !s.line).map((s) => s.y)
    assert.ok(towerY.length && notchY.length,
      `凸 ${towerY.length} 段、凹 ${notchY.length} 段，两种都得有`)
    assert.ok(Math.max(...towerY) < Math.min(...notchY),
      `最矮的凸 y=${Math.max(...towerY)} 并不比最深的凹 y=${Math.min(...notchY)} 高 —— `
      + '两者交错，城墙就是一片随机锯齿而不是「一排凸起」')
  })

  test('凸起都带颜色、凹都不带（6 : 6）', () => {
    const towers = wallBase().filter((s) => s.line)
    const notches = wallBase().filter((s) => !s.line)
    assert.equal(towers.length, 6, `凸起有 ${towers.length} 段`)
    assert.equal(notches.length, 6, `凹有 ${notches.length} 段`)
    for (const t of towers) {
      assert.match(String(t.line), /^#[0-9a-f]{6}$/,
        `x=${t.x} 这个凸起没有合法颜色（是 ${t.line}）`)
    }
  })

  test('相邻凸起颜色不同（含循环接缝那一对）', () => {
    const lines = wallBase().filter((s) => s.line).map((s) => s.line)
    for (let i = 1; i < lines.length; i++) {
      assert.notEqual(lines[i], lines[i - 1],
        `第 ${i - 1} 与第 ${i} 个凸起都是 ${lines[i]} —— 并排同色，中间那道分界就没了，`
        + '两座垛口会看成一根长条')
    }
    // 带子由两份 wallBase 拼成：末尾的凸起紧挨着下一份的开头，同样不能撞色。
    assert.notEqual(lines[lines.length - 1], lines[0],
      `末尾凸起 ${lines[lines.length - 1]} 和开头凸起 ${lines[0]} 同色 —— `
      + '带子滑到循环接缝时这两座会紧挨着同色')
  })

  test('凸起线条颜色一个都不重复（不只是相邻不同）', () => {
    const lines = wallBase().filter((s) => s.line).map((s) => s.line)
    const dup = lines.filter((c, i) => lines.indexOf(c) !== i)
    assert.equal(dup.length, 0,
      `颜色重复了：${[...new Set(dup)].join(' ')} —— 同一种颜色出现两次以上，`
      + '哪怕不相邻，城墙也读不出「每座垛口一个自己的色」')
  })

  test('每个凸起的颜色都取自 @theme（跟 UI 统一，不是随手挑的）', () => {
    const theme = themeHexes()
    assert.ok(theme.size > 20, `@theme 里只解析出 ${theme.size} 个色值，正则多半没对上`)
    const stray = wallBase().filter((s) => s.line).map((s) => s.line).filter((c) => !theme.has(c))
    assert.equal(stray.length, 0,
      `这些颜色在 style.css 的 @theme 里不存在：${stray.join(' ')} —— `
      + '城墙会露出不属于本站色系的杂色')
  })

  test('凸起横跨多个品牌色族，不是同族深浅凑数', () => {
    // 只查「色族覆盖」：6 个凸起全用主色的深浅也能通过上面的「互不相同」，
    // 但那就不是色谱、只是同色系的 6 根条。族从 @theme 反查，不在测试里写死。
    const families = themeFamilies()
    assert.ok(families.size > 20, `@theme 里只反查出 ${families.size} 个色值，解析多半没对上`)

    const lines = [...new Set(wallBase().filter((s) => s.line).map((s) => s.line))]
    const seen = new Set()
    for (const c of lines) {
      const fam = families.get(c)
      assert.ok(fam, `城墙色 ${c} 在 @theme 里查不到所属色族 —— 它的来路说不清`)
      seen.add(fam)
    }
    assert.ok(seen.size >= 3,
      `城墙只用了 ${seen.size} 个色族（${[...seen].join('/')}）—— `
      + '少于 3 个就不是色谱，是同色系的 6 根条')
  })

  test('渐变只给凸起生成（凹没有顶线）', () => {
    const defs = heroVue.match(/<defs>[\s\S]*?<\/defs>/)
    assert.ok(defs, '没有 <defs>')
    assert.match(defs[0], /v-for="s in wallSteps\.filter\(w => w\.line\)"/,
      '渐变的 v-for 没有按 line 过滤 —— 会给凹也生成一条渐变，'
      + '而凹的 line 是空的，这条渐变渲出来是空的')
  })

  test('顶线与凸起的起伏共用同一份相位（这段亮到最盛时正好浮到最高）', () => {
    const g = heroVue.match(/<g v-for="s in wallSteps"[\s\S]*?<\/g>/)
    assert.match(g[0], /class="wall-line"\s*\n\s*:style="\{ animationDelay: s\.delay \+ 's' \}"/,
      '顶线 rect 上没有 animationDelay 绑定 —— 亮起来的时刻和浮起来的时刻对不上')
    assert.doesNotMatch(heroVueRaw, /shineDelay|lineDelay/,
      '数据里又有独立的延迟字段了 —— 亮与浮一旦分成两套延迟，就对不上拍了')
  })

  test('顶线本身是渐变：两端羽化，不是一条硬边实色', () => {
    const defs = heroVue.match(/<defs>[\s\S]*?<\/defs>/)[0]
    assert.match(defs, /:stop-color="s\.line"/,
      '渐变用的不是这一段自己的颜色 —— 会渲成一条白带，而不是「这段的线」')
    const stops = defs.match(/<stop\b[^>]*\/>/g) || []
    assert.ok(stops.length >= 2, `渐变里只有 ${stops.length} 个 stop`)
    assert.match(stops[0], /offset="0%"/, '第一个 stop 不在 0%')
    assert.match(stops[0], /stop-opacity="0"/,
      '渐变起点不透明 —— 顶线是硬边，不是渐变显现')
    assert.match(stops[stops.length - 1], /offset="100%"/, '最后一个 stop 不在 100%')
    assert.match(stops[stops.length - 1], /stop-opacity="0"/,
      '渐变终点不透明 —— 顶线右端是硬边，不是渐变显现')
  })

  test('顶线靠 opacity 呼吸显现，不是靠位移', () => {
    // 这条是这个形态的核心：只要线里出现 translate，观感就退回
    // 「一条线全程可见 + 有个东西在它上面动」—— 正是要去掉的那个。
    const body = keyframeBody('wall-line')
    assert.match(body, /opacity/, 'wall-line 里没有 opacity —— 线条不会渐变地亮灭')
    // 只查「出现过 opacity」是不够的：把它写成全程 opacity: 1 也能过，
    // 而那正是「线一直全亮」。所以要查两件事 —— 值**变过**，且**暗过**。
    const vals = [...new Set([...body.matchAll(/opacity:\s*([\d.]+)/g)].map((m) => m[1]))]
    assert.ok(vals.length >= 2,
      `wall-line 的 opacity 全程同一个值（${vals.join(' ')}）—— 线条不呼吸，一直亮着`)
    assert.ok(vals.some((v) => Number(v) < 1),
      `wall-line 的 opacity 最低只到 ${vals.join(' ')} —— 线全程不透明，没有「渐变显现」`)
    assert.doesNotMatch(body, /translate[XY]\(/,
      'wall-line 里出现了位移 —— 那是「线全程亮着 + 一个光点在上面跑」')
    assert.match(cssRule('.wall-line'),
      /animation:\s*wall-line\s+[\d.]+s\s+[\w-]+\s+infinite/,
      '.wall-line 上没有「周期 + 缓动 + 无限循环」的 animation')
  })

  test('没有常驻的实色底线条（线全程可见就等于回到旧形态）', () => {
    const g = heroVue.match(/<g v-for="s in wallSteps"[\s\S]*?<\/g>/)[0]
    assert.doesNotMatch(g, /:fill="s\.line"/,
      '又加回了一条 :fill="s.line" 的实心线 —— 线条会全程可见，'
      + '那正是「线条完全显现」这个形态')
    assert.doesNotMatch(g, /stop-color="#fff"/,
      '渐变里混进了白色高光 —— 白带画在实线上就是「光点在动」')
  })

  test('渐变和引用它的 rect 绑在同一个 id 上（对不上 = 静默失效）', () => {
    // 不硬编码 'wall-line' 前缀：那样只要前缀一变，正则就整个失配，
    // 「两者相等」这个真正要守的判断反而永远轮不到触发。
    const idFull = heroVue.match(/:id="(`[^`]*`)"/)
    const urlRef = heroVue.match(/url\(#([^)]*)\)/)
    assert.ok(idFull, '渐变没有用模板生成动态 id')
    assert.ok(urlRef, '顶线 rect 没有 url(#…) 引用')

    assert.equal(idFull[1].slice(1, -1), urlRef[1],
      `渐变 id 是 ${idFull[1]}、rect 引用的是 #${urlRef[1]} —— 对不上时顶线渲染成空，**且不报任何错**`)
  })

  test('渐变挂在 <defs> 里（挂到 DOM 里会直接显示成一条色带）', () => {
    const defs = heroVue.match(/<defs>[\s\S]*?<\/defs>/)
    assert.ok(defs, '没有 <defs>')
    assert.match(defs[0], /<linearGradient/, '渐变不在 <defs> 内 —— 它会作为可见图形画出来')
  })

  test('渐变横跨整条线（x1→x2 是一段的宽度）', () => {
    const m = heroVue.match(/:x1="s\.x" :x2="s\.x \+ (\d+)"/)
    assert.ok(m, '渐变的横向范围没有按 s.x 铺开')
    assert.equal(Number(m[1]), blockSpec().width,
      `渐变铺了 ${m[1]}，线宽是 ${blockSpec().width} —— 线两端会有一截没吃到渐变`)
  })

  test('起伏与顶线同周期（不同步 = 看着很乱）', () => {
    const bob = Number(cssRule('.wall-step').match(/wall-bob\s+([\d.]+)s/)[1])
    const line = Number(cssRule('.wall-line').match(/wall-line\s+([\d.]+)s/)[1])
    assert.equal(bob, line,
      `起伏 ${bob}s、顶线 ${line}s 周期不同 —— 两个动画会周期性错拍，看着像卡带`)
  })
})


/**
 * 源码断言到不了的部分，浏览器实测（Chrome DevTools，evaluate_script）：
 *
 *   · translateX(1440px) 落在 SVG 内 <g> 上走的是 **1440 个用户单位**、不是 1440 屏幕像素
 *     —— t=6000ms 读到的 transform 是 matrix(1,0,0,1,720,0)，正好是 1440 的一半。
 *     这一条决定了循环接缝准不准：若是屏幕像素，499px 宽的窗口上会滑过 1.125 个周期、接缝必跳。
 *   · 循环点闭合：t=11999ms 时 a0 副本在右缘 499.29px、b0 副本在左缘 −0.04px，
 *     svg 宽 499.33px —— 差值正好一个周期宽，接缝严丝合缝。
 *   · 底边不露缝：把漂移停在 0/3000/6000/9000/11999ms × 浮动停在波峰/波谷共 10 个相位，
 *     逐个量 24 个块在 svg 最底下一行的并集，gaps 全为 0；浮到波峰时最低的块底
 *     仍超出 svg 底边 10.5px（被裁掉），所以底下永远是齐的。
 *
 * 内置 Browser 标签页的 CSS 动画是冻结的、且 query(kind=dom) 序列化 SVG 会丢属性，
 * 上面的数都是在 Chrome DevTools 那个页面上量的。
 */
