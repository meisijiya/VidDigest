# VidDigest 测试策略

本文档说明：测什么、每类用哪种测试、覆盖目标定在哪、示例用例怎么写、现有覆盖缺什么。

**数据来源**：本文件所有数字都是实测得到的，不是估算。复现命令见文末「如何复现本文数据」。
采集日期对应的基线：`./init.sh` 退出 0，后端 373 passed，前端 91 pass，工作树干净。

---

## 0. 先读这一段：本文数字的三个局限

策略文档最怕的是拿一个不可信的覆盖率数字去定目标。所以先把局限讲在前面。

### 0.1 这是行覆盖，不是「代码质量」

1859 条可执行语句行、967 行被执行、**52.0%**。这个数字里：

- **数字偏低的部分是真的**：整块能力从未被任何测试碰过（见第 5 节清单）。
- **数字精确的部分也是真的**：分母由 AST 节点起始行得出，不含注释、docstring、续行。

但 52.0% **不能**被解读成「测试覆盖了一半」。它混合了两类完全不同的东西：

| 类别 | 例子 | 该不该追 |
|---|---|---|
| 可离线测的编排与数据逻辑 | 额度计算、FTS 查询、并发占位行、凭据脱敏 | **应该追到高覆盖** |
| 必须真网/真模型的 I/O | `douyin.py` 16 个函数、`_extract_bilibili` | 追覆盖率是浪费，应改用契约桩 |

一个更诚实的说法是：**可测的那部分覆盖得不错，不可测的那部分是 0**。策略的重点因此是前者守住、后者换测法。

### 0.2 函数级分母被高估，`X/Y` 里的 Y 偏大

报告里函数级的 `X/Y`（`Y` = 命中行 / 函数体物理行范围）会**系统性低估真实覆盖率**，因为分母把多行字符串字面量的物理行算了进去。

实测证据 —— `database.init_db` 显示 5/115：

```
backend/database.py:197  def init_db():
backend/database.py:199      with get_db() as conn:
backend/database.py:200          conn.executescript("""      ← 从这里到 311 是一整段 SQL 字符串字面量
backend/database.py:201              CREATE TABLE IF NOT EXISTS users (
...
```

`executescript` 里那 ~110 行是 **SQL 文本，不是 Python 语句**，永远不会产生 `line` 追踪事件。所以 `init_db` 的真实情况是「几乎全执行」，不是 5%。

**结论**：凡涉及多行 SQL / 长 prompt / 模板字符串的函数（`init_db`、`_build_full_prompt` 7/48、`quota_limit` 1/9 等），`Y` 不可信，**不要照着数字定优先级**。定性结论（这个函数有没有被测过）仍然可信，定量结论不可信。

### 0.3 三个变异装置不在门禁里

仓库里跟踪着三个变异装置：

| 文件 | 是否被门禁收集 |
|---|---|
| `backend/tests/mutation_check.py` | ❌ 不匹配 `test_*.py`，pytest 默认不收集 |
| `backend/tests/mutation_check_quota.py` | ❌ 同上 |
| `frontend/tests/mutation_wiring.mjs` | ❌ 不匹配 `*.test.mjs`，`npm test` 不收集 |

它们是**人工按需运行**的装置，不是回归测试。含义有二：

1. 不要把「变异全杀」当成 CI 已有的护栏 —— 它没有自动化，每次都要人手动跑。
2. 不要因为它们在 `tests/` 目录下就以为门禁在跑它们。**目录位置不决定收集行为，文件名模式才决定。**

---

## 1. 现状快照

| 项 | 数值 |
|---|---|
| 后端用例 | 373 passed（14 个 `test_*.py`） |
| 前端用例 | 91 pass（5 个 `*.test.mjs`） |
| 门禁检查项 | 3 条（pytest / compileall / npm test） |
| 后端可执行语句行 | 1859 |
| 已执行行 | 967（**52.0%**） |
| 从未被执行的函数/方法 | **63 个** |
| 被执行但覆盖不全的函数 | 96 个 |

