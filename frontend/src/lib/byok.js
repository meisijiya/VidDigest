/**
 * 自带凭据（BYOK）的**唯一**状态源。
 *
 * 为什么要独立成一个模块：解析与追问是两条不同的代码路径，而「用谁的
 * key」是一个用户层面的决定。放在任何一个组件里，另一条路径就看不见它——
 * 于是用户填了 key，追问不扣额度但解析照扣，两边行为对不上，
 * 而页面上没有任何地方能解释这个差别。
 *
 * ## 真值只从一条出口出去
 *
 * 模块内部闭包持有 api_key 明文。**唯一**能把它读出去的函数是
 * `getRequestCredential()`，它只被 api/summarize.js 拼进请求体用。
 * UI 一律读 `getPublicState()`——那个返回值里结构上就没有 key 这个键，
 * 所以「不小心把 key 渲染出去」需要一个凭空多出来的键，不是一句手滑。
 *
 * 纪律沿用原 BYOK 面板的三条，多了一条：
 * 1. 真值不进任何会渲染的节点，也不进任何错误信息。
 * 2. 输入框在保存后立刻清空——留在 DOM 里会进浏览器自动填充与「检查元素」。
 * 3. 只交给请求层一次。
 * 4. **配置（厂商 / 端点 / 模型）可以公开**，key 不行。
 */

/**
 * 厂商清单**不在这个模块里**。
 *
 * 它有一份真值来源：服务端的 `GET /api/models`（由 `api/models.js` 拉取，
 * 字段转换收在那一个文件里）。工单 #13 之前这里是硬编码的 7 个厂商，
 * 而 summarizer.py 只认 2 个、默认模型也不一样 —— 两份清单各说各话，
 * 正在漂移。现在本模块只留**用户的选择状态**（provider 是哪个 id）。
 *
 * 组件拉清单、自己渲染下拉，选中时把**已经拿在手里的**那条记录连同 id 一起
 * 交给 `chooseProvider(id, provider)`。这样漂移源是被删掉，而不是搬到别处。
 *
 * 刻意**不留**「接口没回来之前先用这些」的兜底表：那份兜底就是漂移本身，
 * 而且它会让「前端不再硬编码厂商」这条断言永远测不红。
 *
 * `platform` 仍然作为**模式**默认值出现在本模块（见 MODE_PLATFORM）：它是
 * 「用谁的额度」这个开关，不是厂商，不需要向服务端查。
 */

const KEY_STORE = 'viddigest_user_api_key'
const CONFIG_STORE = 'viddigest_byok_config'

/** 两种使用方式。用户要的「一个下拉选其一」就是这两个值。 */
export const MODE_PLATFORM = 'platform'
export const MODE_BYOK = 'byok'

/** 端点只做与后端一致的最粗筛，真正的判定在服务端。 */
export function normalizeBaseUrl(raw) {
  return String(raw || '').trim().replace(/\/+$/, '')
}

export function normalizeModel(raw) {
  return String(raw || '').trim()
}

/**
 * 前端侧的端点预检。**只是为了少发一次注定被拒的请求**，
 * 不是安全边界——用户可以绕过它（直接调接口），所以服务端那道校验
 * 才是真正承重的那一道。
 */
export function validateBaseUrl(raw) {
  const text = normalizeBaseUrl(raw)
  if (!text) return null
  let parts
  try {
    parts = new URL(text)
  } catch {
    return '端点地址格式不对'
  }
  if (parts.protocol !== 'http:' && parts.protocol !== 'https:') {
    return '端点地址只支持 http 或 https'
  }
  if (parts.username || parts.password) {
    return '端点地址里不能带用户名或密码'
  }
  if (!parts.hostname) return '端点地址缺少主机名'
  return null
}

// ── 闭包内的真值 ──────────────────────────────────────────
//
// 刻意不做「可从外部任意读写的模块级变量」：那等于给每个调用方
// 一条能顺手 log 出来的路。改成只有下面几个函数能碰到。

let state = read()

const listeners = new Set()

function read() {
  let apiKey = ''
  let config = { provider: 'platform', baseUrl: '', model: '' }
  try {
    apiKey = localStorage.getItem(KEY_STORE) || ''
  } catch { apiKey = '' }
  try {
    const raw = localStorage.getItem(CONFIG_STORE)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === 'object') {
        config = {
          provider: typeof parsed.provider === 'string' ? parsed.provider : 'platform',
          baseUrl: normalizeBaseUrl(parsed.baseUrl),
          model: normalizeModel(parsed.model),
        }
      }
    }
  } catch { /* 坏数据当作没配过，不报错 */ }
  return { apiKey, config }
}

function notify() {
  for (const fn of listeners) {
    try { fn(getPublicState()) } catch { /* 一个订阅者坏了不该连累其它 */ }
  }
}

/**
 * 给 UI 读的状态。**结构上不含 key**。
 * 连 `keyHint` 之类的衍生字段也不给：任何以 key 为原料的字符串
 * 都会跟着进 DOM 与截图。
 *
 * mode 只反映**用户的显式选择**（选了哪个厂商），不反映「有没有填 key」——
 * 这两件事合成一个字段的话，「选了自带但还没填」会被渲染成「用平台」，
 * 用户于是以为自己没选过。`hasKey` 单独给，UI 负责把这两条拼成一句话。
 *
 * mode 同时是**请求侧的开关**（见 getRequestCredential）：渲染成「用平台额度」
 * 时请求里就不会带用户自己的 key。两边必须一致 —— 它们本来就是同一个决定。
 */
