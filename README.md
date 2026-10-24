<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/VidDigest-AI%20视频理解与下载平台-7c3aed?style=for-the-badge&logo=github">
    <img alt="VidDigest" src="https://img.shields.io/badge/VidDigest-AI%20视频理解与下载平台-2563eb?style=for-the-badge&logo=github">
  </picture>
</p>

<p align="center">
  <a href="#-功能特性">功能</a> ·
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-安装指南">安装</a> ·
  <a href="#-使用指南">使用</a> ·
  <a href="#-配置">配置</a> ·
  <a href="#-常见问题">FAQ</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Vue_3-4FC08D?logo=vuedotgithub&logoColor=white" alt="Vue 3">
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/yt--dlp-1800+-blue" alt="yt-dlp 1800+">
  <img src="https://img.shields.io/badge/LLM-AI-8b5cf6" alt="LLM AI">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
</p>

<p align="center">
  <b>中文</b> · <a href="README.en.md">English</a>
</p>

---

<p align="center">
  <b>VidDigest</b> 是一个基于 yt-dlp + LLM 的全栈视频理解与下载平台。<br>
  粘贴链接即可获得 <b>AI 总结、思维导图、AI 问答</b>，同时支持 <b>1800+</b> 平台视频解析下载。
</p>

---

## ✨ 功能特性

| 功能 | 说明 | 免费 |
|------|------|:----:|
| 🧠 **AI 总结摘要** | LLM 自动分析字幕，生成结构化视频总结（概述、大纲、要点） | 每日 3 次 |
| 🗺️ **思维导图** | AI 自动提取知识结构，生成可交互的 SVG 思维导图 | 与总结共用 |
| 💬 **AI 问答** | 基于视频内容对话，追问细节、深入理解 | 每日 10 次 |
| 📝 **字幕导出** | 提取并导出 SRT / TXT 格式字幕 | 随解析 |
| 🎬 **视频解析下载** | YouTube、B站、抖音等 1800+ 平台，选择清晰度一键下载 | ✅ 不限次 |
| 🗂️ **社区浏览** | 公开社区：任何人可读他人已解析的总结、字幕、思维导图 | ✅ 不消耗额度 |
| 🕘 **解析历史** | 按关键词 / 标签 / 收藏筛选自己的解析记录 | ✅ |
| 🛠️ **管理后台** | 用户额度、管理员标记、社区审核、模型清单 | 仅管理员 |

> 视频解析下载无需登录，AI 功能需要注册账号。免费额度是**两个独立计数器**：
> 每日 3 次解析（产出总结 + 思维导图 + 标签）、每日 10 次追问；
> 读社区里别人已解析的内容**不消耗任何额度**。
> 具体数值由环境变量 `VIDDIGEST_DAILY_PARSE_LIMIT` / `VIDDIGEST_DAILY_CHAT_LIMIT` 控制。

---

## 🖼️ 界面预览

<table>
  <tr>
    <td width="50%" align="center"><img src="docs/screenshots/home.png" alt="首页：粘贴链接即可解析" /><br><sub>首页</sub></td>
    <td width="50%" align="center"><img src="docs/screenshots/home-dark.png" alt="暗色主题" /><br><sub>暗色主题</sub></td>
  </tr>
</table>

<p align="center">
  <img src="docs/screenshots/features.png" alt="核心功能" width="820" />
</p>

> 截图取自本机 `docker compose up -d --build` 跑起来的当前版本（1440×900）。

---

## 🚀 快速开始

```bash
# 后端（Windows；Linux/macOS 见安装指南）
cd backend
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env             # 配置 API Key（见下方）
venv\Scripts\python.exe main.py    # → http://localhost:8000

# 前端（新终端）
cd frontend
npm install
npm run dev                    # → http://localhost:5173
```

> ⚠️ **后端一律用 `venv\Scripts\python.exe`**，不要用 `python main.py`
> —— 那会用系统 Python，而本项目的依赖装在 venv 里。
> 也不要用 `source venv/Scripts/activate`（那是 Windows 的 activate 批处理，
> 在 Git Bash / Linux 下不适用）。

浏览器打开 **http://localhost:5173** → 粘贴视频链接 → 解析下载 / AI 总结。

