<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/VidDigest-AI%20视频理解与下载平台-7c3aed?style=for-the-badge&logo=github">
    <img alt="VidDigest" src="https://img.shields.io/badge/VidDigest-AI%20视频理解与下载平台-2563eb?style=for-the-badge&logo=github">
  </picture>
</p>

<p align="center">
  <b>English</b> · <a href="README.md">中文</a>
</p>

<p align="center">
  <a href="#-what-it-does">What it does</a> ·
  <a href="#-quick-start">Quick start</a> ·
  <a href="#-architecture">Architecture</a> ·
  <a href="#-testing--verification">Testing</a> ·
  <a href="#-configuration">Configuration</a> ·
  <a href="#-design-decisions">Decisions</a>
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
  <b>VidDigest</b> is a full-stack video understanding and download platform built on
  <b>yt-dlp + LLM</b>. Paste a link and you get an <b>AI summary, a mind map and AI Q&amp;A</b>;
  download from <b>1800+</b> video platforms in the same flow.
</p>

> This file is the English entry point. [README.md](README.md) is the primary document and
> carries the same content in Chinese.

---

## ✨ What it does

| Feature | Description | Free tier |
|--------|-------------|:--------:|
| 🧠 **AI summary** | The LLM analyses the subtitles and produces a structured summary (overview, outline, key points) | 3 per day |
| 🗺️ **Mind map** | An interactive SVG mind map extracted from the video's knowledge structure | Shared with the summary |
| 💬 **AI Q&amp;A** | Ask follow-up questions about the video, answered from its content | 10 per day |
| 📝 **Subtitle export** | Extract and export subtitles as SRT / TXT | Included |
| 🎬 **Parse &amp; download** | YouTube, Bilibili, Douyin and 1800+ other platforms, pick a quality and download | ✅ Unlimited |
| 🗂️ **Community** | Read summaries, subtitles and mind maps other people already generated | ✅ Free |
| 🕘 **Parse history** | Search, tag and favourite your own parse records | ✅ |
| 🛠️ **Admin console** | Quota management, admin flags, community moderation, model catalogue | Admins only |

> Parsing and downloading do not require an account; the AI features do. The free tier is
> **two independent counters**: 3 parses per day (a parse yields summary + mind map + tags)
> and 10 follow-up questions per day. Reading the community consumes neither. The numbers
> come from `VIDDIGEST_DAILY_PARSE_LIMIT` / `VIDDIGEST_DAILY_CHAT_LIMIT`.

---

## 📦 Requirements

| Tool | Minimum | Used for |
|------|:--------:|----------|
| Python | 3.11+ | Backend (developed and tested on 3.11.9) |
| Node.js | 20.19+ or 22.12+ | Frontend build — a hard requirement of Vite 8 (**Node 18 cannot install it**) |
| npm | 10+ | Frontend dependencies |
| ffmpeg | any | Merging separate video and audio streams (strongly recommended) |

