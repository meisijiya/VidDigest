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

## ⚙️ 配置

复制 `backend/.env.example` 为 `backend/.env`，填入以下内容：

```env
# ── AI 服务（必填）───────────────
DEEPSEEK_API_KEY=sk-your-key     # 从 platform.deepseek.com 获取

# ── JWT 密钥（必填，无默认值）──────
# 漏配时进程直接拒绝启动，不会用兜底值凑合。
JWT_SECRET=your-random-secret-32chars+

# ── 支付（可选）──────────────────
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRICE_ID_MONTHLY=

# ── 前端地址 ─────────────────────
FRONTEND_URL=http://localhost:5173
```

> 不配置 LLM API Key 时，视频下载功能仍可用，AI 功能不可用。

完整的配置说明见 [docs/](docs/)。

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

| 用户类型 | AI 总结次数 |
|:--------:|:-----------:|
| 未登录 | 0 次/日 |
| 注册用户 | **3 次/日** |

每个账号每日 3 次，0 点重置。本项目不设会员制。

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