> ⚠️ 前端用 `localhost` 而不是 `127.0.0.1`：Vite 只监听 IPv6 回环，
> `127.0.0.1:5173` 连不上。

### 🐳 或者用 Docker 跑

仓库自带 `docker-compose.yml`，一条命令起前后端（后端通过 `env_file` 读
`backend/.env` 里的密钥，镜像里不含任何密钥）：

```bash
docker compose up -d --build
curl http://127.0.0.1:8000/api/health     # → {"status":"ok"}
# 前端入口：http://127.0.0.1:8080
# 前端端口把 /api/ 反代到同一个后端，健康检查也一样通：http://127.0.0.1:8080/api/health
docker compose down                       # 停掉并删掉容器
```

> 停容器**不删数据**：`backend/data` 与 `backend/logs` 是 bind mount，
> SQLite 库文件留在宿主机上，容器内外看到的是同一份。

---

## 📦 环境要求

| 工具 | 最低版本 | 用途 |
|------|:--------:|------|
| Python | 3.11+ | 后端运行（开发实测 3.11.9） |
| Node.js | 20.19+ 或 22.12+ | 前端构建（Vite 8 的硬要求，**Node 18 装不上**） |
| npm | 10+ | 前端依赖管理 |
| ffmpeg | 任意 | 高清视频音画合并（强烈建议安装） |