### 分模块行覆盖

| 模块 | 语句行 | 命中 | 覆盖率 | 评价 |
|---|---:|---:|---:|---|
| `tags.py` | 21 | 19 | 90.5% | 好 |
| `credentials.py` | 16 | 14 | 87.5% | 好（BYOK 是重点，值得） |
| `api_community.py` | 27 | 23 | 85.2% | 好 |
| `api_summarize.py` | 211 | 186 | 88.2% | 好 |
| `auth.py` | 52 | 41 | 78.8% | 表面好，**4 个密码函数全未测**（见 5.2） |
| `api_history.py` | 37 | 25 | 67.6% | 3 个读出口函数全未测 |
| `database.py` | 465 | 288 | 61.9% | 核心数据层，尚可 |
| `main.py` | 108 | 59 | 54.6% | **6 个主入口函数全未测**（见 5.1） |
| `summarizer.py` | 466 | 210 | 45.1% | 多为 I/O，见 0.1 |
| `api_auth.py` | 47 | 21 | 44.7% | 活跃但近乎裸奔 |
| `api_payment.py` | 59 | 17 | 28.8% | 活跃但近乎裸奔 |
| `downloader.py` | 125 | 22 | 17.6% | 真网 I/O |
| `douyin.py` | 225 | 42 | 18.7% | 真网 + WAF 对抗 |

**读法**：覆盖率最高的三块（tags / credentials / community）是最近三期工单主动建测试的地方，数字反映的是投入而非天然质量。反过来，**低覆盖率不等于低优先级** —— `api_auth.py` / `api_payment.py` 覆盖率低但代码是活的（见 5.3）。

---

## 2. 测试金字塔在本项目的映射

```
        /  冒烟 E2E  \        3 条，只跑门禁能覆盖的
       /  集成（HTTP 层）\    ~40 条，FastAPI TestClient 真路由
      /     单元 + 契约   \   ~330 条，纯函数与数据层
```

**这个项目的形状是倒过来的 —— 而且是对的。**

原因：本仓的绝大部分价值在「额度算得对不对、并发有没有串、FTS 能不能搜到、凭据有没有落盘」这类**判定逻辑**上，而不是在 HTTP 报文本身。HTTP 层用 `TestClient` 打进去成本很低，但它只能证明「路由通了」，证明不了「额度扣对了」。

所以：

- **单元层是承重层**，额度、并发、FTS、脱敏的判据全在这里。
- **集成层是接线层**，验的是依赖注入、鉴权、事务、真实客户端序列化。
- **E2E 只做冒烟**，且**永不引入真实第三方依赖**（真实 B 站、真实模型、真实 Stripe）—— 一旦引入，门禁就会因网络和凭据而变成不稳定信号，而不稳定信号会被习惯性忽略，等于门禁失效。

---

## 3. 分组件测什么、怎么测

### 3.1 额度与并发（最高优先级）

**风险**：这是本仓最容易出错、且出错后用户直接受损的地方。已知的两个真实坑：

- `daily_chat_count` 记的是**已用次数**（`+1`），`remaining` 才是 `-1`。写反了不会报错，只会让用户额度虚增一倍。
- 直接 `UPDATE` 额度而**不碰 `last_chat_date`**，跨天时会被重置逻辑覆盖掉。测试必须**反复调 `consume_quota`** 才能观察到真实行为，单次调用看不出这个问题。

**测法**：
- 单元：`consume_quota` / `check_quota_kind` / `quota_limit` 的表驱动测试，每个分支一条用例。
- 并发：`Barrier(2)` 放在**路由调用之前**，不是模型调用里。放错位置会死锁 —— 这是本仓已踩过的坑。
- **变异确认**：结构性约束（占位行 + 等待复用、唯一 `finally` 回滚）必须用变异装置确认真能变红。已验证 12 条变异全杀。

