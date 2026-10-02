# AGENTS.md

VidDigest —— 通用视频解析 / 下载 + AI 字幕总结工具。
`backend/` FastAPI（Python 3.11）+ `frontend/` Vue 3 + Vite。两个进程独立启动，没有 monorepo 编排。

## 环境事实（实测，勿凭猜测推翻）

- 后端解释器**必须**用 `backend\venv\Scripts\python.exe`（3.11.9），系统 Python 不对。
- `backend/main.py` 用 `uvicorn.run(app, ...)`，**没有 `--reload`**——改 Python 代码必须重启进程。
- 前端 Vite 只监听 IPv6 回环：一律用 `http://localhost:5173`，`127.0.0.1:5173` 连不上。
- 真实凭据只在 `backend/.env`（已 gitignore）。**任何时候不要 cat 出来贴进文档、提交或日志。**
- Windows 上 PowerShell 会静默改写内联脚本里的中文 / 引号 / JSON。含中文、多行、引号的一次性脚本一律先 `write` 成 `.py` / `.cjs` 再执行，不要用 `python -c` / `node -e` 内联。

## 启动工作流

1. 读下面「环境事实」与「工作规则」
2. `bash ./init.sh` 确认基线是绿的——**基线红着就不要开工**，先修基线
3. `gh issue list --state open` 挑**一张**阻塞边清楚的工单
4. 只实现那一张（`/implement`）。别在同一轮里开第二张
5. 收尾前重跑 `bash ./init.sh`；退出 0 才能在工单里留证据

## 验证命令

**唯一门禁入口是 `init.sh`**，不是本节，不是 `CONSTRAINTS.md`，也不是 CI 之外任何东西。

```bash
& 'C:\Program Files\Git\bin\bash.exe' ./init.sh
```

它依次跑三件**真会失败**的事：后端 pytest（用 `backend\venv\Scripts\python.exe`）、后端 compileall、前端 `npm test`。
单独调试时可以只跑其中一条，但**声称完成前跑的一定是 `./init.sh` 整条**，且要是本次跑出来的。

## 状态与交接：承接方是工程 skill

- **工程流程归 `mattpocock`** 这一套 skill，由它按阶段分派。

- **状态与阻塞边 → `to-tickets`**，落在 GitHub Issues（见 `docs/agents/issue-tracker.md`）
- **会话交接 → `handoff`**
- **本文件不记录任何进度状态。** 状态指针会过期，而指令文件里的过期指针会被**执行**，不是被阅读。
- 没装这套 skill 就先跑 `/setup-matt-pocock-skills`

## Agent skills

### Issue tracker

Issues and specs live in this repo's GitHub Issues, driven through the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, using the default label strings verbatim. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` at the repo root, ADRs under `docs/adr/`. See `docs/agents/domain.md`。

## 社区 / BYOK 功能

`CONTEXT.md` 与 `docs/adr/0001`–`0006` 记录了 2026-10-01 grill 敲定的社区/BYOK 设计。
动这块前先读那两份文档，别重新推导已经定过的东西。

**实现进度不要从本文件读**——工单会一张张关掉，本文件不会跟着变。判据是工单自身：

- 哪些还没做：`gh issue list --state open`
- 哪些做完了：`gh issue list --state closed`，并在 `git log` 里找到对应提交
- **别信票面标题，也别信本文件**——跑一遍测试，看它声称的行为是不是真守住了

## 工作规则

- **一次一个工单。** 同一时间只有一个活动工单。
- **多代理必须先划所有权边界，写入范围具体到文件。** 写同一批文件的多条并行线不是并行，是并行写冲突。
  委派时把范围写进 brief，并要求 worker 报告它**没碰**哪些越界文件。越权就报告缺口、交回主代理，不要自己扩张。
- **别信票面标题。** 票面可能与代码实际状态不符——工单 #3 就是：票面说「调整字幕优先级顺序」，
  而代码里顺序早已正确，真正的缺口在静默落穿与零测试覆盖。**动手前先读相关代码。**
- **测试只断言外部可观察的行为。** 不测私有函数、不断言内部调用顺序、不绑实现细节。
- **接缝优先于内部改动。** 桩按方法分别计数、HTTP 层真实客户端这两条接缝是工单 #2 建的，用它们，别绕过。
- 改 Python 代码必须重启后端进程（`main.py` 没有 `--reload`）。

## 范围边界

- 领域定义在 `CONTEXT.md`，设计决策在 `docs/adr/0001`–`0006`。动社区/BYOK 之前先读，别重新推导已定过的东西。
- **明确不做**：会员制、点赞/收藏/评论/关注、内容审核与举报、视频下载功能本身的改动、推荐算法与个性化排序、移动端原生应用、多语言界面、社区视频的删除与下架。
- 会员判定与相关额度语义**留在代码里不动**（前端入口已关），但**新增测试不得锁会员行为**。
- 社区视频表**不做条数裁剪**；解析历史表的 30 条滚动删除规则不变。

## 完成定义

一张工单满足以下**全部**条件才算完成：

1. `bash ./init.sh` 退出 0，且是**本次跑出来的**（不是引用旧记录、不是"应该没问题"）
2. 工单里留了证据：命令 + 结果摘要 + 退出码，或 CI 链接
3. 没有顺手改掉范围边界之外的东西
4. 其它工单不被这次改动破坏（全量测试仍然绿）

**无证据不得标记完成。**「声称完成但测试没过」是头号失败模式。
子代理说「已修好」不算证据——自己看 diff、自己跑命令。

## 会话结束

- 跑 `bash ./init.sh`，如实报告结果（过了就说过了，没过就说没过，附完整输出与退出码）
- 状态更新写进工单，**不写进本文件**
- 需要跨会话交接时用 `handoff`，不要手写交接文件

## 升级处理

- **门禁红了**：先定位**一个**根因，查一次完整堆栈。别一次派多个代理同时查同一批红测试。
- **测试红了且根因不明**：读完整堆栈，不要边猜边改。
- **收到复审意见**：先核实，再有依据地反驳或记为显式延后。不要表演性赞同，也不要盲目照改。
- **大面积机械改动**（如批量替换、格式重写）：先在一次性脚本里做，改完逐个核对，不要用管道直接覆写源文件。

## 必需产物

本仓应留下：

- [x] `AGENTS.md`（本文件，工程档，承接方已具名）
- [x] `init.sh`（可执行门禁，三项检查都是真命令，不存在占位）
- [x] `CONTEXT.md` + `docs/adr/`（领域词汇与设计决策）
- [x] `docs/agents/`（tracker、标签、领域三份约定）

状态与交接产物**不在此列**——它们归 `to-tickets` 与 `handoff`，本文件不代建。
