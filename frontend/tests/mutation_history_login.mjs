// 历史页「未登录给登录入口」的变异核验（工单 #95）。
//
// 为什么单独一个脚本：判据在 **vitest 挂载用例**里（真挂载、真发事件、
// 真看 DOM），而仓库里既有的前端变异脚本（mutation-ui-fixes /
// mutation_wiring / mutation-nav-wall）跑的是 `node --test`，那条路上
// 碰不到 .spec.mjs。要让这批判据真能变红，变异装置就得跑 vitest。
//
// 每条变异都必须让挂载用例转红；转不红 = 测试守不住 = 失败。
// 跑完逐字节还原源文件，末尾自检。
//
// 用法：node tests/mutation_history_login.mjs
// 放在 tests/ 下但文件名不带 .test.mjs —— npm test 只收 *.test.mjs。
import { readFileSync, writeFileSync, rmSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tmpdir } from 'node:os'

const FRONTEND = dirname(dirname(fileURLToPath(import.meta.url)))
const VUE = join(FRONTEND, 'src', 'components', 'HistoryPage.vue')
const VITEST = join(FRONTEND, 'node_modules', 'vitest', 'vitest.mjs')
const SPEC = 'tests/history-page.mount.spec.mjs'

// 锚点都带足够多的上下文：组件里 `isLoggedIn()` 与 `isUnauthorized(e)`
// 各出现两处（load / loadFacets），只写短锚点会命中错的那一处，
// 变异就会打在没被断言覆盖的分支上 —— 那是「变异存活」的假象。
const MUTATIONS = [
  [
    'H1 未登录时不再拦请求（load 不再短路）',
    '  if (!isLoggedIn()) {\n    // 没登录就不去撞 401',
    '  if (false) {\n    // 没登录就不去撞 401',
  ],
  [
    'H2 401 不再翻译成 needLogin（load）',
    '    if (isUnauthorized(e)) {\n      // token 在本地还在',
    '    if (false) {\n      // token 在本地还在',
  ],
  [
    'H3 未登录时标签也去撞 401（loadFacets 不再短路）',
    '  if (!isLoggedIn()) {\n    // 标签同样是私有数据',
    '  if (false) {\n    // 标签同样是私有数据',
  ],
  ['H4 模板里没有未登录这一支', 'v-else-if="needLogin"', 'v-else-if="neverNeedLogin"'],
  [
    'H5 登录后不自动刷新列表',
    '      needLogin.value = false\n      void load()\n      void loadFacets()',
    '      needLogin.value = false\n      void Promise.resolve()\n      void Promise.resolve()',
  ],
  [
    'H6 标签 401 变成橙色报错条（而不是安静地当空标签）',
    '    if (isUnauthorized(e)) {\n      // 401：标签也是私有数据',
    '    if (false) {\n      // 401：标签也是私有数据',
  ],
]

const original = readFileSync(VUE, 'utf8')
const normalized = original.replace(/\r\n/g, '\n')
let failed = false

function applyMutation(text, pattern, repl) {
  if (!text.includes(pattern)) return null
  return text.replace(pattern, repl)
}

/** 读 vitest 的 JSON 报告；读不到或不是 JSON，就当「这次没跑起来」。 */
function readReport(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch {
    return null
  }
}

/** 跑一遍挂载用例，返回 { ran, status, failedTests }。 */
function runSpec() {
  const out = join(tmpdir(), `vitest-mut-${process.pid}.json`)
  const r = spawnSync(process.execPath, [VITEST, 'run', SPEC, '--reporter=json', `--outputFile=${out}`], {
    cwd: FRONTEND,
    encoding: 'utf8',
  })
  const report = readReport(out)
  try {
    rmSync(out, { force: true })
  } catch {
    /* 临时文件清不掉不算失败 */
  }
  const failedTests = []
  for (const suite of report?.testResults ?? []) {
    for (const t of suite.assertionResults ?? []) {
      if (t.status === 'failed') failedTests.push(t.fullName || t.title)
    }
  }
  return {
    ran: !!report && report.numTotalTests > 0,
    status: r.status,
    failedTests,
  }
}

try {
  for (const [name, pattern, repl] of MUTATIONS) {
    const mutated = applyMutation(normalized, pattern, repl)
    if (mutated === null) {
      console.log(`  [未命中] ${name} —— 模式没匹配上，变异无效`)
      failed = true
      continue
    }
    writeFileSync(VUE, mutated.replace(/\n/g, '\r\n'), 'utf8')
    const result = runSpec()
    writeFileSync(VUE, original, 'utf8')
    if (!result.ran) {
      console.log(`  [装置故障] ${name} —— 测试没跑起来，这条变异不算数`)
      failed = true
      continue
    }
    if (result.status === 0) {
      console.log(`  [存活] ${name}  <-- 测试守不住`)
      failed = true
    } else {
      console.log(`  [杀死] ${name}（${result.failedTests.length} 条失败）`)
      for (const t of result.failedTests) console.log(`         · ${t}`)
    }
  }
} finally {
  writeFileSync(VUE, original, 'utf8')
}

const restored = readFileSync(VUE, 'utf8') === original
console.log(`\n还原自检: ${restored ? 'HistoryPage.vue 逐字节一致' : '还原失败！'}`)
process.exit(failed || !restored ? 1 : 0)