**不建议**：对 `quota_limit` 追行覆盖率（1/9 是分母虚高）。用**表驱动行为断言**代替。

### 3.2 社区搜索（FTS5 + trigram）

**风险**：SQLite 的 FTS5 不可用时**必须报错，不许降级成 `LIKE`**。降级了功能还在，但 trigram 分词能力没了 —— 症状是「某些中文查询搜不到」，极难定位。

**测法**：
- 单元：`search_community_videos` 的真实 SQLite 库测试（不是 mock）。
- 构造性：FTS5 不可用的环境里必须抛错。**这条要靠变异确认**（删触发器看测试是否转红）。
- 已验证：5 条变异全杀，包括 `bm25()` 缺表名、触发器被删。

### 3.3 追问会话（跨用户隔离）

**风险**：**假通过**。本仓的真实教训 —— 「用户 B 读不到 A 的记录」这条断言一度成立，原因是**谁的记录都读不到**（功能缺失），不是隔离生效。

**测法（强制）**：
- 每个写入者都要有一条**不依赖旧表任何行**的读路径。B 追问了 A 的视频，B 自己没解析过 —— 这条路径必须在测试里被独立走通。
- 隔离断言要**双向**：B 读不到 A 的是数据；B 读得到自己的是功能。两者都要。
- **变异确认**：打断读出口（返回空 / 恒返回常量）必须让测试转红。已验证 M6 变异仍红。

### 3.4 BYOK 凭据（安全边界）

**风险**：裸串泄漏。三条硬约束：**不进 URL、不进请求头、不进任何别的请求、不落盘**。

**测法**：
- 哨兵法：用一个任何服务商都不会签发的 `SENTINEL`，把所有抓到的请求摊开逐处搜。
- 源码层：`.vue` 模板与 `<script setup>` 都要查，且**要去掉注释**再查（注释里的说明不算代码）。
- 凭据路径**一条日志都不记** —— 用「抓全部日志 + 搜哨兵」断言。
- 已验证：3 条变异全杀。

**注意**：`UserCredential.from_secret` 的意义是**让裸串只存在于 `credentials.py` 内**。所以扫描测试要追**数据来源**（沿赋值/helper return 回溯到 `os.getenv`），而不是只扫函数源码里出现了什么名字 —— 只扫名字会被「把取值挪进一个 helper」这种最自然的重构绕过去。

### 3.5 前端

**现状约定**（`byok.test.mjs` 已写明，且应保持）：`.js` 里的行为**用假 fetch 真跑一遍**；`.vue` 只做**源码接线断言**。渲染结果验证需要挂载环境，超出本仓「`node --test` 零额外依赖」的约定。

这是有意的取舍，不是偷懒。它换来的是：`npm test` 在任何机器上都能跑，不需要装 jsdom/vue-test-utils。

**代价要认**：源码接线断言**守不住渲染正确性**。模板语法错、条件渲染漏一个分支，这类问题它在绿灯下依然存在。真要覆盖渲染，需要引入挂载环境 —— 那是一个单独的决策，不在本文档范围。

---

## 4. 覆盖目标

**不给单一数字目标。** 单一数字会逼着人去测不值得测的东西。

按风险分档：

| 档 | 范围 | 目标 | 判据 |
|---|---|---|---|
| **A · 承重** | 额度、并发占位、隔离、凭据脱敏、FTS 可用性 | 每个判定分支有对应用例；**每条结构性约束经变异确认能变红** | 变异装置全杀 |
| **B · 接线** | 全部 HTTP 路由、鉴权依赖、事务边界 | 每个路由至少一条正例 + 一条拒绝/错误例 | 路由清单 vs 测试清单逐条对账 |
| **C · 契约** | 真网 I/O（`douyin`、`downloader`、`_extract_bilibili`、Stripe） | **不追行覆盖**；改为对「我方契约」建测试：URL 识别、响应字段映射、错误分类 | 每个外部依赖至少一组 fixture 契约测试 |
| **D · 不测** | `__init__`、纯转发 getter、框架胶水、`features.js` 开关 | 不测 | — |

