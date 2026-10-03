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
  for (const m of src.matchAll(/class="([^"]*)"/g)) out.push(m[1])
  for (const m of src.matchAll(/:class="([^"]*)"/g)) {
    for (const s of m[1].matchAll(/'([^']*)'/g)) out.push(s[1])
  }
  return out.join(' ').split(/\s+/).filter(Boolean).map((t) => {
    const s = t.replace(/["'`]/g, '')
    return !s.includes('[') && s.endsWith(']') ? s.slice(0, -1) : s
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
  test('从 ../api/admin.js 导入四个函数（不建这个文件，读它也不做断言）', () => {
    const m = adminCode.match(/import \{([^}]*)\} from '\.\.\/api\/admin\.js'/)
    assert.ok(m, "没有从 '../api/admin.js' 导入")
    const names = m[1].split(',').map((s) => s.trim()).filter(Boolean)
    assert.deepEqual(names.sort(),
      ['fetchAdminCommunity', 'fetchAdminModels', 'fetchAdminUsers', 'setUserQuota'],
      '导入的四个 API 名字与冻结的契约不一致')
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
