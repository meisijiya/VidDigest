/**
 * 工单 #14 · 管理后台 /admin 的接线与结构约束。
 *
 * 测法沿用本仓约定：读源码做静态断言（frontend 没有 vitest /
 * testing-library / vue-router，渲染验证需要挂载环境）。
 *
 * 两条最容易被糊弄过去的地方，这里刻意做成能变红的：
 *
 *  1. 零色值 + 零新色类（ADR 0008 的令牌体系）。这轮刚把 679 处色类
 *     统一到 style.css 的 @theme，后台若绕过它，两套设计会悄悄漂移，
 *     而漂移**不报错**。所以合法色类是从 @theme 解析出来的，不在
 *     这里手写一份——手写的那份迟早过期，锁的就不是当前设计。
 *  2. 「管理」入口的 v-if。只断言「AppHeader 里有管理两个字」没用：
 *     入口挂上去了但没有 v-if，非管理员一样看得见。
 *
 * ⚠️ 判别法（AGENTS.md）：把「四个页签的数据请求」全部打断，对应
 * 用例必须转红。**别让弱断言排在强断言后面**——先命中的把红挡了，
 * 后面的就成了死代码。所以下面每条判据都是独立的 test()。
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

function read(...parts) {
  return readFileSync(new URL(...parts, import.meta.url), 'utf8').replace(/\r\n/g, '\n')
}

/**
 * 剥注释。
 *
 * `//` **只**整行剥，不做行尾一刀切：App.vue 里有正则字面量
 * （`replace(/\/+$/, '')` 之类），行尾一刀切会把那一行从中间砍断，
 * 而那一行往往正是断言的对象——曾经就有一条 pageFromPath 的断言
 * 这么被吃掉过，症状是「明明写了却永远匹配不上」。
 */
function stripComments(src) {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((l) => (l.trim().startsWith('//') ? '' : l))
    .join('\n')
}

const admin = read('../src/components/AdminPage.vue')
const adminCode = stripComments(admin)
const appVue = stripComments(read('../src/App.vue'))
const headerVue = stripComments(read('../src/components/AppHeader.vue'))
const css = read('../src/style.css')

/**
 * <template> 段。转换点收在一处要靠它来验。
 *
 * 必须用 lastIndexOf：模板里还有 <template v-for> 这种同名标签，
 * 第一个 </template> 落在第一个 v-for 结束处，那样切出来的片段
 * 只到表格中间，后面的断言全都恒真。
 */
const adminTemplate = (() => {
  const end = admin.lastIndexOf('</template>')
  assert.ok(end > 0, 'AdminPage.vue 找不到顶层 </template>')
  const seg = admin.slice(0, end)
  assert.ok(seg.includes('</table>'), '切出来的模板段不完整 —— 后面的表被切掉了')
  return stripComments(seg)
})()

/** @theme 块本体（不含明亮覆盖）。合法色类的唯一来源。 */
const themeBlock = css.slice(css.indexOf('@theme {'), css.indexOf('\n}', css.indexOf('@theme {')))

/** 令牌名（去掉 --color- 前缀）。 */
const TOKENS = new Set([...themeBlock.matchAll(/--color-([a-z0-9-]+):/g)].map((m) => m[1]))

/** 消费颜色的工具类前缀 */
const COLOR_PREFIXES = ['text', 'bg', 'border', 'ring', 'outline', 'fill', 'stroke',
  'from', 'via', 'to', 'divide', 'decoration', 'placeholder', 'shadow', 'accent', 'caret']

/**
 * 每个前缀的「不是颜色」形态。
 *
 * 刻意写死：漏一个就会得到一堆假 FAIL（border-b、text-sm、ring-2 都被
 * 当成色类），而假 FAIL 会诱使人去改本来正确的类——这是本仓栽过的坑
 * （「只量真实存在的用法，别把可能存在的组合也算进去」）。
 */
const NON_COLOR = {
  text: /^(xs|sm|base|lg|xl|\d?xl|left|center|right|justify|start|end|wrap|nowrap|truncate|ellipsis|balance|pretty|clip|uppercase|lowercase|capitalize|underline|overline|line-through|no-underline|transparent|current|inherit|none)$/,
  bg: /^(transparent|current|inherit|none|repeat|no-repeat|repeat-x|repeat-y|cover|contain|auto|center|top|bottom|left|right|fixed|local|scroll|border)$/,
  border: /^([trblxyse](-\d+)?|\d+|solid|dashed|dotted|double|none|hidden|collapse|separate|transparent|current|inherit)$/,
  ring: /^(\d+|inset|none|transparent|current|inherit)$/,
  outline: /^(\d+|none|dashed|dotted|double|hidden|transparent|current|inherit)$/,
  divide: /^([xy](-reverse)?|reverse|transparent|current|inherit|none)$/,
  stroke: /^(\d+|none|transparent|current|inherit)$/,
  fill: /^(none|transparent|current|inherit)$/,
  shadow: /^(sm|md|lg|xl|2xl|inner|none)$/,
  decoration: /^(solid|double|dotted|dashed|wavy|underline|overline|line-through|none|auto)$/,
  from: /^(none|transparent|current|inherit)$/,
  via: /^(none|transparent|current|inherit)$/,
  to: /^(none|transparent|current|inherit)$/,
  placeholder: /^$/,
  accent: /^(auto|none)$/,
  caret: /^(auto|transparent|current|inherit)$/,
}

/**
 * 抽出文件里**真实用到**的类。
 *
 * :class 的第一遍会把整条三元表达式也吞进来，切出来的碎片带着引号
 * （border-cyan-200'）。不清掉就会得到四个假 FAIL，然后人会去改本来
 * 正确的类。收尾的方括号要削，但 text-[10px] 的方括号要留（那是任意
 * 值，交给下面「零色值」那条判据）。
 */