export function getPublicState() {
  return {
    mode: state.config.provider === MODE_PLATFORM ? MODE_PLATFORM : MODE_BYOK,
    provider: state.config.provider,
    baseUrl: state.config.baseUrl,
    model: state.config.model,
    hasKey: !!state.apiKey,
  }
}

/**
 * 给请求层用的真值。**只该被 api/summarize.js 调用**。
 *
 * mode 是**开关**，不只是显示：用户在弹窗里选了「用平台额度」，
 * 请求就不该带他自己的 key —— 否则界面上写着走平台、实际扣的是他自己的额度，
 * 而两边对不上时用户没有任何线索能发现（工单 #26）。
 * 以前这条只判「有没有 key」，于是「有 key ≠ 这次会用它」这个承诺没人兑现。
 *
 * 代价要说清：切到平台模式时已存的 key **仍在 localStorage 里**，
 * 只是这条链路上看不见它。所以 `usePlatform()` 用 save 而不是 clear ——
 * 切回自带模式不用重填（那条语义在 byok-center.test.mjs 里有两条断言守着：
 * 一条验存储层没被清，一条验切回来拿得到）。
 *
 * 没有 key 时返回 null，让调用方走平台路径（而不是发一个空 key 过去）。
 */
export function getRequestCredential() {
  if (!state.apiKey) return null
  if (state.config.provider === MODE_PLATFORM) return null
  return {
    apiKey: state.apiKey,
    baseUrl: state.config.baseUrl,
    model: state.config.model,
  }
}

export function subscribe(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/** 保存一整套配置。key 为空等于清除。 */
export function save({ apiKey, provider, baseUrl, model }) {
  const trimmedKey = String(apiKey || '').trim()
  state = {
    apiKey: trimmedKey,
    config: {
      provider: String(provider || 'platform'),
      baseUrl: normalizeBaseUrl(baseUrl),
      model: normalizeModel(model),
    },
  }
  try {
    if (state.apiKey) localStorage.setItem(KEY_STORE, state.apiKey)
    else localStorage.removeItem(KEY_STORE)
    localStorage.setItem(CONFIG_STORE, JSON.stringify(state.config))
  } catch {
    // 隐私模式下 setItem 会抛。那就退化成「只本次有效」：
    // 内存里的 state 仍然是对的，用户照样能用，只是不跨刷新。
  }
  notify()
}

/**
 * 只改配置，**保留已存的 key**。
 *
 * 与 save 分开是因为「留空则沿用已保存的」是输入框占位符承诺的行为，
 * 而 save 的空 key 语义是「清除」。合成一个函数就得多一个布尔参数，
 * 而那个参数迟早会被传反一次——传反的后果是用户填了一次 key，
 * 换个模型再保存，key 被静默清空。
 */
export function updateConfig({ provider, baseUrl, model }) {
  save({
    apiKey: state.apiKey,
    provider,
    baseUrl,
    model,
  })
}

/** 清除 key 与端点，回到平台路径。 */
export function clear() {
  save({ apiKey: '', provider: 'platform', baseUrl: '', model: '' })
}

/**
 * 切到平台模式。**保留已存的 key**——切回来还得再填一遍是纯粹的折磨。
 * 用 save 而不是 clear：clear 会连端点一起抹掉，而用户很可能只是这一次
 * 想用平台额度。
 *
 * 「保留」是**存储层**的语义，挡路的是另一处：`getRequestCredential()`
 * 看到 provider 已是 platform 就返回 null（工单 #26）。这两件事必须分开理解 ——
 * 留在盘上 ≠ 这次会用。合成一处的话，切回来要么重填、要么模式压根不起作用。
 */
export function usePlatform() {
  save({
    apiKey: state.apiKey,
    provider: 'platform',
    baseUrl: state.config.baseUrl,
    model: state.config.model,
  })
}

/**
 * 选厂商：填入该厂商的默认端点与模型（可继续改）。
 *
 * `provider` 是调用方**已经拿在手里的**那条清单记录（`api/models.js` 转好的
 * camelCase）。本模块不查表——要查表就得自己持有一份清单，而那份清单会和
 * `GET /api/models` 漂移，正是工单 #13 要消除的那件事。
 *
 * 端点 / 模型取的是**填入时的默认值**，不是限制：用户可以在选完之后继续改
 * 这两栏（自建服务的模型名五花八门，猜错一次就会把「模型名写错」报成
 * 「凭据无效」，指向完全错误的方向）。
 *
 * 传不进来时退化成「两栏都留空」= 用服务端默认，而不是静默换成别的厂商：
 * 静默切换会让用户以为选中了 A，存下去的却是 B。
 *
 * 返回的是 **getPublicState()**，不是那条厂商定义。两者形状不同
 * （后者有 id / label / hint，没有 provider），调用方拿它直接塞进组件
 * state 的话，``state.provider`` 会是 undefined —— 而 provider 正是
 * 「用平台还是用自带」的那个开关，它一丢，保存时就会静默退回平台模式。
 */
export function chooseProvider(id, provider) {
  save({
    apiKey: state.apiKey,
    provider: String(id || 'platform'),
    baseUrl: normalizeBaseUrl(provider?.baseUrl),
    model: normalizeModel(provider?.defaultModel),
  })
  return getPublicState()
}