**关于 52%**：A + B 档的**行为**覆盖应当接近 100%，即使行覆盖数字停在 60% 出头。行覆盖只是 A/B 档是否做够了的**副产品**，不是目标本身。

---

## 5. 缺口清单（按优先级）

### 5.1 P0 —— 产品主入口零测试

**`main.py` 6 个函数从未被执行**：

| 函数 | 路由 | 行 |
|---|---|---:|
| `ParseRequest.clean_url` | — | 87 |
| `DownloadRequest.clean_url` | — | 97 |
| `parse_video` | `POST /api/parse` | 112 |
| `download_video` | `POST /api/download` | 132 |
| `get_direct_url` | `POST /api/direct-url` | 164 |
| `proxy_thumbnail` | — | 183 |

实测确认：`backend/tests/` 下**没有任何测试引用过** `/api/parse`、`/api/download`、
`/api/direct-url`、`/api/proxy/thumbnail`。（`/api/health` 是唯一被间接打到的。）

这是最严重的一条。**用户打开产品第一眼点到的功能，一行测试都没有。** 而 `parse_video` / `download_video` 恰好都有 `is_douyin_url` 分支、`run_in_executor` 分支、文件存在性检查、`except HTTPException: raise` 的异常透传 —— 每一条都是会静默出错的结构。

**建议补的用例**（`clean_url` 是纯函数，最容易测）：

注意 `ParseRequest` 的字段名是 `url`（不是 `video_url`），且 **`clean_url` 不去追踪参数** ——
它的真实契约是「从用户粘贴的分享文本里正则抽出第一个 URL」：

```python
# 1. clean_url 从分享文本里抽 URL —— 纯函数，零成本。
#    真实行为：https://v.douyin.com/abc/ 复制出来的分享文案前后带中文和表情
def test_clean_url_extracts_first_url_from_share_text():
    # 抽到 URL，且截断在中文标点前（「。」不在 URL 字符集里）
    assert ParseRequest(url="【标题】https://v.douyin.com/abc/ 快来看").clean_url() \
           == "https://v.douyin.com/abc/"
    # 抽不到时退化为原样 strip —— 这个分支同样值得钉住
    assert ParseRequest(url="  没有链接  ").clean_url() == "没有链接"
```

第 1 条的正则字符集 `[^\s）\)\"\'＞，。、；：！？》>\]]+` 刻意排除了中文标点，
所以「URL 后面跟一个逗号」是这个函数存在的**全部理由**。删掉那个字符集它就会转红。

```python
# 2. 分流：抖音 URL 走 douyin_parser，其余走 downloader
#    断点：把两边的 parse 都换成会记账的桩，断言各自被调了几次
def test_parse_routes_douyin_to_douyin_parser(client, stub_douyin, stub_downloader):
    r = client.post("/api/parse", json={"url": "https://v.douyin.com/abc/"})
    assert stub_douyin.calls == 1 and stub_downloader.calls == 0

# 3. 异常透传：download_video 里 HTTPException 必须原样抛出，
#    不能被 except Exception 吞成 400
def test_download_video_does_not_swallow_http_exception(...):
    # 把 FileResponse 前的 os.path.exists 打成 False → 应得 500 "下载的文件不存在"
    # 若被 except 吞掉会变成 400，说明 except HTTPException: raise 被删了
```

第 3 条是**变异友好**的：删掉 `except HTTPException: raise` 它就会转红。

### 5.2 P0 —— 认证链路活跃但裸奔

`auth.py` 4 个函数 + `api_auth.py` 2 个函数从未执行：

