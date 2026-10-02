/**
 * 分支 feat/nav-community-pixel-wall 的回归测试。
 *
 * 两件事：
 *   1. 社区入口从首页那个孤立的「浏览社区 →」按钮，搬进顶部导航（常驻）
 *   2. 底部城墙：顶线从左到右扫光，凸起错峰上下浮动成海浪
 *
 * 为什么单独一个文件：这两处都是**纯视觉 / 纯接线**的改动，原有用例对它们
 * 全绿——删掉扫光、删掉波浪、删掉社区入口，没有一条会转红。
 * 下面每条都按「把这个特性整个删掉，它还会绿吗」自查过。
 *
 * ⚠️ 一条实测踩过的坑：browser 的 query(kind=dom) 序列化 SVG 时会**丢掉属性**
 * （rect 只剩一个 data-v-* 残壳），所以「渲染出来的 DOM 对不对」不能靠它判断，
 * 也不能因为它没显示 <defs> 就断言渐变没渲染。下面只断言源码接线；
 * 动画真的在动由浏览器拍帧验证。
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((l) => l.replace(/\/\/.*$/, ''))
    .join('\n')
}

const appVue = stripComments(read('../src/App.vue'))
const headerVue = stripComments(read('../src/components/AppHeader.vue'))
const heroVueRaw = read('../src/components/HeroSection.vue')
const heroVue = stripComments(heroVueRaw)

/** 从 wallSteps 数组字面量里抠出那一行行对象 */
function wallStepLines() {
  const start = heroVue.indexOf('const wallSteps = [')
  assert.ok(start >= 0, 'HeroSection 里没有 wallSteps —— 城墙退回手写 rect 了？')
  const end = heroVue.indexOf(']', start)
  return heroVue.slice(start, end).split('\n').filter((l) => l.trim().startsWith('{ x:'))
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
// 城墙：海浪 + 顶线扫光
// ─────────────────────────────────────────────────────────
describe('城墙 · 凸起像海浪一样错峰浮动', () => {
  test('12 段阶梯，x 严格递增', () => {
    const xs = wallStepLines().map((l) => Number(l.match(/x:\s*(\d+)/)[1]))
    assert.equal(xs.length, 12, `阶梯段数是 ${xs.length}，不是 12`)
    for (let i = 1; i < xs.length; i++) {
      assert.ok(xs[i] > xs[i - 1], `x 在第 ${i} 段没有递增：${xs[i - 1]} -> ${xs[i]}`)
    }
  })

  test('每段都有 delay，且按 x 递增（错峰才连成波，不是各自乱浮）', () => {
    const delays = wallStepLines().map((l) => Number(l.match(/delay:\s*([\d.]+)/)[1]))
    assert.equal(delays.length, 12)
    for (let i = 1; i < delays.length; i++) {
      assert.ok(delays[i] > delays[i - 1],
        `第 ${i} 段的 delay 没有比前一段大：${delays[i - 1]} -> ${delays[i]} —— 相位不递增就没有波`)
    }
    assert.equal(delays[0], 0, '第一段没有从 0 起步，相位整体偏移')
  })

  test('错峰总跨度小于动画周期（否则最后一波会追上第一波，浪会回卷）', () => {
    const delays = wallStepLines().map((l) => Number(l.match(/delay:\s*([\d.]+)/)[1]))
    const period = Number(heroVueRaw.match(/wall-wave\s+([\d.]+)s/)[1])
    const spread = delays[delays.length - 1] - delays[0]

    assert.ok(period > 0, 'wall-wave 没有时长')
    assert.ok(spread < period,
      `错峰跨度 ${spread}s >= 周期 ${period}s —— 第 12 段的相位会追过第 1 段，浪往回走`)
  })

  test('wall-wave 动画是上下位移（海浪是上下，不是左右）', () => {
    const m = heroVueRaw.match(/@keyframes wall-wave\s*\{([\s\S]*?)\n\}/)
    assert.ok(m, '没有 @keyframes wall-wave —— 凸起根本不会浮')
    assert.match(m[1], /translateY\(/, 'wall-wave 没有 translateY，浮动的方向不对')
    assert.doesNotMatch(m[1], /translateX\(/, 'wall-wave 里混进了 translateX —— 会把城墙整体推着走')
  })

  test('wall-step class 真的挂到了每一段（漏挂一段，那段就是块死砖）', () => {
    const groups = heroVue.match(/class="wall-step"/g) || []
    assert.equal(groups.length, 1,
      '模板里 wall-step 出现次数异常（应当靠 v-for 挂到 12 段上）')
    assert.match(heroVue, /<g v-for="s in wallSteps"[\s\S]*?class="wall-step"/,
      'wall-step 不在 v-for 的 <g> 上 —— 波浪只作用在一段上，或者根本没作用')
  })

  test('块和它顶上的彩色线在同一个 <g> 里（分开写线会脱节）', () => {
    const g = heroVue.match(/<g v-for="s in wallSteps"[\s\S]*?<\/g>/)
    assert.ok(g, '找不到城墙的 <g>')
    assert.match(g[0], /v-if="s\.line"/,
      '彩色顶线不在块的 <g> 内 —— 波浪一走位，顶线留在原地')
    assert.match(g[0], /:fill="s\.line"/, '块上找不到彩色顶线')
  })
})

describe('城墙 · 顶线从左到右扫光', () => {
  test('三条顶线，各自有颜色', () => {
    const lines = wallStepLines()
      .map((l) => l.match(/line:\s*'(#[0-9A-Fa-f]{6})'/))
      .filter(Boolean)
      .map((m) => m[1].toLowerCase())
    assert.equal(lines.length, 3, `带顶线的段数是 ${lines.length}，不是 3`)
    assert.equal(new Set(lines).size, 3, `三条顶线颜色有重复：${lines.join(' ')}`)
  })

  test('三条线错开扫（同时亮 = 一起闪，不是「从左到右」）', () => {
    const shines = wallStepLines()
      .map((l) => l.match(/line: '#[0-9A-Fa-f]{6}',\s*shineDelay:\s*([\d.]+)/))
      .filter(Boolean)
      .map((m) => Number(m[1]))
    assert.equal(shines.length, 3, '不是每条顶线都有 shineDelay')
    assert.equal(new Set(shines).size, 3,
      `三条线扫光延迟相同：${shines.join(',')} —— 会同时亮，看不出往右走`)
  })

  test('wall-shine 动画是横向位移（扫光是从左到右，不是上下）', () => {
    const m = heroVueRaw.match(/@keyframes wall-shine\s*\{([\s\S]*?)\n\}/)
    assert.ok(m, '没有 @keyframes wall-shine —— 扫光根本没定义')
    assert.match(m[1], /translateX\(/, 'wall-shine 没有 translateX，扫光方向不对')
  })

  test('扫光跑满一整条线的宽度（位移量 = 线宽 120）', () => {
    const m = heroVueRaw.match(/@keyframes wall-shine\s*\{[\s\S]*?translateX\((\d+(?:\.\d+)?)px\)/)
    assert.ok(m, 'wall-shine 里找不到终点位移')
    assert.equal(Number(m[1]), 120,
      `扫光位移 ${m[1]}px ≠ 线宽 120 —— 要么扫不到头，要么扫两遍`)
  })

  test('渐变带和引用它的 rect 绑在同一个 id 上（对不上 = 静默失效）', () => {
    // 不硬编码 'wall-shine' 前缀：那样只要前缀一变，正则就整个失配，
    // 「两者相等」这个真正要守的判断反而永远轮不到触发。
    const idFull = heroVue.match(/:id="(`[^`]*`)"/)
    const urlRef = heroVue.match(/url\(#([^)]*)\)/)
    assert.ok(idFull, '渐变没有用模板生成动态 id')
    assert.ok(urlRef, '扫光 rect 没有 url(#…) 引用')

    assert.equal(idFull[1].slice(1, -1), urlRef[1],
      `渐变 id 是 ${idFull[1]}、rect 引用的是 #${urlRef[1]} —— 对不上时扫光层渲染成空，**且不报任何错**`)
  })

  test('带顶线的段 x 互不相同（x 相同 = 两条线共用一个渐变 id）', () => {
    const withLine = wallStepLines()
      .filter((l) => /line:\s*'#/.test(l))
      .map((l) => Number(l.match(/x:\s*(\d+)/)[1]))
    assert.equal(new Set(withLine).size, withLine.length,
      `带顶线的段 x 有重复：${withLine.join(',')} —— 渐变 id 会撞车`)
  })

  test('波浪与扫光同周期（不同步 = 看着很乱）', () => {
    const wave = heroVueRaw.match(/wall-wave\s+([\d.]+)s/)[1]
    const shine = heroVueRaw.match(/wall-shine\s+([\d.]+)s/)[1]
    assert.equal(wave, shine,
      `波浪 ${wave}s、扫光 ${shine}s 周期不同 —— 两个动画会周期性错拍，看着像卡带`)
  })
})