> **ffmpeg** merges the video and audio streams that DASH sources (Bilibili, YouTube) ship
> separately. Without it the downloader falls back to a single stream.
> Windows: [ffmpeg.org](https://ffmpeg.org/download.html) → add `bin` to PATH ·
> macOS: `brew install ffmpeg` · Linux: `sudo apt install ffmpeg`

---

## 🚀 Quick start

```bash
# Backend (Windows; Linux/macOS below)
cd backend
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env                # fill in your keys (see Configuration)
venv\Scripts\python.exe main.py       # → http://localhost:8000

# Frontend (another terminal)
cd frontend
npm install
npm run dev                           # → http://localhost:5173
```

```bash
# Backend on Linux / macOS
cd backend
python3 -m venv venv
source venv/bin/activate              # bin/activate, not Scripts/
pip install -r requirements.txt
cp .env.example .env
python main.py
```

> ⚠️ **Always run the backend with the venv interpreter** (`venv\Scripts\python.exe` on
> Windows, `venv/bin/python` on Linux/macOS). A bare `python main.py` uses the system
> interpreter, where the dependencies are not installed.
>
> ⚠️ Use `localhost`, not `127.0.0.1`, for the frontend: Vite listens on the IPv6 loopback
> only, so `127.0.0.1:5173` does not connect.

Open **http://localhost:5173**, paste a video link, then parse / summarise / download.

> Restart the backend after editing Python code — `main.py` runs Uvicorn without `--reload`.

---

## 🏗️ Architecture

```
browser (Vue 3 + Vite)
  │  relative /api calls — same origin in dev (Vite proxy) and in production (Nginx)
  ▼
FastAPI (Uvicorn)                     40 REST endpoints, 29 of them behind an auth dependency
  ├─ api_auth / api_history / api_community / api_payment / admin_api
  ├─ api_summarize ──► SSE streaming (text/event-stream) for summary and Q&A
  ├─ downloader / douyin ──► yt-dlp and a dedicated Douyin module
  ├─ summarizer ──► LLM calls (OpenAI-compatible API)
  └─ database ──► SQLite via the Python standard library (WAL mode)
```

| Layer | Technology | Notes |
|:----:|-----------|-------|
| Frontend | Vue 3 + Vite + Tailwind CSS 4 | SPA, streaming rendering, two test runners |
| Backend | FastAPI + Uvicorn | REST API plus SSE for the AI features |
| Video parsing | yt-dlp + a dedicated Douyin module | 1800+ platforms; parsing is **not** re-implemented here |
| AI | OpenAI-compatible API (DeepSeek, Alibaba Bailian, …) | Summary, mind map, Q&A |
| ASR fallback | Whisper API | Only when a video has no subtitle track |
| Auth | JWT + bcrypt | Token lifetime 72 h; admin rights are re-read from the database per request |
| Database | SQLite (standard library, WAL) | Users and quotas, orders, community videos, parse history, chat sessions |

Design decisions worth reading are recorded as ADRs — see [Design decisions](#-design-decisions).
Two examples that shape most of the code:

- **ADR 0006** — the first parse of a video wins: a unique index arbitrates concurrent
  parses of the same URL, so a video is never parsed twice and never billed twice.
- **ADR 0004** — a user-supplied API key (BYOK) is forwarded to the provider for that
  request only; it is never written to the database.

---

## ✅ Testing &amp; verification

The repository has a single gate. It is the only thing that decides whether a change is
allowed to be called done:

```bash
bash ./init.sh          # exit code 0 = verified
```

| # | Check | What it proves |
|---|-------|----------------|
| 1 | `backend: pytest` | 1261 backend tests pass (pytest, standard-library sqlite fixtures) |
| 2 | `backend: compileall` | Every backend module compiles |
| 3 | `secrets: content-level scan` | No credential-shaped string in tracked files **or in reachable git history** — `.gitignore` is filename-level and does not stop a pasted key |
| 4 | `frontend: npm test` | 496 static / contract assertions (node:test) |
| 5 | `frontend: npm run test:mount` | 191 mounted-component tests (Vitest) — a separate runner on purpose, so a green layer cannot hide a red one |
| 6 | `refs: ADR 与路径引用一致性` | Every `ADR NNNN` reference and every source path mentioned in docs or comments still resolves |
| 7 | `gate: 关卡数口径一致性` | The number of checks this README claims is the number the gate really runs — counted from `init.sh`, never hard-coded |

Two properties make the gate trustworthy rather than decorative:

- **Mutation testing.** 16 mutation scripts deliberately break one invariant at a time
  (auth boundary, canonical URLs, database fixtures, payment webhooks, secret scanning,
  …) and assert that the gate turns red. A check that never turns red is not a check.
- **The gate checks itself.** Checks 6 and 7 exist because the instructions, the ADRs,
  the test strategy and the source comments point at each other, and a stale pointer
  turns no test red — it just keeps constraining the next reader. One real instance:
  a source comment referenced the wrong ADR, and nothing noticed for a long time.

```bash
# one mutation script, e.g. the secret-scan gate
./backend/venv/Scripts/python.exe scripts/mutation_secrets_gate.py
```

Further reading: [docs/TESTING-STRATEGY.md](docs/TESTING-STRATEGY.md) (strategy, coverage
gaps, mutation harness) and [docs/OPERATIONS.md](docs/OPERATIONS.md) (runtime facts).

---

## ⚙️ Configuration

Copy `backend/.env.example` to `backend/.env` and fill in what you need. **No secret has a
default value**: the process refuses to start when `JWT_SECRET` is missing rather than
falling back to something guessable.

| Variable | Required | Purpose |
|----------|:--------:|---------|
| `JWT_SECRET` | ✅ | Token signing key — the process refuses to start without it |
| `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `ALIYUN_BAILIAN_API_KEY` | for AI features | LLM provider key |
| `VIDDIGEST_ALLOWED_ORIGINS` | no | Explicit CORS allow-list; empty means no cross-origin access at all (the default) |
| `VIDDIGEST_ADMIN_EMAILS` | no | Comma-separated list of accounts seeded as admins |
| `FRONTEND_URL` | no | Frontend origin used in redirects and CORS decisions |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_PRICE_ID_MONTHLY` | no | Payments |

> Without an LLM key, parsing and downloading still work; the AI features do not.

---

## 📖 Documentation

| Document | Contents |
|----------|----------|
| [docs/API.md](docs/API.md) | Complete endpoint list with auth requirements |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Runtime facts, deployment, static hosting, monitoring points |
| [docs/TESTING-STRATEGY.md](docs/TESTING-STRATEGY.md) | Test strategy, coverage gaps, mutation harness |
| [docs/AI功能核心技术说明.md](docs/AI功能核心技术说明.md) | How the AI pipeline works (Chinese) |
| [GLOSSARY.md](GLOSSARY.md) | Domain vocabulary |
| [docs/adr/](docs/adr/) | Architecture decision records |

---

## 🧭 Design decisions

Each ADR records a decision that was hard to reverse, surprising without context, and the
result of a real trade-off.

| ADR | Decision |
|-----|----------|
| 0001 | Community videos live in their own global table, not in personal parse history |
| 0002 | Subtitle text is stored together with the community video |
| 0003 | Parse quota and chat quota are separate counters |
| 0004 | A user-supplied API key is forwarded by the server but never stored |
| 0005 | Tags come from a hand-maintained fixed vocabulary; the model may not invent them |
| 0006 | The first parse of a video wins — no overwrite, no double billing |
| 0007 | The owner of a parse may regenerate their own summary; later visitors may not |
| 0008 | Visual identity: graphite + lake blue (the blue ramp is no longer remapped to violet) |
| 0009 | Light and dark themes override CSS variables instead of introducing semantic tokens |
| 0010 | Admin console: an `/admin` route in the same Vue app, read + quota scope, no account lifecycle |
| 0011 | The model catalogue lives in the database; platform and user keys stay in `.env` |
| 0012 | Account lifecycle moves into the admin console: create, flag as admin, delete (409 when content exists) |
| 0013 | Community moderation: retag and delete entries — deleting the community row only, never a user's history |
| 0014 | Card display fields are written at reservation time, not back-filled by the frontend |
| 0015 | The regeneration gate is stored in the database instead of assuming a single backend process |
| 0016 | URL canonicalisation: compare by canonical form, echo the original |
| 0017 | ~~Lightweight hand-written routing, no vue-router~~ — superseded by ADR 0018 |
| 0018 | Adopt `vue-router` with four real paths, replacing the hand-written router |

---

## 🛟 FAQ

<details>
<summary><b>Parsing fails?</b></summary>

- **"Cannot parse this link"** → `pip install --upgrade yt-dlp`
- **Download has no sound** → install ffmpeg
- **Douyin fails** → make sure you paste the short share link, then retry
</details>

<details>
<summary><b>AI summary does not work?</b></summary>

- **"No subtitles available"** → the video has no subtitle track, so it cannot be summarised
- **"Daily limit reached"** → 3 per account per day, reset at midnight
- **"Please sign in first"** → register or sign in to use the AI features
- **No response** → check the LLM key in `.env` (`ALIYUN_BAILIAN_API_KEY` or `DEEPSEEK_API_KEY`)
</details>

<details>
<summary><b>Database questions</b></summary>

```bash
# reset the database (removes all users and orders)
rm -f backend/data/app.db          # rebuilt automatically on the next start

# inspect it
sqlite3 backend/data/app.db "SELECT * FROM users;"
```
</details>

---

## 🤝 Contributing

Issues and pull requests are welcome. The engineering conventions live in
[AGENTS.md](AGENTS.md); the gate is `./init.sh`.

## 📄 License

[MIT](LICENSE)

---

<p align="center">
  <sub>Built with Vue 3 + FastAPI + yt-dlp + LLM</sub>
</p>
