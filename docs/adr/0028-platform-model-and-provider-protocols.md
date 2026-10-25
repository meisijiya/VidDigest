---
status: accepted
date: 2026-10-25
---

# ADR 0028：平台模型统一为 qwen3.7-flash，provider 走协议配置，删掉 DeepSeek 兜底

## 决定

**平台模型统一为 `qwen3.7-flash`**（百炼混合思考系列）。清单表里 `bailian` 行的
`default_model` 是它，`models` 列表把它放在首位。存量库靠一条**写迁移**把旧值
收敛过来——播种用的是 `ON CONFLICT(id) DO NOTHING`，只补缺失的行，改种子对老库
毫无作用。

**显式关思考**：每次请求都带 `enable_thinking: false`。该模型**默认开着**思考，
不显式关掉的话思维链会先吃掉 `max_tokens` 的预算（同一问句实测 845 vs 47 tokens），
长总结于是提前截断。

**参数**：总结与追问两路都 `temperature=0.4`；总结一路 `max_tokens=8192`
（原 4096），追问一路 `max_tokens=2048` 不动。字幕截断从 15000 字放宽到 60000 字
（追问那路的 12000 字不动）。

**没有兜底链**：删掉 DeepSeek 那段兜底分支。provider 失败就报错。只配了
`DEEPSEEK_API_KEY` **不**再算「配置好了」。

**provider 抽象**：新增 `backend/provider_config.py`，把一个 provider 表达成
「协议 + 端点 + 模型 + key」：

- `openai`：OpenAI 兼容协议（百炼兼容模式等），走仓库已有的 `openai` SDK；
- `anthropic`：Anthropic Messages API，用仓库已有的 `requests` 手写适配层
  （POST `/v1/messages`、`x-api-key`、`anthropic-version` 头、SSE 流），
  **不引入 anthropic SDK**。

两种协议对外形状一致（`client.chat.completions.create(...)`，增量块带
`choices[0].delta.content`），所以上游那段读取代码一个字都没改。

**协议与凭据不进表**：协议名、端点、key 全部从环境变量读
（`ALIYUN_BAILIAN_PROTOCOL` / `ALIYUN_BAILIAN_BASE_URL` / `ALIYUN_BAILIAN_API_KEY`）。
清单表仍然管「有哪些厂商、默认用哪个模型」（ADR 0011）。

**BYOK 不动**：用户自带凭据那条路径（ADR 0004 / ADR 0011）一个字没改。统一
平台模型 ≠ 统一用户凭据。

## 为什么

- **只换模型名不换端点必然 404**。DeepSeek 的模型名拿到百炼端点上去，端点根本
  不认识那个模型。所谓「换个模型名就升级了」是假的。
- **兜底链让失败变得不可见**。第一个 provider 挂了，错误被第二个 provider 的
  404 盖住，排查时看到的是一个与真实原因无关的报文；而且「配了就能用」的错觉
  本身就会把人引向错误的配置。
- **协议写死在代码里就没有插进来的地方**。换一个说 Anthropic Messages API 的
  provider，原先只能改 `summarizer.py` 的分支。
- **「改种子」不等于「存量库改了」**。播种只补缺失的行，线上库是老库，光改种子
  的话平台模型统一这件事在存量部署上根本不会发生。收敛只能靠写迁移。
- **思考默认开着，而预算是有限的**。思维链吃掉的 `max_tokens` 是从正文那里扣的。
- **Anthropic 那条路用到的就三样东西**：一个 POST、两个头、一条 SSE 流。为这三样
  东西拉一整个 SDK 不划算，而且 SDK 升级会带着行为变化进来。
- **协议与 key 同属部署配置**。key 已经在环境变量里（ADR 0004 / ADR 0011），
  协议再单独存进表，就得在两个地方配同一件事；而管理后台的改字段入口是冻结的，
  加一个改不动的字段比不加更糟。

## 代价

- **env 优先于表**。`.env` 里若留着 `ALIYUN_BAILIAN_MODEL=qwen-turbo`，它会盖过
  清单表里的新模型，而那个模型名在百炼工作空间端点上是 404。运维要么改那一行，
  要么删掉它。
- **换协议要改环境变量并重启**。协议是启动时读一次的。
- **只配了 DeepSeek key 的部署会启动失败**。这是有意的：那段兜底本来就在制造
  一个假的成功。报错信息写清了该配什么。
- **Anthropic 适配层是手写的**。Anthropic 改 API 时要自己跟。
- **`extra_body` 只能请求级传**。OpenAI SDK 的客户端构造函数不收它（实测
  openai 3.37.0），只有 `create()` 收，所以这个 provider 的固定请求体字段由
  适配层在每次调用时注入。

## 推翻条件

- 百炼不再提供 OpenAI 兼容模式，或平台改用别的协议族。
- 决定把 provider 的协议与端点也存进清单表（那要同时解掉管理后台的改字段限制，
  并重新想清楚 key 与非密钥字段的边界）。
- 产品改平台默认模型、参数数值或字幕截断长度。

## 后果

- `backend/provider_config.py`：provider 配置抽象 + Anthropic Messages API 适配层
  （纯函数与薄客户端，不读库、不落盘、不打 key 日志）。
- `backend/summarizer.py`：平台分支改走 `provider_config.build_client`；DeepSeek
  兜底分支与它的两个常量删掉；参数与字幕截断改成具名常量。
- `backend/model_catalog.py`：`PLATFORM_DEFAULT_MODEL` 常量、
  `_migrate_platform_default_model` 写迁移（只改「旧种子值」，管理员自己挑的
  模型名不动，幂等）。
- 行为由 `backend/tests/test_provider_config.py`（协议归一、两种协议的请求形状、
  错误路径）、`backend/tests/test_platform_provider.py`（平台口径：模型来源、
  env 优先、关思考、参数、没有兜底、协议路由）与 `backend/tests/test_model_catalog.py`
  （种子、迁移、老库升级）钉住；结构性约束由 `backend/tests/mutation_prompt.py`
  里的 V1–V5 确认能变红，其中 V5 是反向对照（必须保持绿）。
- 运维口径（怎么配 provider、怎么选协议、怎么填 key、别把 key 提交进仓库）写在
  `docs/OPERATIONS.md` 的「提供商配置」一节与 README 的同名一节。
