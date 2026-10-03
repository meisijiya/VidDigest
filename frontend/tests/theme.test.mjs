/**
 * 亮暗双主题机制的回归测试。
 *
 * 为什么要单独一个文件：把 240°–300° 色相从 @theme 里彻底删掉，是本次改动的
 * 全部意义所在（ADR 0008）。但「删掉」是**一次性动作**——没有任何测试守着的话，
 * 下一个人加一条 `text-purple-500` 就把它悄悄加回来了，而所有既有用例仍然全绿。
 * 绿色不等于在守，所以这里补结构性断言。
 *
 * 测法沿用本仓约定：源码接线断言（渲染验证需要挂载环境，
 * 超出「node --test 零额外依赖」的约定）。
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, basename } from 'node:path'
import { fileURLToPath } from 'node:url'

function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

/** 递归列出 frontend/src 下所有 .vue，用于扫「主色按钮写死白字」这类跨文件问题 */
const SRC = fileURLToPath(new URL('../src/', import.meta.url))
function vueFiles(dir = SRC) {
  const out = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) out.push(...vueFiles(full))
    else if (name.endsWith('.vue')) out.push(full)
  }
  return out
}

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((l) => l.replace(/\/\/.*$/, ''))
    .join('\n')
}

const css = read('../src/style.css')
const indexHtml = read('../index.html')
const headerVue = stripComments(read('../src/components/AppHeader.vue'))
const useTheme = read('../src/composables/useTheme.js')

/** @theme 块本体（不含明亮覆盖） */
const themeBlock = css.slice(css.indexOf('@theme {'), css.indexOf('\n}', css.indexOf('@theme {')))

/**
 * 明亮主题覆盖块。
 *
 * 用「行首的选择器」定位，不能用 indexOf('[data-theme="light"]')：
 * 文件顶部的说明注释里**提到过这个选择器**，indexOf 会命中注释里的那一次，
 * 于是 lightBlock 从注释开始往右切，把 @theme 里的暗色值也圈了进去 ——
 * 表现出来就是「明亮主题的 primary 和暗色一模一样」，CSS 却是好的。
 */