| 函数 | 路由/位置 | 行 |
|---|---|---:|
| `hash_password` | — | 17 |
| `verify_password` | — | 21 |
| `validate_email` | — | 46 |
| `validate_password` | — | 50 |
| `register` | `POST /api/auth/register` | 47 |
| `login` | `POST /api/auth/login` | 70 |

`auth.py` 78.8% 的行覆盖率具有**欺骗性**：高分来自 token 签发/解析（那部分确实测过），而**密码这一侧一行没测**。

**为什么是 P0 而不是 P1**：`api_auth.py` 行覆盖 44.7% 看着像「测了一部分」，实际是「`/me` 被测过、注册登录完全没测」。`login` / `register` 是 `frontend/src/api/auth.js` 的真实调用目标。

**示例用例**：

```python
# 密码哈希用 bcrypt.gensalt()：同一密码两次哈希必须不同（随机盐），
# 但都能 verify 通过
def test_hash_is_salted_and_verifiable():
    a, b = auth.hash_password("pw"), auth.hash_password("pw")
    assert a != b, "同一密码两次哈希相同 = 没加盐"
    assert auth.verify_password("pw", a) and auth.verify_password("pw", b)
    assert not auth.verify_password("wrong", a)

# validate_password 返回的是**错误文案或 None**，不是布尔。
# 钉住两个边界：6 位（含）和 51 位
def test_validate_password_bounds():
    assert auth.validate_password("a" * 6) is None
    assert auth.validate_password("a" * 5) == "密码长度不能少于 6 位"
    assert auth.validate_password("a" * 51) == "密码长度不能超过 50 位"

# 登录失败不泄露是哪个字段错了（钉住当前已有的正确行为）
# 现状：邮箱不存在与密码错误都抛 400 + 同一句「邮箱或密码错误」
def test_login_does_not_leak_which_field_was_wrong(client):
    r1 = client.post("/api/auth/login", json={"email": "nobody@x.com", "password": "pw"})
    r2 = client.post("/api/auth/login", json={"email": "real@x.com",  "password": "bad"})
    assert r1.status_code == r2.status_code == 400
    assert r1.json()["detail"] == r2.json()["detail"] == "邮箱或密码错误"
```

最后一条的价值是**防回归**：把两处 400 拆成「邮箱不存在」/「密码错误」两种文案，
是一个看起来无害的重构，却会立刻造成用户名枚举。这条测试能拦住它。

`validate_password` 那条防的是 off-by-one：把 `len(password) > 50` 改成 `>= 50`，
用户就在恰好 50 位时被拒。这类改动不会报错，只会让一个边界值莫名其妙登不上去。

**这两个函数不是死代码**：`register`（`api_auth.py:49–53`）逐个调用它们，
且 `validate_password` 的返回值**直接进 400 响应体**（`raise HTTPException(detail=err)`）。
文案一旦变了就是对外可见的 API 变更 —— 钉住它不是洁癖。

### 5.3 P1 —— 支付链路活跃但裸奔

`api_payment.py` 4 个函数全部从未执行：

| 函数 | 路由 | 行 | 规模 |
|---|---|---:|---:|
| `_generate_order_no` | — | 31 | 3 行 |
| `create_checkout_session` | `POST /api/payment/create-checkout` | 39 | **54 行** |
| `stripe_webhook` | `POST /api/payment/webhook` | 102 | **32 行** |
| `list_orders` | `GET /api/payment/orders` | 140 | 5 行 |

配套的 `database.py` 也全未测：`create_order`(874)、`update_order_stripe_session`(883)、**`complete_order`(891, 48 行)**、`get_user_orders`(942)。

**为什么是 P1 不是「直接删」**：`frontend/src/config/features.js` 明确写着「不做会员制，但后端 VIP 判定与 Stripe 支付代码**保留不删**」，而 `App.vue:341` 仍在调 `create_checkout_session`。**它是活的、但零测试的。**