function classTokens(src) {
  const out = []
  // 负向后顾：`:class="` 里也含 `class="`，不加它会把整个 Vue 表达式
  // 当成静态类整段吞进来。
  for (const m of src.matchAll(/(?<![:\w-])class="([^"]*)"/g)) out.push(m[1])
  for (const m of src.matchAll(/:class="([^"]*)"/g)) {
    for (const s of m[1].matchAll(/'([^']*)'/g)) out.push(s[1])
  }
  return out.join(' ').split(/\s+/).filter(Boolean).map((t) => {
    let s = t.replace(/["'`]/g, '')
    // 收尾的 `]` 削掉；以 `[` 开头的是任意值（text-[10px]），要留着
    if (!s.includes('[') && s.endsWith(']')) s = s.slice(0, -1)
    // 数组元素之间的逗号会粘在 token 尾巴上
    return s.replace(/,+$/, '')
  }).filter(Boolean)
}

function colorValueOf(tok) {
  const t = tok.replace(/^[a-z-]+:/, '')                       // 去变体前缀 hover: / focus: / last:
  const i = t.indexOf('-')
  if (i < 0) return null
  const prefix = t.slice(0, i)
  if (!COLOR_PREFIXES.includes(prefix)) return null
  const value = t.slice(i + 1).replace(/\/[\w.]+$/, '')        // 去 /70 这类透明度后缀
  if (!value) return null
  if (/^\[/.test(value)) return null                            // 任意值：交给「零色值」那条
  if (/^\d+$/.test(value)) return null                          // 纯数字：宽度 / 透明度
  if (NON_COLOR[prefix] && NON_COLOR[prefix].test(value)) return null
  return value
}

/** 文件里用到的全部颜色类 → 颜色名（去重） */
function usedColors(src) {
  const names = new Set()
  const classes = new Set()
  for (const tok of classTokens(src)) {
    const v = colorValueOf(tok)
    if (v === null) continue
    names.add(v)
    classes.add(tok)
  }
  return { names, classes }
}

// ─────────────────────────────────────────────────────────
// 视觉：零色值字面量、零新色类
// ─────────────────────────────────────────────────────────
describe('视觉 · 后台长在 @theme 令牌体系里', () => {
  test('AdminPage.vue 里没有任何色值字面量', () => {
    const hits = []
    // hex：3/4/6/8 位都算
    for (const m of adminCode.matchAll(/#[0-9a-fA-F]{3,8}\b/g)) hits.push(m[0])
    for (const m of adminCode.matchAll(/\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color-mix|color)\s*\(/g)) hits.push(m[0])
    assert.deepEqual(hits, [],
      `后台组件里出现了色值字面量：${hits.join(' ')}\n`
      + '  —— 视觉的唯一来源是 src/style.css 的 @theme 令牌。'
      + '在这里写死一个 hex，明亮主题下它不会跟着翻，必然留下深色残留。')
  })

  test('AdminPage.vue 里的每个颜色类都存在于 @theme', () => {
    const { names } = usedColors(adminCode)
    // 只量真实用过的类：这里不小于 20 就说明枚举器本身坏了
    //（类名改了 / 模板被搬走），空集合会让下面这条断言恒真。
    assert.ok(names.size >= 20,
      `只枚举出 ${names.size} 个颜色类 —— 枚举器或模板变了，先修枚举器再谈越界`)
    const offenders = [...names].filter((n) => !TOKENS.has(n)).sort()
    assert.deepEqual(offenders, [],
      `这些颜色类不在 @theme 里：${offenders.join(' ')}\n`
      + `  —— 当前令牌体系有 ${TOKENS.size} 个：${[...TOKENS].sort().join(' ')}\n`
      + '  写一个 @theme 之外的名字，等于在后台另起一套设计；'
      + '两套设计的漂移不会报错，只会在某个主题下看起来不对。')
  })

  test('页头新加的管理入口也没带越界色类', () => {
    const btn = headerVue.match(/<button[^>]*open-admin[^>]*>/)
    assert.ok(btn, '找不到管理入口按钮')
    const { names } = usedColors(btn[0])
    const offenders = [...names].filter((n) => !TOKENS.has(n))
    assert.deepEqual(offenders, [],
      `管理入口用了 @theme 之外的色类：${offenders.join(' ')}`)
  })

  test('SVG 的颜色属性只有 none / currentColor（不许写死填充色）', () => {
    const hits = [...adminCode.matchAll(/\s(?:fill|stroke)="([^"]*)"/g)]
      .map((m) => m[1])
      .filter((v) => v !== 'none' && v !== 'currentColor')
    assert.deepEqual(hits, [],
      `这些 SVG 颜色属性不是 none / currentColor：${hits.join(' ')}\n`
      + '  —— currentColor 会跟着文字色的 class 走，写死值则不跟主题。')
  })

  test('后台没有另起一套：它用的是既有那几个结构类', () => {
    // 卡片 / 边框 / 骨架屏 / 等宽数字——都来自主站已在用的类。
    // 断言它们真的在，是为了让「从既有体系长出来」这件事可复核。
    for (const c of ['bg-panel', 'border-line', 'skeleton', 'font-pixel', 'rounded-2xl']) {
      assert.ok(adminCode.includes(c), `AdminPage.vue 里没有 ${c} —— 后台像是另起了一套组件`)
    }
  })

  test('后台不自己判主题（data-theme / viddigest-theme 都不该出现在这里）', () => {
    // 主题判定有两处权威：index.html 的内联脚本（首帧前）与 useTheme（只读 DOM）。
    // 后台再写第三处，表现形式是「切到后台主题跳回去」，且不报错。
    assert.doesNotMatch(adminCode, /data-theme|viddigest-theme/,
      'AdminPage.vue 里自己动了主题 —— 主题判定只能有 index.html 与 useTheme 两处')
  })
})

// ─────────────────────────────────────────────────────────
// 鉴权入口
// ─────────────────────────────────────────────────────────
describe('入口 · 只对管理员可见（前端隐藏是体验，不是边界）', () => {
  test('「管理」入口在顶部导航 <nav> 里', () => {
    assert.match(headerVue, /<nav[\s\S]*?open-admin[\s\S]*?<\/nav>/,
      '管理入口不在 <nav> 里 —— 它成了页头角落里的一个孤立按钮')
  })

  test('管理入口的按钮上挂着 v-if="isAdmin"', () => {
    const btn = headerVue.match(/<button[^>]*open-admin[^>]*>/)
    assert.ok(btn, 'AppHeader 里没有 emit open-admin 的按钮')
    assert.match(btn[0], /v-if="isAdmin"/,
      '管理入口没有 v-if="isAdmin" —— 非管理员也看得见，'
      + '而「看不见」正是这一条要守的东西（前端隐藏只做体验，边界在后端 403）')
  })

  test('isAdmin 声明成 prop 且默认 false', () => {
    const props = headerVue.match(/defineProps\(\{([\s\S]*?)\n\}\)/)
    assert.ok(props, 'AppHeader 里找不到 defineProps')
    const m = props[1].match(/isAdmin:\s*\{[^}]*default:\s*false/)
    assert.ok(m, 'isAdmin 不是默认 false 的布尔 prop —— 不传时它会是非管理员可见')
  })

  test('open-admin 在 defineEmits 里（漏声明 = 点击静默失效）', () => {
    const m = headerVue.match(/defineEmits\(\[([^\]]*)\]\)/)
    assert.ok(m, '找不到 defineEmits')
    assert.match(m[1], /['"]open-admin['"]/,
      "open-admin 没进 defineEmits —— Vue 只对未声明的事件发警告，"
      + '而点击**看起来正常**，父组件就是收不到')
  })

  test('App.vue 传了 is-admin 并监听了 open-admin', () => {
    assert.match(appVue, /:is-admin="isAdmin"/, 'AppHeader 没收到 isAdmin')
    assert.match(appVue, /@open-admin="openAdmin"/, 'App.vue 没监听 open-admin —— 点了管理页面不动')
  })

  test('openAdmin 真的切页并改 URL', () => {
    const m = appVue.match(/function openAdmin\(\)\s*\{([\s\S]*?)\n\}/)
    assert.ok(m, 'App.vue 里没有 openAdmin')
    assert.match(m[1], /currentPage\.value\s*=\s*'admin'/, 'openAdmin 没有把 currentPage 切到 admin')
    assert.match(m[1], /pushPage\('admin'\)/, 'openAdmin 没有改 URL —— 刷新就掉回首页')
  })

  test('管理员判据有两级：localStorage 渲染 + /api/auth/me 回查', () => {
    // 第一级只决定这一帧显不显示（防闪），第二级才是服务端事实。
    // 少了第二级，撤权要等 72 小时 token 过期才生效（ADR 0010）。
    assert.match(appVue, /isAdmin\s*=\s*ref\([\s\S]{0,120}is_admin/,
      'isAdmin 的初值没有读登录用户 —— 刷新后管理入口要闪一下才出现')
    assert.match(appVue, /async function refreshAdminFlag\(\)/, '没有回查管理员身份')
    assert.match(appVue, /await fetchMe\(\)[\s\S]{0,80}is_admin/,
      'refreshAdminFlag 没有用 /api/auth/me 的 is_admin —— 服务端才是权威')
  })
})

// ─────────────────────────────────────────────────────────
// 轻路由：history.pushState + popstate
// ─────────────────────────────────────────────────────────
/**
 * 把源码里的一个纯函数取出来直接调用。
 *
 * 为什么不对着源码做正则：那类断言锁的是「写法」，而这里要守的是
 * 「行为」。上面 pageFromPath 有一版就是这么写的，结果断言的正则本身
 * 写错（要在正则里匹配另一个正则），红的原因是测试自己坏了。
 *
 * 代价与前提：函数体必须自包含（不引用模块作用域里的东西）。
 * 一旦它开始引用外部变量，new Function 会抛 ReferenceError，
 * 那属于「这条测试不再适用」，得改测试而不是绕过它。
 */
function loadFn(src, name) {
  const m = src.match(new RegExp(`function ${name}\\(([^)]*)\\)\\s*\\{([\\s\\S]*?)\\n\\}`))
  assert.ok(m, `AdminPage.vue / App.vue 里没有纯函数 ${name}`)
  return new Function(...m[1].split(',').map((s) => s.trim()).filter(Boolean), m[2])
}

describe('轻路由 · /admin 进得来也留得住', () => {
  test('HistoryPage 从 v-else 改成了 v-else-if（否则整条链塌掉）', () => {
    assert.match(appVue, /<HistoryPage v-else-if="currentPage === 'history'"/,
      'HistoryPage 还挂在 v-else 上 —— 后面再插一个 v-else-if 分支，'
      + '历史页会被前面抢光，整条条件链塌掉')
  })

  test('admin 分支排在 history 分支之前', () => {
    const adminAt = appVue.indexOf(`currentPage === 'admin'`)
    const historyAt = appVue.indexOf(`currentPage === 'history'`)
    assert.ok(adminAt > 0 && historyAt > 0, '两个分支没都找到')
    assert.ok(adminAt < historyAt,
      'admin 分支排在 history 之后 —— 顺序反了的话历史页永远进不去')
  })

  test('初始页由 location.pathname 决定（刷新停在 /admin）', () => {
    assert.match(appVue, /const currentPage = ref\(pageFromPath\(/,
      'currentPage 不是从 URL 读出来的 —— 刷新 /admin 会掉回首页')
    assert.match(appVue, /function pageFromPath\(/, 'App.vue 里没有 pageFromPath')
  })

  test('pageFromPath：/admin 与 /admin/ 都落 admin，其余落 home', () => {
    const pageFromPath = loadFn(appVue, 'pageFromPath')
    // 尾部斜杠是真实存在的输入（某些部署会在 /admin/ 下发页面）。
    // 不容忍它的话，刷新一次就掉回首页——而且只在那个部署下复现。
    assert.equal(pageFromPath('/admin'), 'admin')
    assert.equal(pageFromPath('/admin/'), 'admin')
    assert.equal(pageFromPath('/'), 'home')
    assert.equal(pageFromPath(''), 'home')
    assert.equal(pageFromPath('/history'), 'home',
      '把 /history 也认成 admin 就反了 —— 那是历史页的活儿')
  })

  test('监听 popstate（浏览器后退能退回主站）', () => {
    assert.match(appVue, /addEventListener\('popstate', syncPageFromUrl\)/,
      "没监听 popstate —— 浏览器后退后 URL 与页面会对不上")
    assert.match(appVue, /function syncPageFromUrl\(\)/, 'App.vue 里没有 syncPageFromUrl')
  })

  test('popstate 在卸载时解绑（否则组件重建后一次后退切多遍）', () => {
    assert.match(appVue, /onUnmounted\(\(\)\s*=>\s*\{[\s\S]*?removeEventListener\('popstate'/,
      'popstate 没有解绑 —— 每次重建都多挂一个监听')
  })
})

// ─────────────────────────────────────────────────────────
// 四个页签
// ─────────────────────────────────────────────────────────
describe('四个页签 · 各自有取数路径', () => {
  test('TABS 恰好四个，顺序即展示顺序', () => {
    const m = adminCode.match(/const TABS = \[([\s\S]*?)\n\]/)
    assert.ok(m, 'AdminPage.vue 里没有 TABS')
    const keys = [...m[1].matchAll(/key:\s*'([a-z]+)'/g)].map((x) => x[1])
    // 比**序列**而不是查成员：四个键互不相同，逐个 includes() 查不出顺序错乱
    assert.deepEqual(keys, ['users', 'quota', 'community', 'models'])
  })

  test('LOADERS 恰好四份，且与页签一一对应', () => {
    const m = adminCode.match(/const LOADERS = \{([\s\S]*?)\n\}/)
    assert.ok(m, 'AdminPage.vue 里没有 LOADERS')
    const keys = [...m[1].matchAll(/^\s*([a-z]+):\s*load[A-Za-z]+,?\s*$/gm)].map((x) => x[1])
    assert.deepEqual(keys, ['users', 'quota', 'community', 'models'],
      'LOADERS 与 TABS 不是一一对应 —— 某个页签永远取不到数')
  })

  /** 逐页签独立成条：四个请求全打断时，四条一起红，不是一条顶四条。 */
  for (const [fn, api, label] of [
    ['loadUsers', 'fetchAdminUsers', '用户'],
    ['loadQuota', 'fetchAdminUsers', '额度'],
    ['loadCommunity', 'fetchAdminCommunity', '社区'],
    ['loadModels', 'fetchAdminModels', 'AI 服务'],
  ]) {
    test(`${label}页签的 ${fn} 真的调 ${api}`, () => {
      const m = adminCode.match(new RegExp(`async function ${fn}\\(\\)[\\s\\S]*?\\n\\}`))
      assert.ok(m, `AdminPage.vue 里没有 ${fn}`)
      assert.ok(m[0].includes(api),
        `${fn} 里没有调 ${api} —— 「${label}」页签的数据出口断了，`
        + '而页签本身还会照常渲染，症状是「点开永远是空的」')
    })
  }

  test('空状态四份文案都在（不是只给某一页写）', () => {
    const m = adminCode.match(/const EMPTY = \{([\s\S]*?)\n\}/)
    assert.ok(m, 'AdminPage.vue 里没有 EMPTY')
    const keys = Object.keys(
      Object.fromEntries([...m[1].matchAll(/^\s*([a-z]+):\s*\{/gm)].map((x) => [x[1], 1])),
    )
    assert.deepEqual(keys, ['users', 'quota', 'community', 'models'],
      '空状态文案没覆盖四个页签 —— 某一页空时会渲染成白屏')
  })

  test('页签是 role="tab" 的真按钮，带 aria-selected', () => {
    assert.match(adminTemplate, /role="tablist"/, '页签条没有 role="tablist"')
    const btn = adminTemplate.match(/<button[^>]*role="tab"[^>]*>/)
    assert.ok(btn, '页签不是 role="tab" 的按钮 —— 屏幕阅读器读不出这是页签')
    assert.match(btn[0], /:aria-selected="/, '页签没有 aria-selected')
    assert.match(btn[0], /:aria-controls="/, '页签没有 aria-controls')
  })

  test('内容区是 role="tabpanel" 且与页签 id 互相指向', () => {
    assert.match(adminTemplate, /role="tabpanel"/, '内容区没有 role="tabpanel"')
    assert.match(adminTemplate, /:id="`admin-panel-\$\{tab\}`"/, 'tabpanel 的 id 没跟着页签走')
    assert.match(adminTemplate, /:aria-labelledby="`admin-tab-\$\{tab\}`"/,
      'tabpanel 没有 aria-labelledby —— 读屏时不知道自己在哪个页签下')
  })

  test('每个页签的请求都写进自己的状态槽（切页不会串台）', () => {
    const m = adminCode.match(/const views = reactive\(\{([\s\S]*?)\n\}\)/)
    assert.ok(m, 'AdminPage.vue 里没有 views')
    const keys = [...m[1].matchAll(/^\s*([a-z]+):\s*blankView\(\),?\s*$/gm)].map((x) => x[1])
    assert.deepEqual(keys, ['users', 'quota', 'community', 'models'],
      'views 不是四份独立状态 —— 在「用户」页加载中会把「社区」页的内容一起换成骨架屏')
  })

  test('「额度」页签把编辑控件常驻行内（不是另建一份额度数据）', () => {
    assert.match(adminTemplate, /v-if="editorOpen\(u\)"/,
      '没有行内编辑行 —— 额度页签会退化成和用户页一模一样、却什么也改不了')
    const m = adminCode.match(/function editorOpen\(([\s\S]*?)\n\}/)
    assert.ok(m, '没有 editorOpen')
    assert.match(m[1], /tab\.value === 'quota'/,
      "editorOpen 不认 quota 页签 —— 到了「额度」页还要一格格点「编辑额度」，"
      + '与工单写的「挂在用户行内编辑」不符')
    assert.match(adminTemplate, /v-if="tab !== 'quota'"/,
      '用户页与额度页没有区分开编辑入口')
  })

  test('用户页签有搜索与分页（契约里的 q / limit / offset）', () => {
    assert.match(adminTemplate, /@submit\.prevent="search"/, '搜索框不触发 search()')
    const input = adminTemplate.match(/<input[^>]*id="admin-user-q"[^>]*>/)
    assert.ok(input, '没有按邮箱搜的输入框')
    assert.match(input[0], /type="search"/, '搜索框不是 type="search"（回车提交与清除按钮都没有）')
    assert.match(adminTemplate, /@click="goPage\(view\.page - 1\)"/, '没有上一页')
    assert.match(adminTemplate, /@click="goPage\(view\.page \+ 1\)"/, '没有下一页')
    assert.match(adminTemplate, /共 \{\{ view\.total \}\} 条/, '分页没显示总条数')
    // 翻页不能落到模型页签上：那张清单是全量的
    assert.match(adminTemplate, /v-if="tab !== 'models' && view\.total > PAGE_SIZE"/,
      '分页块没有排除模型页签')
  })

  test('goPage 不越界（否则最后一页的「下一页」会打出一个越界请求）', () => {
    const m = adminCode.match(/function goPage\(([\s\S]*?)\n\}/)
    assert.ok(m, '没有 goPage')
    assert.match(m[1], /n < 1/, 'goPage 没挡下界')
    assert.match(m[1], /n > totalPages\.value/, 'goPage 没挡上界')
  })
})

// ─────────────────────────────────────────────────────────
// 三态 + 改额度的两条反馈
// ─────────────────────────────────────────────────────────
describe('每个页签的 loading / 空 / 错误三态', () => {
  test('loading 态：骨架屏 + 屏幕阅读器能读到的文字', () => {
    assert.match(adminTemplate, /v-if="view\.loading"/, '没有 loading 分支')
    assert.match(adminTemplate, /class="[^"]*skeleton/, 'loading 分支没有骨架屏')
    assert.match(adminTemplate, /正在加载\{\{ tabLabel \}\}/,
      'loading 分支没有可读文案 —— 只有一块会动的灰，屏幕阅读器读不到任何东西')
  })

  test('空状态分支的条件是「列表真的空了」', () => {
    // 声明在「顺序」那条**前面**：两条都在守空状态，但条件这条更具体。
    // 顺序颠倒的话，把条件改坏会先被后面那条的「分支还在不在」挡住，
    // 指定来守条件的那条断言就成了死代码（变异实测过）。
    assert.match(adminTemplate, /v-else-if="!view\.items\.length"/,
      '空状态的判据不是 items 为空 —— 列表空着时会直接渲染出一张空表，'
      + '而不是告诉管理员「没有数据」')
  })

  test('错误态在空状态**之前**（否则一次 500 被渲染成「没有数据」）', () => {
    const errAt = adminTemplate.indexOf('v-else-if="view.error"')
    const emptyAt = adminTemplate.indexOf('v-else-if="!view.items.length"')
    assert.ok(errAt > 0, '没有错误分支')
    assert.ok(emptyAt > 0, '没有空状态分支')
    assert.ok(errAt < emptyAt,
      '错误分支排在空状态之后 —— 请求挂了会被渲染成「没有数据」，'
      + '用户会以为该换个关键词再搜，真正的原因被吞掉')
  })

  test('错误态有 role="alert" 与重试入口', () => {
    const m = adminTemplate.match(/v-else-if="view\.error"[\s\S]*?<\/div>/)
    assert.ok(m, '取不到错误分支')
    assert.match(m[0], /role="alert"/, '错误分支没有 role="alert" —— 读屏不播报')
    assert.match(m[0], /@click="reload"/, '错误分支没有重试按钮')
  })

  test('空状态取自 EMPTY（跟着页签换文案，不是写死一句）', () => {
    assert.match(adminTemplate, /\{\{ empty\.text \}\}/, '空状态没有用 empty.text')
    assert.match(adminTemplate, /\{\{ empty\.hint \}\}/, '空状态没有提示行')
  })

  test('403 是整页可读错误态，不是白屏', () => {
    assert.match(adminTemplate, /v-if="view\.forbidden"/,
      '没有 403 分支 —— 非管理员手动访问 /admin 会看到一片空白')
    const m = adminTemplate.match(/v-if="view\.forbidden"[\s\S]*?<\/button>/)
    assert.ok(m, '取不到 403 分支的正文')
    assert.match(m[0], /没有管理员权限/, '403 分支没有说明「没有管理员权限」')
    assert.match(m[0], /emit\('back'\)/, '403 分支没有离开的出口')
  })

  test('markFailure 真的按 403 分流（而不是把拒绝当成「列表为空」）', () => {
    const m = adminCode.match(/function markFailure\(([\s\S]*?)\n\}/)
    assert.ok(m, 'AdminPage.vue 里没有 markFailure')
    assert.match(m[1], /status\s*===\s*403/, 'markFailure 没有判 403')
    assert.match(m[1], /v\.items\s*=\s*\[\]/, 'markFailure 没有清空 items —— 旧数据会留在错误态里')
  })
})

describe('改额度 · 成功与失败都要有反馈', () => {
  test('成功反馈是一条看得见的文案', () => {
    assert.match(adminCode, /feedbackOf\(u\.id\)\.kind\s*=\s*'ok'/,
      'saveQuota 里没有成功分支的反馈 —— 改完没有任何确认')
    assert.match(adminCode, /text = '额度已更新'/, '成功反馈没有文案')
  })

  test('失败反馈是另一条，不与成功共用一句话', () => {
    const fn = adminCode.match(/async function saveQuota\([\s\S]*?\n\}/)
    assert.ok(fn, '取不到 saveQuota')
    const m = fn[0].match(/catch \(err\) \{([\s\S]*?)\n  \}/)
    assert.ok(m, 'saveQuota 里没有 catch —— 请求失败会一路冒到组件边界，管理员什么都看不到')
    assert.match(m[1], /kind\s*=\s*'error'/, 'catch 里没有标成 error')
    assert.match(m[1], /messageOf\(err\)/,
      'catch 里没带服务端原因 —— 管理员只看到「失败」，无从判断是自己填错还是服务端挂了')
  })

  test('不做乐观更新：本地数字只被服务端回读的那份替换', () => {
    const m = adminCode.match(/async function saveQuota\([\s\S]*?\n\}/)
    assert.ok(m, '取不到 saveQuota')
    const body = m[0]
    const replaceAt = body.indexOf('replaceUser(')
    const okAt = body.indexOf('kind = \'ok\'')
    assert.ok(replaceAt > 0 && okAt > 0, 'saveQuota 里既没有 replaceUser 也没有成功分支')
    assert.ok(replaceAt < okAt, '成功反馈早于 replaceUser —— 顺序反了就会出现「先报成功再改数」')
    // 乐观更新的形态是「发请求之前就写本地行」
    const beforeReq = body.slice(0, body.indexOf('await setUserQuota'))
    assert.doesNotMatch(beforeReq, /replaceUser\(/,
      'replaceUser 出现在发请求之前 —— 那是乐观更新，失败时会留下假数字')
  })

  test('vip_not_effective 必须渲染出来（不提示管理员就会以为是自己操作错了）', () => {
    assert.match(adminCode, /res\.note === 'vip_not_effective'/,
      "没有判 note === 'vip_not_effective'")
    assert.match(adminCode, /feedbackOf\(u\.id\)\.text = res\.message/,
      'VIP 提示没有把服务端 message 显示出来')
    assert.match(adminTemplate, /feedbackOf\(u\.id\)\.text/,
      '反馈文案没有进模板 —— 后端说了什么前端都没显示')
  })

  test('成功后不收起编辑行（否则成功提示跟着一起消失）', () => {
    const fn = adminCode.match(/async function saveQuota\([\s\S]*?\n\}/)
    assert.ok(fn, '取不到 saveQuota')
    const body = fn[0]
    const okAt = body.indexOf("kind = 'ok'")
    const catchAt = body.indexOf('catch (err)')
    assert.ok(okAt > 0 && catchAt > okAt, 'saveQuota 里找不到成功分支')
    // 反馈写在这行里，收起编辑行等于把「改成功了」一起收走
    assert.doesNotMatch(body.slice(okAt, catchAt), /closeEditor\(/,
      "成功分支里调了 closeEditor —— 编辑行一收，「额度已更新」也跟着没了，"
      + '管理员会以为自己没点上')
  })

  test('反馈区是 aria-live 的礼貌播报区', () => {
    // 必须锁定**改额度反馈那个元素**。全文件搜 aria-live 是不够的：
    // loading 骨架屏那块也有 aria-live，变异实测过——把反馈区的
    // role="status" 删掉，整份测试仍然全绿。
    const fb = adminTemplate.match(/<p[^>]*v-if="feedbackOf\(u\.id\)\.text"[^>]*>/)
    assert.ok(fb, '模板里找不到改额度的反馈元素')
    assert.match(fb[0], /aria-live="polite"/, '反馈区没有 aria-live，读屏不播报')
    assert.match(fb[0], /role="status"/, '反馈区没有 role="status"')
  })

  test('值域在发请求之前先判（-1 无限 / 0 停用 / 空=清除覆盖）', () => {
    const toQuotaValue = loadFn(adminCode, 'toQuotaValue')
    // 契约：null = 清除 override 回落全局；0 = 一条都不能用；-1 = 无限
    assert.deepEqual(toQuotaValue(''), { ok: true, value: null }, '空值应当是「清除覆盖」')
    assert.deepEqual(toQuotaValue('   '), { ok: true, value: null }, '全空格应当也是「清除覆盖」')
    assert.deepEqual(toQuotaValue('0'), { ok: true, value: 0 }, '0 是一条都不能用，必须原样发过去')
    assert.deepEqual(toQuotaValue('-1'), { ok: true, value: -1 }, '-1 是无限，必须原样发过去')
    assert.deepEqual(toQuotaValue('5'), { ok: true, value: 5 })
    assert.equal(toQuotaValue('-2').ok, false, '-2 不是契约里的取值，必须在发请求前就拦下')
    assert.equal(toQuotaValue('1.5').ok, false, '额度必须是整数')
    assert.equal(toQuotaValue('abc').ok, false, '非数字必须在本地就拦下')
    assert.match(adminTemplate, /min="-1"/, '输入框没有 min="-1" —— 用户能敲出契约外的值')
  })
})

// ─────────────────────────────────────────────────────────
// 可达性与契约转换
// ─────────────────────────────────────────────────────────
describe('键盘可达与 aria', () => {
  test('额度输入框每个都有 label（for 与 id 对得上）', () => {
    const inputs = [...adminTemplate.matchAll(/<input[^>]*>/g)].map((m) => m[0])
    assert.ok(inputs.length >= 3, `只找到 ${inputs.length} 个 input —— 搜索框与两个额度框都得在`)
    for (const inp of inputs) {
      assert.ok(/(^|\s):?id="/.test(inp), `input 没有 id：${inp.slice(0, 60)}`)
    }
    const fors = [...adminTemplate.matchAll(/:for="`quota-[a-z]+-\$\{u\.id\}`"/g)]
    assert.equal(fors.length, 2,
      `找到 ${fors.length} 个额度输入框的 label —— 解析与追问两个上限都要能定位`)
  })

  test('表头是 th + scope="col"', () => {
    // 必须要求 <th 后面跟空白：写成 /<th[^>]*>/ 会把 <thead> 也算进来
    const ths = [...adminTemplate.matchAll(/<th\s[^>]*>/g)].map((m) => m[0])
    assert.ok(ths.length >= 2, `只找到 ${ths.length} 个 th —— 表头不见了`)
    for (const th of ths) assert.match(th, /scope="col"/, `表头缺 scope="col"：${th}`)
  })

  test('表格有 caption（读屏用户知道这张表是什么）', () => {
    const n = [...adminTemplate.matchAll(/<caption/g)].length
    assert.ok(n >= 2, `只有 ${n} 个 caption —— 两张表都要有`)
  })

  test('页签除了 Tab 键还能用方向键（不必依赖鼠标）', () => {
    assert.match(adminTemplate, /@keydown="onTabKeydown\(t\.key, \$event\)"/, '页签没有 keydown 处理')
    const m = adminCode.match(/function onTabKeydown\(([\s\S]*?)\n\}/)
    assert.ok(m, '没有 onTabKeydown')
    assert.match(m[1], /ArrowRight/, '方向键只处理了一半')
    assert.match(m[1], /\.focus\(\)/, '切页签后没有把焦点移到新页签上')
  })

  test('表格里的操作按钮都显式写了 type（不会误触发表单提交）', () => {
    // 搜索框那枚是 type="submit"（它本来就在 <form> 里，是有意的），
    // 其余必须是 type="button" —— 不写 type 的 button 放在 form 里会提交。
    const buttons = [...adminTemplate.matchAll(/<button[^>]*>/g)].map((m) => m[0])
    assert.ok(buttons.length >= 6, `只找到 ${buttons.length} 个 button —— 解析器可能失灵`)
    for (const b of buttons) {
      assert.ok(/type="(?:button|submit)"/.test(b), `按钮缺显式 type：${b.slice(0, 70)}`)
    }
  })
})

describe('后端契约 · snake_case 转换收在一处', () => {
  test('从 ../api/admin.js 导入五个函数（不建这个文件，读它也不做断言）', () => {
    // [^}]* 不是 [\s\S]*? —— 后者会从文件里第一个 import {（vue 那行）起吞到
    // 这里，名单里混进 'onMounted } from \'vue\'...' 这种垃圾。
    // [^}]* 天然锚定「最后一个 } 之前」的那条 import，同时照样能跨行。
    const m = adminCode.match(/import \{([^}]*)\} from '\.\.\/api\/admin\.js'/)
    assert.ok(m, "没有从 '../api/admin.js' 导入")
    const names = m[1].split(',').map((s) => s.trim()).filter(Boolean)
    // 契约从五个长到八个：createAdminUser / setUserAdmin / deleteAdminUser
    // 是 ADR 0012 账号生命周期的前端出口。**这份名单是冻结的**——
    // 少一个说明有路径绕过了 api 层，多一个说明有新的未审接口混进来了。
    assert.deepEqual(names.sort(),
      ['createAdminUser', 'deleteAdminUser', 'fetchAdminCommunity',
       'fetchAdminModels', 'fetchAdminUsers', 'setUserAdmin',
       'setUserQuota', 'updateAdminModel'],
      '导入的 API 名字与冻结的契约不一致')
  })

  test('四个转换函数都在（用户 / 社区 / 模型各自的形状）', () => {
    for (const fn of ['toUser', 'toCommunityItem', 'toModel']) {
      assert.ok(adminCode.includes(`const ${fn} =`), `没有 ${fn} —— 转换点散出去了`)
    }
  })

  test('模板里不出现 snake_case 取值（转换必须已经发生）', () => {
    const hits = [...adminTemplate.matchAll(/[A-Za-z_$][\w$]*\.[a-z]+_[a-z]+/g)].map((m) => m[0])
    assert.deepEqual(hits, [],
      `模板里直接读了 snake_case 字段：${hits.join(' ')}\n`
      + '  —— 契约是 snake_case，camelCase 由 toUser/toCommunityItem/toModel 一次性转好。'
      + '模板里混着两种命名，后端一改字段，报错会散落在整份模板里。')
  })

  test('四个请求的调用形状与冻结契约一致', () => {
    assert.match(adminCode, /fetchAdminUsers\(\{ page: v\.page, pageSize: PAGE_SIZE, q: query\.value\.trim\(\) \}\)/,
      '用户列表的调用形状与契约不符（page / pageSize / q）')
    assert.match(adminCode, /fetchAdminCommunity\(\{ page: v\.page, pageSize: PAGE_SIZE \}\)/,
      '社区列表的调用形状与契约不符')
    assert.match(adminCode, /await fetchAdminModels\(\)/, '模型清单应当无参数调用')
    assert.match(adminCode, /setUserQuota\(u\.id, \{ parseLimit: parseLimit\.value, chatLimit: chatLimit\.value \}\)/,
      '改额度的调用形状与契约不符（userId + { parseLimit, chatLimit }）')
  })

  test('解析上限与上限来源都读了（override 决定回填，source 决定解释）', () => {
    const m = adminCode.match(/const toUser = \(([\s\S]*?)\n\}\)/)
    assert.ok(m, '取不到 toUser')
    for (const f of ['parse_limit_override', 'chat_limit_override', 'parse_limit_source', 'chat_limit_source']) {
      assert.ok(m[1].includes(f), `toUser 没有读 ${f} —— 改完额度看不到有没有真的落成 override`)
    }
  })
})

describe('AI 服务页签 · 模型清单可改（ADR 0010）', () => {
  const apiAdmin = stripComments(read('../src/api/admin.js'))

  test('页签不再自称「只读」—— 文案必须与实现对得上', () => {
    // ADR 0010 写的是「AI 服务（模型清单可改）」，ADR 0011 更把
    // 「只读展示」明确列为**被否决**的方案（理由：等于诊断页）。
    // 文案说只读、实现也只读，那是两头都不落：既没实现可改，
    // 也没把它登记成显式延后。
    assert.ok(!/^只读[。.]/m.test(admin), 'AI 服务页签仍自称「只读」，但它现在可改')
    assert.ok(/可改显示名/.test(admin), '页签应说明哪些字段可改')
    assert.ok(/\.env/.test(admin), '仍要说明凭据留在 .env（ADR 0011）')
  })

  test('保存真的调 updateAdminModel（挂上函数不调用 = 装饰）', () => {
    assert.match(adminCode, /import\s*\{[^}]*\bupdateAdminModel\b[^}]*\}\s*from\s*'\.\.\/api\/admin\.js'/,
      'AdminPage.vue 没有 import updateAdminModel')
    assert.match(adminCode, /await\s+updateAdminModel\(/,
      '保存逻辑没有真的调 updateAdminModel')
    assert.match(adminCode, /@click="saveModel\(/,
      '保存按钮没有绑 saveModel')
  })

  test('enabled 必须翻成 0/1 —— 后端显式拒布尔值', () => {
    // Python 里 True == 1：`{"enabled": true}` 会静静地变成「上架」。
    // 这条断言断的是那层翻译真的在，而不是「enabled 这个词出现过」。
    assert.match(apiAdmin, /body\.enabled\s*=\s*patch\.enabled\s*\?\s*1\s*:\s*0/,
      'api/admin.js 没把 enabled 翻成 0/1，后端会拒布尔值')
  })

  test('只带调用方传了的键（PATCH 语义）', () => {
    // 守卫与赋值用的是**两套**键名，这里必须分开查：
    //   守卫  'baseUrl' in patch      （camelCase，看调用方传了什么）
    //   赋值  body.base_url = ...      （snake_case，后端契约）
    // 早先只按 snake_case 查守卫，于是七条一条都查不到 ——
    // 断言恒假。恒假和恒真一样让人以为「有守卫」。
    //
    // 无条件把七个字段全发过去 = 每次改一个显示名都顺手刷掉模型列表、
    // 端点、排序成草稿里的值 —— 而草稿可能没加载全。
    for (const key of ['label', 'hint', 'baseUrl', 'models', 'defaultModel', 'enabled', 'sortOrder']) {
      assert.match(apiAdmin, new RegExp(`'${key}'\\s+in\\s+patch`),
        `请求体无条件带上了 ${key}：没传这个键时后端也会改它，PATCH 语义被破坏`)
    }
    assert.match(apiAdmin, /Object\.keys\(body\)\.length\s*===\s*0/,
      '空 patch 应当在前端就挡住（后端会回 400，但省一次往返）')
  })

  test('键名翻成 snake_case（发 camelCase 会静默什么都不改还返回 200）', () => {
    for (const key of ['base_url', 'default_model', 'sort_order']) {
      assert.match(apiAdmin, new RegExp(`body\\.${key}\\s*=`),
        `没有翻成 snake_case 的 ${key}`)
    }
  })

  test('成功用**回读**结果替换本地行，不做乐观更新', () => {
    // 先本地改卡片、请求失败时静默回滚 —— 管理员看到「改成功了」
    // 而实际没改，那比报错更坏。
    assert.match(adminCode, /await\s+updateAdminModel\([\s\S]*?replaceModel\(res\.item\)/,
      '保存成功后没有用服务端回读的那份替换本地行')
    assert.match(adminCode, /function\s+replaceModel\(/, '缺少 replaceModel')
    assert.ok(!/replaceModel\(\{\s*\.\.\./.test(adminCode),
      'replaceModel 收了本地草稿而不是服务端回读结果')
  })

  test('反馈走 aria-live，且成功提示说清平台默认变成了什么', () => {
    // 改默认模型的影响面是「平台实际会用哪个模型」，不说的话
    // 管理员只能看见「保存成功」，不知道刚才那下改了什么。
    const modelEditor = admin.slice(admin.indexOf('openModelEditor(m.id)'))
    assert.match(modelEditor, /aria-live="polite"/, '厂商编辑区没有 aria-live 反馈区')
    assert.match(adminCode, /platformDefault/, '成功提示里没带上平台默认模型的新值')
  })

  test('前端挡住「默认模型不在可选列表里」，不等服务端 400', () => {
    assert.match(adminCode, /list\.value\.indexOf\(d\.defaultModel\)\s*<\s*0/,
      '没在前端校验「平台默认必须从可选模型里选」')
    assert.match(adminCode, /indexOf\(p\)\s*!==\s*i/,
      '没在前端挡住重复的模型名')
  })

  test('草稿在渲染前播种（v-model 绑不到成员表达式）', () => {
    // 与额度草稿同一条硬约束：Vue 的 v-model 只接受成员表达式，
    // `modelDrafts[m.id].label` 必须在该 key 已存在时才合法。
    assert.match(adminCode, /function\s+primeModelDrafts\(/, '缺少 primeModelDrafts')
    assert.match(adminCode, /primeModelDrafts\(v\.items\)/,
      'loadModels 渲染前没有播种草稿')
  })

  test('保存中禁用按钮，避免重复提交', () => {
    const modelEditor = admin.slice(admin.indexOf('openModelEditor(m.id)'))
    assert.match(modelEditor, /:disabled="savingModelId\s*!==\s*null"/,
      '保存中没禁用按钮，连点会发多次')
  })
})
// ── 把 saveModel 抽出来实跑 ──────────────────────────────────
//
// 前面那些断言是「在源码里找某个表达式」，它们证明不了这个表达式**接对了线**：
// 变异 FM6（saveModel 改发旧行而不是草稿）全绿，FM5（去掉 !list.ok 早退）全绿。
// 下面这组把函数体真的执行一遍，断的是「updateAdminModel 收到的 payload」。
//
// 抽函数用大括号配平计数。saveModel / toModelList / hasModels 三个函数体里
// 没有正则字面量，模板串里的 ${} 自身配平，所以朴素计数是安全的 ——
// 这个前提哪天不成立，计数会失配并在这里直接抛错，不会静默抽错。

function extractFn(src, name) {
  const at = src.indexOf(`function ${name}(`)
  // saveModel is async: slicing from `function` drops the keyword and
  // every `await` inside turns into a SyntaxError at Function() time.
  const start = src.slice(Math.max(0, at - 6), at) === 'async ' ? at - 6 : at
  assert.ok(at > 0, `抽不出 ${name}`)
  let i = src.indexOf('{', start)
  let depth = 0
  for (; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1
    else if (src[i] === '}') {
      depth -= 1
      if (depth === 0) return src.slice(start, i + 1)
    }
  }
  throw new Error(`${name} 的大括号不配对 —— 抽出来的会是半个函数`)
}

const SAVE_FN_BODY = [
  extractFn(adminCode, 'toModelList'),
  extractFn(adminCode, 'hasModels'),
  extractFn(adminCode, 'saveModel'),
].join('\n')

/**
 * 造一个能跑 saveModel 的沙箱。
 *
 * modelDrafts **必须是同一个对象**贯穿「工厂入参」与「返回值」——
 * 各造一份的话种子写进了函数看不见的地方，所有用例都因为 d={} 而早退，
 * 而「全绿」看起来和「判据在咬」一模一样。
 */
function makeSaveSandbox({ saving = null } = {}) {
  const calls = []
  const modelDrafts = {}
  const modelFeedbacks = {}
  const modelFeedbackOf = (id) => {
    if (!modelFeedbacks[id]) modelFeedbacks[id] = { kind: '', text: '' }
    return modelFeedbacks[id]
  }
  const factory = new Function(
    'modelDrafts', 'modelFeedbacks', 'savingModelId', 'modelFeedbackOf',
    'updateAdminModel', 'replaceModel', 'messageOf',
    `${SAVE_FN_BODY}\nreturn { saveModel, toModelList, hasModels }`,
  )
  const api = factory(
    modelDrafts, modelFeedbacks, { value: saving }, modelFeedbackOf,
    async (id, patch) => {
      calls.push({ id, patch })
      return { item: { id, label: patch.label }, platformDefault: 'm1' }
    },
    () => {}, (e) => String((e && e.message) || e),
  )
  return { ...api, modelDrafts, calls, modelFeedbacks, modelFeedbackOf }
}

const REAL_ROW = {
  id: 'bailian', label: '阿里云百炼', enabled: true, sortOrder: 10,
  models: ['qwen-plus', 'qwen-turbo'], defaultModel: 'qwen-plus',
}
/** platform / custom：后端播种就是 models=[] 的占位行 */
const PLACEHOLDER_ROW = {
  id: 'platform', label: '平台', enabled: true, sortOrder: 0,
  models: [], defaultModel: '',
}

function seedDraft(box, row, over = {}) {
  box.modelDrafts[row.id] = {
    label: row.label, hint: '', baseUrl: '',
    modelsText: (row.models || []).join(', '),
    defaultModel: row.defaultModel || '',
    enabled: row.enabled ? 1 : 0, sortOrder: row.sortOrder,
    ...over,
  }
}

describe('saveModel 真跑 · 发出去的 payload', () => {
  test('沙箱自检：种子与函数看见的是同一份草稿', async () => {
    const box = makeSaveSandbox()
    seedDraft(box, REAL_ROW, { label: '草稿里的名字' })
    await box.saveModel(REAL_ROW)
    assert.equal(box.calls.length, 1,
      '沙箱没接上：草稿写进了函数看不见的对象，后面所有用例都会假绿')
  })

  test('正常行：带上 models 与 defaultModel，值取自草稿', async () => {
    const box = makeSaveSandbox()
    seedDraft(box, REAL_ROW, { label: '改过的名', defaultModel: 'qwen-turbo' })
    await box.saveModel(REAL_ROW)
    assert.equal(box.calls.length, 1)
    const { patch } = box.calls[0]
    assert.equal(patch.label, '改过的名', 'label 必须取自草稿，不是旧行')
    assert.deepEqual(patch.models, ['qwen-plus', 'qwen-turbo'])
    assert.equal(patch.defaultModel, 'qwen-turbo', '平台默认必须取自草稿')
    assert.equal(patch.enabled, true)
    assert.equal(patch.sortOrder, 10)
  })

  test('占位行（models 为空）：能保存，且**不发** models/defaultModel', async () => {
    // HIGH-1 的回归测试：占位行过去第一步就早退，任何字段都存不了
    const box = makeSaveSandbox()
    seedDraft(box, PLACEHOLDER_ROW, { label: '平台（改名成功）', enabled: 0 })
    await box.saveModel(PLACEHOLDER_ROW)
    assert.equal(box.calls.length, 1,
      `占位行应当也能保存，实得 ${box.calls.length} 次调用：`
      + `${box.modelFeedbacks.platform && box.modelFeedbacks.platform.text}`)
    const { patch } = box.calls[0]
    assert.equal(patch.label, '平台（改名成功）')
    assert.equal(patch.enabled, false, '下架也要能存')
    // 关键：不能发空数组。后端拒空 models，硬发等于让这一行彻底存不了
    assert.ok(!('models' in patch),
      `占位行不该发 models，实得 ${JSON.stringify(patch.models)}`)
    assert.ok(!('defaultModel' in patch), '占位行不该发 defaultModel')
  })

  test('空模型列表的早退仍在：正常行清空可选模型必须被拒，且不发请求', async () => {
    // 变异 A：删掉 if (!list.ok) return —— 这条必须转红
    const box = makeSaveSandbox()
    seedDraft(box, REAL_ROW, { modelsText: '  ' })
    await box.saveModel(REAL_ROW)
    assert.equal(box.calls.length, 0, '空可选模型必须挡住，不该发出请求')
    const fb = box.modelFeedbacks.bailian
    assert.equal(fb.kind, 'error', '要给出错误态')
    assert.match(fb.text, /可选模型/, `错误文案要说清是可选模型的问题：${fb.text}`)
  })

  test('平台默认不在可选列表里：挡住且不发请求', async () => {
    const box = makeSaveSandbox()
    seedDraft(box, REAL_ROW, { defaultModel: 'ghost' })
    await box.saveModel(REAL_ROW)
    assert.equal(box.calls.length, 0, '默认模型越出列表必须挡住')
    assert.equal(box.modelFeedbacks.bailian.kind, 'error')
  })

  test('模型名重复：挡住且不发请求（后端也会拒，但那边讲的是后端规则）', async () => {
    const box = makeSaveSandbox()
    seedDraft(box, REAL_ROW, { modelsText: 'a, b, a' })
    await box.saveModel(REAL_ROW)
    assert.equal(box.calls.length, 0)
    assert.match(box.modelFeedbacks.bailian.text, /重复/)
  })

  test('保存中再点一次不会发第二个请求', async () => {
    const box = makeSaveSandbox({ saving: 'bailian' })
    seedDraft(box, REAL_ROW)
    await box.saveModel(REAL_ROW)
    assert.equal(box.calls.length, 0, '正在保存时再点必须直接返回')
  })
})
// ── 账号生命周期 · 抽函数真跑（ADR 0012）────────────────────
/**
 * toUser 的替身。它在组件里是真实存在的，但本文件从来没求值过它
 * （只 includes 过名字），所以这里自己实现一份。
 *
 * **必须**照抄 `!!r.is_admin` 这一步：「回读替换」那条断言要靠它——
 * 服务端回的是 0/1，替身若原样透传，那条断言就变成了在测替身自己。
 *
 * 替身只保留这几个用例关心的字段；本组测试的被测对象是
 * saveNewUser / toggleAdmin / confirmDelete，不是 toUser 本身。
 */
const toUserStub = (r) => ({
  id: r.id,
  email: r.email,
  isAdmin: !!r.is_admin,
  isVip: !!r.is_vip,
})

const USER_FN_BODY = [
  extractFn(adminCode, 'saveNewUser'),
  extractFn(adminCode, 'toggleAdmin'),
  extractFn(adminCode, 'askDelete'),
  extractFn(adminCode, 'cancelDelete'),
  extractFn(adminCode, 'confirmDelete'),
].join('\n')

/**
 * 沙箱。view 用普通对象 { value: { items, total } } 冒充 computed——
 * 组件里那几处写的是 view.value.xxx，这个形状能对上。
 *
 * opsFeedbacks / createDraft / createFeedback 必须是**同一份**对象贯穿
 * 「工厂入参」与断言侧：各造一份的话种子写进函数看不见的地方，
 * 所有用例都因为 d={} 早退，而「全绿」和「判据在咬」看起来一模一样。
 *
 * `api` 参数用来注入会抛错的 API：失败分支与 409 分支必须真的跑一遍，
 * 不能靠「反正成功路径绿了」推断失败路径也绿。
 */
function makeUserSandbox({ items = [], total = 0, api = {} } = {}) {
  const calls = []
  const opsFeedbacks = {}
  const createDraft = { email: '', password: '', isAdmin: false }
  const createFeedback = { kind: '', text: '' }
  const pendingDeleteId = { value: null }
  const view = { value: { items, total } }

  const defaultApi = {
    async createAdminUser(payload) {
      calls.push({ op: 'create', payload })
      return { user: { id: 99, email: payload.email, is_admin: payload.is_admin ? 1 : 0 } }
    },
    async setUserAdmin(id, flag) {
      calls.push({ op: 'admin', id, flag })
      return { user: { id, is_admin: flag ? 1 : 0 } }
    },
    async deleteAdminUser(id) {
      calls.push({ op: 'delete', id })
      return { deleted: id }
    },
  }
  const impl = { ...defaultApi, ...api }

  const factory = new Function(
    'createOpen', 'creating', 'busyUserId', 'pendingDeleteId',
    'createDraft', 'createFeedback', 'opsFeedbacks', 'view',
    'opsFeedbackOf', 'createAdminUser', 'setUserAdmin', 'deleteAdminUser',
    'replaceUser', 'toUser', 'messageOf',
    `${USER_FN_BODY}\nreturn { saveNewUser, toggleAdmin, askDelete, cancelDelete, confirmDelete }`,
  )
  const fns = factory(
    { value: false }, { value: false }, { value: null }, pendingDeleteId,
    createDraft, createFeedback, opsFeedbacks, view,
    (id) => {
      if (!opsFeedbacks[id]) opsFeedbacks[id] = { kind: '', text: '' }
      return opsFeedbacks[id]
    },
    impl.createAdminUser, impl.setUserAdmin, impl.deleteAdminUser,
    (raw) => {
      // replaceUser 的替身：只做「把回读结果换成 camelCase 那一行」
      const fresh = toUser(raw)
      const i = view.value.items.findIndex((x) => x.id === fresh.id)
      if (i >= 0) view.value.items.splice(i, 1, fresh)
    },
    toUserStub,
    (e) => String((e && e.response && e.response.data && e.response.data.detail)
      || (e && e.message) || e),
  )
  return { ...fns, calls, opsFeedbacks, createDraft, createFeedback, view, pendingDeleteId }
}

function userRow(id, over = {}) {
  return {
    id, email: `u${id}@example.com`, isAdmin: false, isVip: false,
    parseUsed: 0, chatUsed: 0, parseLimit: 3, chatLimit: 10,
    parseLimitOverride: null, chatLimitOverride: null, createdAt: '2026-01-01',
    ...over,
  }
}

/** 造一个带 blockers 的 409 错误对象，形状与 axios 收到的一致。 */
function conflictErr(detail, blockers) {
  return { response: { data: { detail, blockers } } }
}

describe('账号生命周期 · 建号（ADR 0012）', () => {
  test('沙箱自检：view 与草稿是同一个对象', async () => {
    const box = makeUserSandbox()
    box.createDraft.email = 'a@example.com'
    box.createDraft.password = 's3cret-pass'
    await box.saveNewUser()
    assert.equal(box.calls.length, 1, '沙箱没接上，后面所有用例都会假绿')
  })

  test('成功：新号进列表、按 id 排序、total 加一', async () => {
    const box = makeUserSandbox({ items: [userRow(1), userRow(5)], total: 2 })
    box.createDraft.email = '  new@example.com  '
    box.createDraft.password = 's3cret-pass'
    await box.saveNewUser()
    assert.equal(box.calls.length, 1)
    assert.equal(box.calls[0].payload.email, 'new@example.com', '邮箱要 trim 后再发')
    // 刻意**不**断言「按 id 排序」：新号 id 由 autoincrement 产生、恒为最大，
    // push 本身就落在升序末尾。为它写一条断言只会制造「已覆盖」的错觉——
    // 实测那条断言在拿掉 sort 之后照样全绿。
    assert.equal(box.view.value.items.length, 3)
    assert.equal(box.view.value.items[2].id, 99, '新号应追加到末尾')
    assert.equal(box.view.value.items[2].email, 'new@example.com')
    assert.equal(box.view.value.total, 3)
    // 表单要清空，否则再点一次就是拿同一个邮箱重复建号
    assert.equal(box.createDraft.email, '')
    assert.equal(box.createDraft.password, '')
  })

  test('勾了管理员发 true，没勾发 false', async () => {
    const box = makeUserSandbox()
    box.createDraft.email = 'boss@example.com'
    box.createDraft.password = 's3cret-pass'
    box.createDraft.isAdmin = true
    await box.saveNewUser()
    assert.equal(box.calls[0].payload.isAdmin, true)
  })

  test('空邮箱与短口令在本地就挡下，不发请求', async () => {
    const box = makeUserSandbox()
    box.createDraft.email = '   '
    box.createDraft.password = 's3cret-pass'
    await box.saveNewUser()
    assert.equal(box.calls.length, 0, '空邮箱不该发请求')
    assert.equal(box.createFeedback.kind, 'error')

    const box2 = makeUserSandbox()
    box2.createDraft.email = 'a@example.com'
    box2.createDraft.password = '12345'
    await box2.saveNewUser()
    assert.equal(box2.calls.length, 0, '短口令不该发请求')
    assert.equal(box2.createFeedback.kind, 'error')
  })

  test('建号失败：给出错误，列表与 total 一个都不动', async () => {
    const box = makeUserSandbox({
      items: [userRow(1)], total: 1,
      api: { async createAdminUser() { throw conflictErr('该邮箱已注册', {}) } },
    })
    box.createDraft.email = 'dupe@example.com'
    box.createDraft.password = 's3cret-pass'
    await box.saveNewUser()
    assert.equal(box.createFeedback.kind, 'error')
    assert.match(box.createFeedback.text, /该邮箱已注册/)
    assert.equal(box.view.value.items.length, 1, '失败不该往列表里塞行')
    assert.equal(box.view.value.total, 1)
    assert.equal(box.createDraft.email, 'dupe@example.com',
      '失败时表单要留着，让管理员改完再提交')
  })
})

describe('账号生命周期 · 管理员标记（ADR 0012）', () => {
  test('普通用户 → 发 true；管理员 → 发 false', async () => {
    const box = makeUserSandbox({ items: [userRow(1)], total: 1 })
    await box.toggleAdmin(box.view.value.items[0])
    assert.equal(box.calls[0].op, 'admin')
    assert.equal(box.calls[0].flag, true, '普通用户要提权，发 true')

    const box2 = makeUserSandbox({ items: [userRow(2, { isAdmin: true })], total: 1 })
    await box2.toggleAdmin(box2.view.value.items[0])
    assert.equal(box2.calls[0].flag, false, '管理员要撤权，发 false')
  })

  test('成功用**回读**替换本地行，不做乐观更新', async () => {
    const box = makeUserSandbox({
      items: [userRow(1)], total: 1,
      // 回读说「其实没提成」——本地行必须跟着回读走
      api: { async setUserAdmin() { return { user: { id: 1, is_admin: 0 } } } },
    })
    await box.toggleAdmin(box.view.value.items[0])
    assert.equal(box.view.value.items[0].isAdmin, false,
      '本地行必须被回读结果替换，而不是调用方传进去的 flag')
  })

  test('409（撤自己）：反馈是 error，行不变', async () => {
    const box = makeUserSandbox({
      items: [userRow(1, { isAdmin: true })], total: 1,
      api: {
        async setUserAdmin() {
          throw conflictErr('不能撤销自己的管理员权限——那样就没有人能把它改回来了', {})
        },
      },
    })
    await box.toggleAdmin(box.view.value.items[0])
    assert.equal(box.opsFeedbacks[1].kind, 'error')
    assert.match(box.opsFeedbacks[1].text, /不能撤销自己/)
    assert.equal(box.view.value.items[0].isAdmin, true, '被拒之后权限不能变')
  })
})

describe('账号生命周期 · 删除（ADR 0012）', () => {
  test('成功：行从列表移除、total 减一', async () => {
    const box = makeUserSandbox({ items: [userRow(1), userRow(2)], total: 2 })
    await box.confirmDelete(box.view.value.items[0])
    assert.equal(box.calls[0].op, 'delete')
    assert.deepEqual(box.view.value.items.map((x) => x.id), [2])
    assert.equal(box.view.value.total, 1)
  })

  test('409：行留下、total 不变、反馈里带**数字**', async () => {
    const box = makeUserSandbox({
      items: [userRow(1)], total: 1,
      api: {
        async deleteAdminUser() {
          // detail 里**故意不带数字**：它只说「还有内容」。
          // 于是断言里的「订单 2 条」只可能来自 blockers 的渲染——
          // 而服务端那句话本身已经含数字时，这条断言会被它白送。
          throw conflictErr('这个账号名下还有内容，先处理掉再删。',
            { orders: 2, parse_history: 5 })
        },
      },
    })
    await box.confirmDelete(box.view.value.items[0])
    assert.equal(box.view.value.items.length, 1, '被拒之后不该把人从列表里抹掉')
    assert.equal(box.view.value.total, 1, '被拒之后 total 不该减')
    const fb = box.opsFeedbacks[1]
    assert.equal(fb.kind, 'error')
    // 数字必须来自 blockers 字段，不是从中文里抠出来的
    assert.match(fb.text, /订单 2 条/)
    assert.match(fb.text, /解析历史 5 条/)
  })

  test('删除是**两步**：askDelete 只开确认，不发请求', async () => {
    const box = makeUserSandbox({ items: [userRow(1)], total: 1 })
    box.askDelete(box.view.value.items[0])
    assert.equal(box.calls.length, 0, '点「删除」不该立刻发请求')
    assert.equal(box.pendingDeleteId.value, 1, '应当进入待确认状态')
    box.cancelDelete()
    assert.equal(box.pendingDeleteId.value, null, '取消要回到没有待确认的状态')
  })
})

describe('账号生命周期 · 静态契约', () => {
  test('模板里不用 window.confirm（它阻塞事件循环且无法断言）', () => {
    assert.doesNotMatch(adminTemplate, /window\.confirm/,
      '删除确认必须是内联二次确认，不是 window.confirm')
  })

  test('删除确认行存在，两个按钮都接上了各自的处理器', () => {
    assert.match(adminTemplate, /v-if="pendingDeleteId === u\.id"/,
      '没有内联二次确认行')
    assert.match(adminTemplate, /@click="confirmDelete\(u\)"/)
    assert.match(adminTemplate, /@click="cancelDelete"/)
  })

  test('两条反馈都接进 aria-live（成功与失败都要被读屏读到）', () => {
    const live = [...adminTemplate.matchAll(/aria-live="polite"/g)]
    assert.ok(live.length >= 2,
      `只找到 ${live.length} 处 aria-live —— 建号反馈与行内操作反馈各要一处`)
  })

  test('模板里从不写 is_vip —— VIP 不在后台可改范围', () => {
    const hits = [...adminTemplate.matchAll(/is_vip|vip_expire_at|vipExpireAt/g)].map((m) => m[0])
    assert.deepEqual(hits, [],
      `模板里出现了 VIP 字段：${hits.join(' ')}\n`
      + '  —— VIP 只能由订单支付写入，后台写它会同时踩到'
      + '「新增测试不得锁会员行为」这条纪律。')
  })

  test('行内的管理员标记是**按钮开关**，不是可自由编辑的输入框', () => {
    assert.match(adminTemplate, /@click="toggleAdmin\(u\)"/)
    // 只断**行内**：建号表单里那个 createDraft.isAdmin 勾选框是合理的
    // （建号时就是要决定给不给管理员），把它一起禁掉是判据越界。
    const rowScoped = [...adminTemplate.matchAll(/v-model="([^"]*[Ii]sAdmin[^"]*)"/g)]
      .map((m) => m[1])
      .filter((expr) => !/createDraft/.test(expr))
    assert.deepEqual(rowScoped, [],
      `行内出现了可编辑的管理员标记输入框：${rowScoped.join(' ')}`
      + '——它是一次开关动作，不是可以随手改成任意值的字段。')
  })

  test('新建表单的每个输入都有 label（可达性判据要求 input 有 id）', () => {
    for (const id of ['admin-new-email', 'admin-new-password', 'admin-new-isadmin']) {
      assert.match(adminTemplate, new RegExp(`id="${id}"`), `建号表单缺 ${id}`)
      assert.match(adminTemplate, new RegExp(`for="${id}"`), `${id} 没有对应的 label`)
    }
  })
})