const lightStart = (() => {
  const m = /^\[data-theme="light"\] \{/m.exec(css)
  assert.ok(m, 'style.css 里找不到 [data-theme="light"] 块 —— 明亮主题没有落地')
  return m.index
})()
const lightBlock = css.slice(lightStart)

/** hex -> HSL 色相（度）。近中性色返回 null。
 *  必须设阈值：#fcfcfd 三通道只差 1/255，算出来的色相是 240° ——
 *  一个纯白被判定成「蓝紫」，然后测试报出一个完全无辜的令牌。 */
function hueOf(hex) {
  const r = parseInt(hex.slice(1, 3), 16) / 255
  const g = parseInt(hex.slice(3, 5), 16) / 255
  const b = parseInt(hex.slice(5, 7), 16) / 255
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn
  if (d < 0.02) return null          // 近中性：色相无意义
  let h
  if (mx === r) h = 60 * (((g - b) / d) % 6)
  else if (mx === g) h = 60 * ((b - r) / d + 2)
  else h = 60 * ((r - g) / d + 4)
  return h < 0 ? h + 360 : h
}

const inBlueViolet = (h) => h !== null && h >= 240 && h < 300

describe('配色结构 · 蓝紫带被彻底移除', () => {
  test('@theme 里没有任何色值落在 240°–300°（这就是本次改动要达成的事）', () => {
    const offenders = []
    for (const m of themeBlock.matchAll(/(--color-[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)) {
      const h = hueOf(m[2])
      if (inBlueViolet(h)) offenders.push(`${m[1]} ${m[2]} (H ${Math.round(h)}°)`)
    }
    assert.equal(offenders.length, 0,
      `这些令牌落在蓝紫带：\n    ${offenders.join('\n    ')}\n`
      + '  —— 旧设计把整条 blue 色阶重映射成了紫罗兰，全站因此没有一寸真蓝。'
      + '把它加回来等于把这次改动整个撤销。')
  })

  test('blue 色阶归位为真正的蓝（名字不再说谎）', () => {
    // # 必须落在捕获组**内**：写成 `\s*#([0-9a-fA-F]{6})` 时 m[2] 拿到的是
    // 不带 # 的 "09171e"，而 hueOf 按 slice(1,3) 取通道 —— 于是先丢掉 "09"、
    // 再把 "91" 当红通道，#09171e 这个深蓝被算成 H 45° 的红。
    const steps = [...themeBlock.matchAll(/--color-blue-(\d{2,3}):\s*(#[0-9a-fA-F]{6})/g)]
    assert.ok(steps.length >= 8, `blue 只定义了 ${steps.length} 级，主色色阶不完整`)
    for (const [, step, hex] of steps) {
      const h = hueOf(hex)
      if (h === null) continue
      // 蓝/天蓝/钢蓝都在这个范围；紫（>300）或靛（240 附近）都不算
      assert.ok(h >= 185 && h < 240,
        `blue-${step} 是 ${hex}（H ${Math.round(h)}°），不落在蓝区 185–240 —— `
        + 'blue-* 被重映射是这次事故的根因，不能悄悄回来')
    }
  })

  test('已废止的色族名不再出现（violet / purple / coral / indigo）', () => {
    for (const name of ['violet', 'purple', 'coral', 'indigo']) {
      assert.doesNotMatch(themeBlock, new RegExp(`--color-${name}[-:]`),
        `@theme 里又出现了 --color-${name} —— 这个色族属于 240°–300° 区间，已被删除`)
    }
  })
})

describe('双主题机制 · 同一批令牌，两套值', () => {
  test('明亮主题覆盖的是同名变量（不是另起一套类名）', () => {
    const darkTokens = new Set(
      [...themeBlock.matchAll(/(--color-[a-z0-9-]+):/g)].map((m) => m[1]))
    const lightTokens = new Set(
      [...lightBlock.matchAll(/(--color-[a-z0-9-]+):/g)].map((m) => m[1]))

    // 挑结构色 + 三个角色色做代表，不是全量比对
    for (const key of ['--color-ink', '--color-panel', '--color-line',
                       '--color-primary', '--color-accent', '--color-info']) {
      assert.ok(darkTokens.has(key), `@theme 里没有 ${key}`)
      assert.ok(lightTokens.has(key),
        `明亮主题没有覆盖 ${key} —— 该色在亮色下会保持暗色值`)
    }
  })

  test('主色 / 强调色在明亮主题下确实换了值（不是复制了一份）', () => {
    for (const key of ['--color-primary', '--color-accent', '--color-ink']) {
      const dark = themeBlock.match(new RegExp(`${key}:\\s*(#[0-9a-fA-F]{6})`))?.[1]
      const light = lightBlock.match(new RegExp(`${key}:\\s*(#[0-9a-fA-F]{6})`))?.[1]
      assert.ok(dark && light, `${key} 在某一套主题里没有值`)
      assert.notEqual(dark, light, `${key} 两套主题是同一个值 ${dark} —— 明亮主题没生效`)
    }
  })

  test('明亮主题同样避开蓝紫带（不能只清洗暗色那一套）', () => {
    const offenders = []
    for (const m of lightBlock.matchAll(/(--color-[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)) {
      const h = hueOf(m[2])
      if (inBlueViolet(h)) offenders.push(`${m[1]} ${m[2]}`)
    }
    assert.equal(offenders.length, 0,
      `明亮主题里这些令牌落在蓝紫带：${offenders.join(' ')}`)
  })

  test('表面层（网格线/滚动条/选区/骨架屏）也走变量，两套主题都覆盖', () => {
    for (const v of ['--c-bg', '--c-grid-line', '--c-scroll', '--c-sel-bg', '--c-skel-a']) {
      assert.match(css, new RegExp(`${v}:`), `:root 里没有 ${v}`)
      // 这类「氛围」色不在 @theme 里，Tailwind 不会带上，只能在第二个
      // [data-theme="light"] 块里手动补
      const surfaceLight = css.slice(css.indexOf('[data-theme="light"]', lightStart))
      assert.ok(surfaceLight.includes(`${v}:`),
        `明亮主题没有覆盖 ${v} —— 氛围色不会被 @theme 带上，不手动补就会保持暗色值`)
    }
  })
})

describe('主题切换 · 首帧之前就定下来', () => {
  test('index.html 内联脚本在 <body> 之前写 data-theme（防首帧白闪）', () => {
    const scriptAt = indexHtml.indexOf("setAttribute('data-theme'")
    const bodyAt = indexHtml.indexOf('<body')
    assert.ok(scriptAt > 0, 'index.html 里没有写 data-theme 的内联脚本')
    assert.ok(scriptAt < bodyAt,
      '写 data-theme 的脚本在 <body> 之后 —— 首帧会先用默认色渲染，暗色用户看到一帧白底')
  })

  test('内联脚本读 localStorage 而不是硬编码单一主题', () => {
    const scriptAt = indexHtml.indexOf("setAttribute('data-theme'")
    const head = indexHtml.slice(0, scriptAt)
    assert.match(head, /localStorage\.getItem\('viddigest-theme'\)/,
      '内联脚本没有读 localStorage —— 用户切了主题，刷新就被打回原样')
  })

  test('切换器在页头，且放在登录态判断之外（游客也能切）', () => {
    assert.match(headerVue, /@click="toggleTheme"/, '页头没有主题切换按钮')
    assert.match(headerVue, /aria-pressed/, '切换按钮没有 aria-pressed，屏幕阅读器读不出当前主题')
    const toggleAt = headerVue.indexOf('@click="toggleTheme"')
    const loginBranch = headerVue.indexOf('v-if="user"')
    assert.ok(loginBranch === -1 || toggleAt < loginBranch,
      '切换按钮落在 v-if="user" 里面 —— 游客看不到，也就没有明亮主题可用')
  })

  test('useTheme 只读 DOM，不自己重算一遍主题判定', () => {
    assert.match(useTheme, /getAttribute\('data-theme'\)/,
      'useTheme 没有从 DOM 读主题')
    assert.doesNotMatch(useTheme, /matchMedia|prefers-color-scheme/,
      'useTheme 自己又判了一次 prefers-color-scheme —— '
      + '判定逻辑有两处，迟早打架，且表现为「刷新后主题跳回去」，不报错')
  })

  test('useTheme 同时改 data-theme 与 meta theme-color', () => {
    assert.match(useTheme, /meta\[name="theme-color"\]/,
      '没同步 meta theme-color —— 移动端地址栏配色会与页面明显不符')
  })
})

describe('主色按钮 · 文字色必须够对比度', () => {
  /** WCAG 相对亮度与对比度，实测口径与浏览器一致 */
  const lum = (hex) => {
    const c = [1, 3, 5].map((i) => {
      const v = parseInt(hex.slice(i, i + 2), 16) / 255
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
    })
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
  }
  const ratio = (a, b) => {
    const L1 = lum(a), L2 = lum(b)
    return (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05)
  }
  const val = (block, key) => block.match(new RegExp(`${key}:\\s*(#[0-9a-fA-F]{6})`))?.[1]

  const primaryOf = (block) => val(block, '--color-primary')
  const onOf = (block) => val(block, '--color-on-primary')

  test('两套主题的 on-primary 都过了 AA 正文门槛 4.5', () => {
    for (const [name, block] of [['暗色', themeBlock], ['明亮', lightBlock]]) {
      const bg = primaryOf(block), fg = onOf(block)
      assert.ok(bg && fg, `${name}主题缺 --color-primary 或 --color-on-primary`)
      const r = ratio(fg, bg)
      assert.ok(r >= 4.5,
        `${name}主题主色 ${bg} 上的文字 ${fg} 只有 ${r.toFixed(2)}:1，低于 4.5 —— `
        + '这不是"稍微偏浅"，是实打实读不清的按钮')
    }
  })

  test('on-primary 两套主题取值不同（恒为白是错的）', () => {
    // 白字压 #2fa1da 实测 2.90:1；恒为白会让所有主按钮读不清
    assert.notEqual(onOf(themeBlock), onOf(lightBlock),
      '两套主题的 on-primary 相同 —— 有一侧必然不达标')
  })

  test('主色按钮不再写死 text-white', () => {
    const offenders = []
    for (const f of vueFiles()) {
      const t = readFileSync(f, 'utf8')
      for (const m of t.matchAll(/class="([^"]*)"/g)) {
        const c = m[1]
        if (/\bbg-blue\b/.test(c) && /text-white/.test(c)) {
          offenders.push(`${basename(f)}: ${c.slice(0, 70)}`)
        }
      }
    }
    assert.equal(offenders.length, 0,
      `这些主色按钮还写死白字：\n    ${offenders.join('\n    ')}`)
  })
})

/**
 * 品牌标记豁免。
 *
 * 背景：全站配色换成石墨+湖蓝之后，有人（agent）把 PixelLogo 的 6 个方块
 * 也改成了蓝橙青。用户看过后要求「图标改回这个配色，其他内容按新的来」。
 *
 * 为什么这件事必须有测试守着，而不是靠注释：
 *  - Logo 的**色块**是品牌标记，不是界面装饰。它在浏览器标签、书签、PWA 图标上
 *    出现的地方**不读 CSS 变量、也不跟随 data-theme**。如果方块跟着主题变，
 *    明亮主题下页内是蓝橙方块、标签页上还是紫粉青 —— 两处对不上。
 *  - Logo 的**底板与描边是装饰**，所以跟随主题。2026-10-03 用户明确要求
 *    「可以跟随暗/明亮模式的变化而改变底部颜色」：一枚深蓝方块浮在白底上，
 *    看起来像没做完。favicon 的底板则必须深色 —— 它待在浏览器 chrome 里，
 *    那里的底本来就该深，与页面主题无关。
 *  - 所以刻意是「色块两边一致、底板两边不要求一致」。
 *  - 「顺手把 Logo 也改成新配色」这个动作已经发生过一次。它当时让全部
 *    用例保持绿色（没有任何断言检查 Logo 的具体颜色），所以静默通过。
 *  - 这类豁免的失效方式永远是「有人觉得它不一致」，而不是「报错」。
 */
describe('品牌标记 · 色块豁免主题，底板跟随主题', () => {
  const logo = read('../src/components/PixelLogo.vue')
  const logoMarkup = stripComments(logo)
  const favicon = read('../public/favicon.svg')
  const browserconfig = read('../public/browserconfig.xml')

  /** 品牌原色：紫 → 紫 → 粉 → 紫 → 青 → 青 */
  const BRAND_BLOCKS = ['#7C3AED', '#A855F7', '#EC4899', '#A855F7', '#06B6D4', '#06B6D4']
  // favicon 底板当前是 #161F36，但**刻意不写死这个值**：不变式是「够深的字面量」，
  // 换一个同样深的品牌蓝不该要改测试。判据见下面那条 favicon 底板用例。

  const lum = (hex) => {
    const c = [1, 3, 5].map((i) => {
      const v = parseInt(hex.slice(i, i + 2), 16) / 255
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
    })
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
  }

  test('6 个方块是写死的字面量，不引用任何主题变量', () => {
    const rects = [...logoMarkup.matchAll(/<rect[^>]*class="animate-pixel-blink[^"]*"[^>]*>/g)].map((m) => m[0])
    assert.equal(rects.length, 6, `只找到 ${rects.length} 个带闪动 class 的方块 rect`)
    const offenders = rects.filter((r) => /var\(--color-/.test(r))
    assert.equal(offenders.length, 0,
      `方块里出现了主题变量：\n    ${offenders.join('\n    ')}\n`
      + '  —— 切到明亮主题时这 6 个方块会变色，而浏览器标签上的 favicon 不会。'
      + '同一枚 logo 两处对不上。底板可以跟随主题，方块不行，见 ADR 0008。')
  })

  test('6 个方块仍是品牌原色（紫/紫/粉/紫/青/青）', () => {
    // 底板写的是 var()，不匹配 fill="#..."，所以这里拿到的正好是 6 个方块
    const blocks = [...logoMarkup.matchAll(/<rect[^>]*\sfill="(#[0-9A-Fa-f]{6})"/g)].map((m) => m[1])
    assert.deepEqual(blocks, BRAND_BLOCKS,
      `Logo 方块变成了 [${blocks.join(' ')}]，与品牌原色 [${BRAND_BLOCKS.join(' ')}] 不符。`
      + '改配色前先读 ADR 0008 的「品牌标记豁免」。')
  })

  test('底板与描边跟随主题（这一条是被要求过的，别"顺手"改回写死）', () => {
    assert.match(logoMarkup, /<rect[^>]*\sfill="var\(--color-panel-2\)"/,
      'Logo 底板没有走主题令牌 —— 明亮主题下会是一枚深蓝方块浮在白底上，'
      + '看起来像没做完。这是 2026-10-03 明确要求的：底板跟随明暗主题。')
    assert.match(logoMarkup, /<rect[^>]*\sstroke="var\(--color-line\)"/,
      'Logo 描边没有走主题令牌 —— 底板与页面同色时就没有轮廓了')
  })

  test('favicon 的 6 个方块与组件逐个一致（底板不要求一致，另有一条单独查）', () => {
    // 比**顺序**而不是比成员。#06B6D4 与 #A855F7 各出现两次，用
    // includes() 逐个查的话，少掉一个方块另一个还在，断言照样绿 ——
    // 变异实测过：删掉末尾那个青块，成员检查完全无感。
    const fills = [...favicon.matchAll(/<rect[^>]*\sfill="(#[0-9A-Fa-f]{6})"/g)].map((m) => m[1])
    // [0] 是底板，底板的判据在下一条，这里只管方块
    assert.deepEqual(fills.slice(1), BRAND_BLOCKS,
      `favicon.svg 的方块序列变成了 [${fills.join(' ')}]，`
      + `与 [${BRAND_BLOCKS.join(' ')}] 不符 —— 页内和标签页上的 logo 对不上。`
      + '注意是两个青块两个紫块，只查"有没有出现过"是查不出少了一个的。')
  })

  test('favicon 底板是深色字面量（它在浏览器 chrome 里，不该跟随页面主题）', () => {
    // 刻意**不**断言「等于某个具体 hex」：那样换成另一个同样深的品牌蓝就要改测试，
    // 而换成浅色时又会被上面那条「不等于某 hex」先命中、根本走不到亮度检查 ——
    // 那会让亮度断言变成死代码（实测：把底板改浅，它一次都没跑）。
    // 真正的不变式是两条：它是字面量（不是 var()），且它够深。
    assert.doesNotMatch(favicon, /var\(--color-/,
      'favicon.svg 引用了 CSS 变量 —— favicon 不读 :root 变量，'
      + '引用它的效果是这块矩形变成黑色或直接不渲染。')
    const fills = [...favicon.matchAll(/<rect[^>]*\sfill="([^"]*)"/g)].map((m) => m[1])
    const plate = fills[0]
    assert.match(plate || '', /^#[0-9A-Fa-f]{6}$/,
      `favicon.svg 底板是 ${plate}，不是 6 位 hex 字面量`)
    assert.ok(lum(plate) < 0.05,
      `favicon 底板 ${plate} 不够深 —— 标签页上 logo 与底色分不开。`
      + 'favicon 待在浏览器 chrome 里，那里的底本来就该深，与页面主题无关。')
  })

  test('Windows 磁贴底色是深色（磁贴在浏览器 chrome 里，同 favicon）', () => {
    const m = /<TileColor>(#[0-9A-Fa-f]{6})<\/TileColor>/.exec(browserconfig)
    assert.ok(m, 'browserconfig.xml 里没有 TileColor')
    assert.ok(lum(m[1]) < 0.08,
      `TileColor ${m[1]} 太浅 —— 磁贴底上那枚深色 logo 会糊成一片。`
      + '磁贴在浏览器 chrome 里，不该跟着页面主题翻。')
  })
})

/**
 * 明亮主题的结构性护栏。
 *
 * 上一版亮色块是这么坏的：它照抄了暗色的**色阶形状**（50..300 = 深色 tint），
 * 于是 `--color-blue-50` 出来是 #071217 —— 页面上每一个浅色徽章、导航激活态、
 * 功能卡图标底都变成了一块近黑。rose / red / yellow 三族干脆漏了整段覆盖，
 * `bg-red-50` 停在 #260b0a。
 *
 * 为什么必须用测试守：那是一次性动作。色值一旦好看就没人再动它，但下一个人
 * 复制粘贴一段色阶、或新增一个色族忘了补亮色覆盖时，**没有任何东西会报错**，
 * 页面只是悄悄变丑。首页当时就没渲染到 red/rose/yellow，所以连肉眼扫描都漏了。
 */
describe('明亮主题 · 色阶按角色分配，不是照抄暗色形状', () => {
  const FAMS = ['blue', 'cyan', 'amber', 'emerald', 'yellow', 'rose', 'red']
  const SURFACE_STEPS = [50, 100, 200]
  const TEXT_STEPS = [300, 400, 600, 700]
  /**
   * blue / amber / cyan 的 -500 是用户拍板的品牌**填充**值（#1f7ead / #b66b16 /
   * #1da581），压同族浅底只有 4.20 / 3.82 / 2.91 —— 它们不是文字色。
   * 其余四族的 -500 是解出来的（对各自 -50 实测 4.6+），当文字色完全合法，
   * 所以要把它们一并纳入对比度检查，而不是一刀切禁掉整个 -500。
   */
  const LITERAL_500_FAMS = ['blue', 'amber', 'cyan']
  const val = (block, key) => block.match(new RegExp(`${key}:\\s*(#[0-9a-fA-F]{6})`))?.[1]
  const lum = (hex) => {
    const c = [1, 3, 5].map((i) => {
      const v = parseInt(hex.slice(i, i + 2), 16) / 255
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
    })
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
  }
  const ratio = (a, b) => {
    const L1 = lum(a), L2 = lum(b)
    return (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05)
  }

  test('@theme 里的每个令牌在明亮主题都有同名覆盖（不只是抽几个代表色）', () => {
    const dark = new Set([...themeBlock.matchAll(/(--color-[a-z0-9-]+):/g)].map((m) => m[1]))
    const light = new Set([...lightBlock.matchAll(/(--color-[a-z0-9-]+):/g)].map((m) => m[1]))
    const orphans = [...dark].filter((k) => !light.has(k))
    assert.equal(orphans.length, 0,
      `这些令牌在明亮主题没有覆盖：\n    ${orphans.join('\n    ')}\n`
      + '  —— 它们会保持暗色值。最早漏的就是 rose / red / yellow 整族：'
      + '首页没渲染到错误提示，肉眼扫描也没发现，直到登录和历史页才看见黑块。')
  })

  test('表面带 50/100/200 在明亮主题是浅色（不许出现近黑 tint）', () => {
    const bad = []
    for (const f of FAMS) {
      for (const s of SURFACE_STEPS) {
        const hex = val(lightBlock, `--color-${f}-${s}`)
        if (!hex) continue
        // 平均通道 < 0xc0 即视为"深色表面"。#071217 的三通道均值是 0x1a。
        const mean = [1, 3, 5].reduce((a, i) => a + parseInt(hex.slice(i, i + 2), 16), 0) / 3
        if (mean < 0xc0) bad.push(`${f}-${s} ${hex}`)
      }
    }
    assert.equal(bad.length, 0,
      `明亮主题的浅色底变成了深色：\n    ${bad.join('\n    ')}\n`
      + '  —— 这些是徽章 / 导航激活态 / 图标底的底色。'
      + '深色底上再压一个浅色文字，就是上一版那个"黑块"故障。')
  })

  test('文字带 300/400/600/700 压在自己的 -50 底上够 4.5:1（-500 见下一条）', () => {
    const bad = []
    for (const f of FAMS) {
      const chip = val(lightBlock, `--color-${f}-50`)
      if (!chip) continue
      for (const s of TEXT_STEPS) {
        const ink = val(lightBlock, `--color-${f}-${s}`)
        if (!ink) continue
        const r = ratio(ink, chip)
        if (r < 4.5) bad.push(`text-${f}-${s} ${ink} on ${f}-50 ${chip} = ${r.toFixed(2)}`)
      }
      // 解出来的那四族，-500 同样是文字色，一并量
      if (!LITERAL_500_FAMS.includes(f)) {
        const ink = val(lightBlock, `--color-${f}-500`)
        if (ink) {
          const r = ratio(ink, chip)
          if (r < 4.5) bad.push(`text-${f}-500 ${ink} on ${f}-50 ${chip} = ${r.toFixed(2)}`)
        }
      }
    }
    assert.equal(bad.length, 0,
      `这些文字压在同族浅底上读不清：\n    ${bad.join('\n    ')}\n`
      + '  —— 浅底这一侧可以再浅一点，但文字这一侧必须够深。'
      + '色阶形状照抄暗色时，两侧会同时落到错误的一边。')
  })

  test('on-solid 两套主题都在，且 App.vue 不再用 text-ink 压实底', () => {
    for (const [name, block] of [['暗色', themeBlock], ['明亮', lightBlock]]) {
      const v = val(block, '--color-on-solid')
      assert.ok(v, `${name}主题没有 --color-on-solid`)
      const r = ratio(v, val(block, '--color-info'))
      assert.ok(r >= 4.5, `${name}主题 on-solid ${v} 压 info 只有 ${r.toFixed(2)}:1`)
    }
    const appVue = stripComments(read('../src/App.vue'))
    assert.doesNotMatch(appVue, /hover:bg-cyan-500[^"]*\btext-ink\b/,
      'hover:bg-cyan-500 仍配 text-ink —— 亮色主题下 ink 是页面底色（近白），'
      + '压在实心青块上只有 2.89:1。应改用 text-on-solid。')
  })

  test('源码里没有把「品牌填充色 -500」当文字色压在同族浅底上', () => {
    const offenders = []
    for (const f of vueFiles()) {
      const t = readFileSync(f, 'utf8')
      for (const m of t.matchAll(/class="([^"]*)"/g)) {
        const c = m[1]
        for (const fam of LITERAL_500_FAMS) {
          // 必须用 new RegExp 把色族插回去，**不能**写 /\bbg-\1-50\b/：
          // 那是另一个正则字面量，自己没有捕获组，\1 指不到前一个模式的组，
          // 于是永远匹配失败、断言恒真。变异实测过：把三处 text-blue-400
          // 改成 text-blue-500，这个断言当时一点反应都没有。
          if (new RegExp(`\\btext-${fam}-500\\b`).test(c)
              && new RegExp(`\\bbg-${fam}-50\\b`).test(c)) {
            offenders.push(`${basename(f)}: ${c.replace(/\s+/g, ' ').slice(0, 80)}`)
          }
        }
      }
    }
    assert.equal(offenders.length, 0,
      `这些地方用品牌填充色 ${LITERAL_500_FAMS.join('/')}-500 当文字色压在同族浅底上：\n`
      + `    ${offenders.join('\n    ')}\n`
      + '  —— 这三个 500 是用户拍板的填充值，压浅底只有 3.8–4.2:1。'
      + 'chip 上的文字请用 -400 或 -600。\n'
      + '  （red / rose / yellow / emerald 的 -500 是解出来的，实测 4.6+，当文字色合法，不在此列。）')
  })
})