这里测试的价值不在于「测得好」，而在于**钉住它当前的行为**。`stripe_webhook`（32 行，处理外部回调）尤其需要最少限度的一条：**签名校验失败必须拒绝**。

```python
# 1. 签名无效必须拒绝 —— 当前返回 400 "Invalid signature"
#    注意它依赖环境变量 STRIPE_WEBHOOK_SECRET，测试要先把它设上，
#    否则会在更早的分支就返回 "Webhook secret not configured"（也是 400，但理由不同）
def test_webhook_rejects_bad_signature(client, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    r = client.post("/api/payment/webhook",
                    content=RAW_BODY,
                    headers={"Stripe-Signature": "t=1,v1=deadbeef"})
    assert r.status_code == 400
    assert r.json()["error"] == "Invalid signature"
```

⚠️ 这条有个真实的坑：**签名未配置和签名无效都返回 400**，只靠状态码分辨不出
「因为没配 secret」和「因为签名错了」。断言必须落在 `error` 文案上。

```python
# 2. 幂等：同一个 checkout.session.completed 重放两次，complete_order 只能生效一次。
#    幂等性在 database.complete_order 里（只处理 pending 状态），不在路由里——
#    所以这条要断言**效果**（VIP 到期时间没被刷新成第二次的时间），
#    而不是断言 complete_order 被调了几次。
def test_replayed_webhook_does_not_extend_membership_twice(...):
    # 第一次：VIP 到期 = T1
    # 重放同一事件
    # 断言：到期时间仍是 T1，且订单 status 仍是 paid 而非二次更新
```

第 2 条是变异友好的：把 `complete_order` 里的 `status == "pending"` 判断删掉，
重复回调测试就会转红。**这类「幂等」断言必须断在效果上**——断调用次数的话，
把 `complete_order` 换成恒返回 None 也能全绿。

### 5.4 P1 —— 历史读出口未测

`api_history.py` 3 个函数从未执行：`list_history`(30)、`save_history`(36)、`remove_history`(102)。

与 3.3 呼应 —— 工单 #8 的教训正是「**新表的读出口按『谁在读』枚举，不要按『数据在哪张表』枚举**」。这三个函数就是历史相关的读出口，目前无测试。

### 5.5 P2 —— C 档：真网 I/O 换测法

`douyin.py`（16 函数全未执行）、`downloader.py`（7）、`summarizer.py`（13）合计 46 个函数。**不要为它们追覆盖率。**

正确做法是对**我方契约**建 fixture 测试：

| 目标 | 测什么 |
|---|---|
| `douyin.is_douyin_url` | URL 识别纯函数，值得测（7 行） |
| `douyin._decode_b64` | b64 解码纯函数（9 行） |
| `douyin._fmt_duration` | 时长格式化（4 行） |
| `DouyinParser._extract_video_id` | 从分享链接抽 ID 的规则（21 行） |
| `douyin._build_result` | 字段映射契约（45 行，最值得） |
| `summarizer._parse_vtt` | VTT 解析（36 行） |
| `summarizer.clean_mindmap_markdown` | 思维导图清洗（32 行，**当前零测试**） |
| `summarizer._time_to_seconds` | 时间解析（7 行） |

判据很简单：**这个函数的输入输出是确定的吗？** 是 → 用 fixture 测。否（真网、真 WAF 对抗、真模型流）→ 只测它在失败时怎么归类错误。

`clean_mindmap_markdown` 特别值得优先：它是**纯函数**（32 行正则处理），却零测试，而且它的输出直接决定前端 markmap 能不能渲染 —— 错了是「思维导图空白」，用户看不出原因。

### 5.6 P3 —— 部分执行的高价值函数

按「值不值得补」排序（非按覆盖率排序）：