> **ffmpeg** 用于合并 YouTube 等平台的视频+音频流。未安装时自动降级为单一格式。
> - Windows: 从 [ffmpeg.org](https://ffmpeg.org/download.html) 下载，将 `bin` 目录加入 PATH
> - macOS: `brew install ffmpeg`
> - Linux: `sudo apt install ffmpeg`

---

## 🔧 安装指南

### 后端

```bash
# Windows
cd backend
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env             # 编辑 .env 填入密钥
venv\Scripts\python.exe main.py
```

```bash
# Linux / macOS
cd backend
python3 -m venv venv
source venv/bin/activate           # 注意是 bin/activate，不是 Scripts/
pip install -r requirements.txt
cp .env.example .env
python main.py
```

> 改了 Python 代码要**重启进程**：`main.py` 没有 `--reload`。

### 前端

```bash
cd frontend
npm install
npm run dev                      # 开发模式 → http://localhost:5173
npm run build                    # 日常构建 → dist/
npm run release:build            # 发布用：先清空 dist/ 再构建（避免陈旧 bundle 上线）
```

生产构建产物需要**静态托管 + SPA rewrite**（否则刷新 `/admin` 得到 404）。
完整 Nginx / Caddy 配置与上线后自检见
[docs/OPERATIONS.md §11.5](docs/OPERATIONS.md#115-生产静态托管spa-rewrite)。

---

## ✅ 验证

第一次跑之前先装环境（两栈一起装）：`bash scripts/bootstrap.sh`
——它会建 `backend/venv`、装后端依赖，并在 `frontend/` 跑 `npm ci`。

本仓库只有一个门禁，它决定一次改动能不能算「做完了」：

```bash
bash ./init.sh          # 退出码 0 = 已验证
```

| # | 检查 | 它证明什么 |
|---|------|-----------|
| 1 | `backend: pytest` | 后端全部用例通过 |
| 2 | `backend: compileall` | 后端全部模块可编译 |
| 3 | `secrets: content-level scan` | 跟踪文件**与可达 git 历史**里没有凭据形态的字符串（`.gitignore` 只挡文件名，挡不住被抄进文档的口令） |
| 4 | `frontend: npm test` | 前端全部静态 / 契约断言通过（node:test） |
| 5 | `frontend: npm run test:mount` | 前端 199 条挂载层用例通过（Vitest）——两套 runner 刻意分开，绿色的那一层遮不住红的那一层 |
| 6 | `frontend: npm run typecheck` | `vue-tsc --noEmit` 无类型错误（`.js` 迁到 `.ts` 之后，类型检查比人眼审 53 个组件快得多） |
| 7 | `frontend: npm run lint` | ESLint 无告警（未使用的变量、无用转义、模板可访问名缺失都在这一关现形） |
| 8 | `frontend: npm run format:check` | Prettier 全部文件符合格式 |
| 9 | `refs: ADR 与路径引用一致性` | 文档与注释里提到的每个 `ADR NNNN`、每条源码路径都还指得到 |
| 10 | `gate: 关卡数口径一致性` | 本节写的关卡数就是门禁真跑的关卡数（从 `init.sh` 里数出来，不硬编码） |

关卡本身也要被验证：22 个变异脚本每次故意破坏一条不变量（鉴权边界、链接归一、
数据库夹具、支付回调、敏感内容扫描……），断言门禁会转红。详见
[docs/TESTING-STRATEGY.md](docs/TESTING-STRATEGY.md)。

---

## ⚙️ 配置

复制 `backend/.env.example` 为 `backend/.env`。模板里每一项都带注释，本节讲**哪些必填、填错会怎样**。

### AI 服务（不填就只有下载能用）

程序读**三个 key** 和两个百炼端点/模型变量，作用各不相同：

| 变量 | 用途 | 从哪拿 | 缺了会怎样 |
|------|------|--------|-----------|
| `ALIYUN_BAILIAN_API_KEY` | **主用**：摘要、思维导图、标签、追问（阿里云百炼 OpenAI 兼容模式） | [bailian.console.aliyun.com](https://bailian.console.aliyun.com/) → API-KEY 管理 | 走下面的兜底；两个都没有 ⇒ AI 功能直接报错 |
| `ALIYUN_BAILIAN_BASE_URL` | 端点，默认 `https://dashscope.aliyuncs.com/compatible-mode/v1` | 百炼控制台给的 workspace 专属地址 | 用默认值 |
| `ALIYUN_BAILIAN_MODEL` | 模型，默认取模型清单表里的 `bailian.default_model`（表里没配时回落 `qwen-plus`） | 同上 | 用默认值 |
| `DEEPSEEK_API_KEY` | **兜底**：百炼没配时用它 | [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys) | 同上 |
| `OPENAI_API_KEY` | **只给 Whisper ASR**：视频没有字幕时转写音频 | [platform.openai.com](https://platform.openai.com/) | 跳过 ASR，不影响其它 AI 功能 |

两个 key 都在时**百炼优先**（`backend/summarizer.py` 里读环境变量的顺序）。用户自己带凭据（BYOK）时，平台 key 不参与。

模板里的 `ALIYUN_BAILIAN_MODEL=qwen-turbo` 是**显式选择**：留空则走模型清单表里的 `bailian.default_model`。

### 其它必填项

| 变量 | 用途 | 缺了会怎样 |
|------|------|-----------|
| `JWT_SECRET` | Token 签名密钥，**必填、无兜底**（ADR 0010） | 进程在 import 阶段就拒绝启动 —— 不会拿兜底值凑合 |
| `VIDDIGEST_ALLOWED_ORIGINS` | 跨域白名单，逗号分隔；留空 = 完全不允许跨域（默认） | 前端同源访问不受影响 |
| `VIDDIGEST_ADMIN_EMAILS` | 逗号分隔的管理员账号 | 没人是管理员 |
| `VIDDIGEST_DAILY_PARSE_LIMIT` / `VIDDIGEST_DAILY_CHAT_LIMIT` | 每日免费额度，默认 3 / 10，各自独立、次日 0 点（UTC）重置 | 用默认值；**改它只改默认值，不清零已用额度** |

生成一个 JWT 密钥：

```bash
openssl rand -hex 32
```

### 支付（当前不启用）

`STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_PRICE_ID_MONTHLY` 只保留配置位：项目不做会员制，填了也不会被用。

> **真实值只进 `backend/.env`。** 它已被 `.gitignore` 挡住，但 `.gitignore` 是**文件名级**的 —— 它挡得住这个文件，挡不住「把口令抄进文档」。所以本仓还有一道内容级扫描（`scripts/check_secrets.py`，`init.sh` 第 4 关）扫工作树与全部可达 git 历史。模板 `backend/.env.example` 里刻意**不留任何具体凭据字符串**（连示例口令都没有），要值就现生成。

> 不配置 LLM API Key 时，视频下载功能仍可用，AI 功能不可用。

完整变量清单与生效方式见 [docs/OPERATIONS.md §4](docs/OPERATIONS.md)，逐项注释的模板见 [backend/.env.example](backend/.env.example)。

---

## 📖 使用指南

### 🎬 视频解析下载

```
① 打开首页 → 粘贴视频链接 → ② 点击「解析」
③ 在信息面板中选择清晰度 → ④ 点击「下载视频」
```

**支持的平台：**

| 平台 | 链接示例 |
|:----:|----------|
| Bilibili | `https://www.bilibili.com/video/BVxxx` |
| 抖音 | `https://v.douyin.com/xxx/` |
| YouTube | `https://www.youtube.com/watch?v=xxx` |
| Twitter/X | `https://x.com/user/status/xxx` |
| Instagram | `https://www.instagram.com/p/xxx/` |
| ... 1800+ 其他 | 几乎所有主流视频平台 |

### 🧠 AI 视频总结

注册登录后，解析视频 → 点击「AI 总结」→ 自动展示四个 Tab：

| Tab | 说明 |
|:---:|------|
| **总结摘要** | 结构化的视频概述、内容大纲、核心知识要点（流式显示） |
| **思维导图** | 可缩放拖拽的交互式 SVG 思维导图（基于 markmap） |
| **字幕文本** | 提取到的原始字幕，含时间戳 |
| **AI 问答** | 基于视频内容自由提问，AI 实时回答 |

### 💎 免费额度

额度按**成本性质**拆成两个计数器，各自每日重置（ADR 0003）：

| 用户类型 | AI 总结 | AI 追问 |
|:--------:|:--------:|:-------:|
| 未登录 | 0 次/日 | 0 次/日 |
| 注册用户 | **3 次/日** | **10 次/日** |

每个账号每日各 3 次解析、10 次追问，0 点重置。本项目不设会员制。

---

## 🏗️ 技术栈

| 层 | 技术 | 用途 |
|:--:|------|------|
| 前端 | Vue 3 + Vite + TailwindCSS 4 | SPA 框架、界面与流式渲染 |
| 后端 | FastAPI + Uvicorn | REST API + SSE 流式 |
| 视频解析 | yt-dlp + 抖音独立模块 | 1800+ 平台 |
| AI | OpenAI 兼容接口（DeepSeek / 阿里云百炼等） | 总结 / 导图 / 问答 |
| ASR 回退 | OpenAI Whisper API | 语音转文字（需配置） |
| 认证 | JWT + bcrypt | 用户鉴权 |
| 数据库 | SQLite | 用户与额度、订单、社区视频、解析历史、追问会话 |

---

## 🛟 常见问题

<details>
<summary><b>视频解析失败？</b></summary>

- **"无法解析该链接"** → `pip install --upgrade yt-dlp`
- **下载有画无声** → 安装 ffmpeg
- **抖音解析失败** → 确认分享的是短链接，稍后重试
</details>

<details>
<summary><b>AI 总结用不了？</b></summary>

- **"没有可用的字幕"** → 该视频没有字幕轨道，不支持 AI 总结
- **"今日次数已用完"** → 每账号每日 3 次，次日 0 点重置
- **"请先登录"** → 注册/登录后使用
- **无响应** → 检查 `.env` 中的 LLM API Key（`ALIYUN_BAILIAN_API_KEY` 或 `DEEPSEEK_API_KEY`）
</details>

<details>
<summary><b>数据库相关？</b></summary>

```bash
# 重置数据库（删除所有用户和订单数据）
rm -f backend/data/app.db     # 重启后端后自动重建

# 查看数据
sqlite3 backend/data/app.db "SELECT * FROM users;"
sqlite3 backend/data/app.db "SELECT * FROM orders;"
```
</details>

<details>
<summary><b>更多帮助</b></summary>

访问 [docs/](docs/) 查看完整 API 文档、部署指南和架构说明。
</details>

---

## 🤝 贡献

欢迎提交 Issue 和 PR。开发指南见 [docs/](docs/)。

## 📄 License

[MIT](LICENSE)

---

<p align="center">
  <sub>Built with Vue 3 + FastAPI + yt-dlp + LLM</sub>
</p>