| 函数 | 命中/范围 | 判断 |
|---|---|---|
| `database.publish_video_card` | 8/37 | 值得。卡片「先到先得」语义（ADR 0006）复杂 |
| `database.search_community_videos` | 21/47 | 值得，搜索是核心功能 |
| `database.reserve_video` | 22/47 | 值得，并发语义的承重墙 |
| `tags.validate_tags` | 10/25 | 值得，词表校验是 ADR 0005 |
| `database._migrate_video_card_columns` | 4/21 | 值得，迁移代码出错不可逆 |
| `summarizer._build_full_prompt` | 7/48 | 追覆盖率无意义（分母虚高），改为断言**关键约束**：字幕不外传、去重逻辑 |
| `database.quota_limit` | 1/9 | 同上，改表驱动行为测试 |
| `database._fts_phrase` | 1/13 | 值得，FTS 短语转义，错了会 SQL 注入或搜不到 |

---

## 6. 示例用例的写法要求

本仓已有的三条纪律（源自已关闭工单的复审 FAIL），补测试时必须遵守：

### 6.1 断言外部可观察行为

不测私有函数、不断言内部调用顺序、不绑实现细节。
判断标准：**功能整体坏掉时，它还会绿吗？**

### 6.2 结构性约束用变异确认

声称「这条测试守住了 X」的，必须做一次变异（把 X 改坏）确认它转红。
**注意变异判据**：`pytest` 的 exit 5 = **一条测试都没选中**，不等于「测试失败」。脚本里要显式检查 `ran > 0`：

```python
if ran == 0:
    verdict = "NO-TESTS-RAN"   # 不是 KILLED
```

`-k` 表达式里不要粘类名 —— 类名含 `as` / `and` / `or` / `in` 等 Python 关键字时会被当关键字解析，静默选出 0 条。

### 6.3 新表读出口按「谁在读」枚举

每个写入者配一条**不依赖旧表任何行**的读路径。

### 6.4 接缝优先

用 `tests/seams.py` 已建好的三条接缝，别绕过：

1. `StubSummarizer` —— **按方法分别计数**。`calls`（总和）不能单独用来断言「某方法没被调用」。
2. `close_all_thread_connections()` —— 路由层用 `run_in_executor`，连接按线程缓存。
3. `make_client()` —— 鉴权依赖必须**真的**被解析。直接调路由函数时 `user` 拿到的是依赖对象本身，「传空用户测 401」只是手写了依赖返回值。

---

## 7. 不做什么

- **不引入真实第三方依赖**（真 B 站 / 真模型 / 真 Stripe）。门禁必须任何机器都能跑。
- **不为 C 档 I/O 追覆盖率。**
- **不测 `__init__`、纯转发、框架胶水。**
- **不把变异装置并入门禁**（当前是人工按需运行，这是有意的）。若要自动化，需要独立的 nightly job，不能塞进 PR 门禁 —— 变异运行慢，且会改写生产源码，与并行开发冲突。
- **不新增覆盖率工具依赖**。当前用 `sys.settrace` 零依赖收集，是为了让盘点不污染 `requirements.txt`。

---

## 如何复现本文数据

```bash
# 1. 基线（唯一门禁入口）
& 'C:\Program Files\Git\bin\bash.exe' ./init.sh     # 期望：373 passed / 91 pass / 退出 0

# 2. 用例盘点
backend\venv\Scripts\python.exe .scratch\test_inventory.py

# 3. 行覆盖收集（零依赖，产物 .scratch/lines.json）
backend\venv\Scripts\python.exe .scratch\cov_plugin.py

# 4. 覆盖报告（分模块 + 从未执行的函数）
$env:PYTHONIOENCODING='utf-8'
backend\venv\Scripts\python.exe .scratch\cov_report.py
```

⚠️ 第 3 步会**就地改写生产代码**（变异/trace 装置），必须独占工作树，不要与其它进程并行跑。

**若要更准的覆盖率，装 `pytest-cov` 重新测。** 本仓的自制收集器分母口径见第 0.2 节。
