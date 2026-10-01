# AGENTS.md

VidDigest —— 通用视频解析 / 下载 + AI 字幕总结工具。
`backend/` FastAPI（Python 3.11）+ `frontend/` Vue 3 + Vite。两个进程独立启动，没有 monorepo 编排。

## 环境事实（实测，勿凭猜测推翻）

- 后端解释器**必须**用 `backend\venv\Scripts\python.exe`（3.11.9），系统 Python 不对。
- `backend/main.py` 用 `uvicorn.run(app, ...)`，**没有 `--reload`**——改 Python 代码必须重启进程。
- 前端 Vite 只监听 IPv6 回环：一律用 `http://localhost:5173`，`127.0.0.1:5173` 连不上。
- 真实凭据只在 `backend/.env`（已 gitignore）。**任何时候不要 cat 出来贴进文档、提交或日志。**
- Windows 上 PowerShell 会静默改写内联脚本里的中文 / 引号 / JSON。含中文、多行、引号的一次性脚本一律先 `write` 成 `.py` / `.cjs` 再执行，不要用 `python -c` / `node -e` 内联。

## 测试

```powershell
cd backend;  & .\venv\Scripts\python.exe -m pytest tests -q
cd frontend; npm test          # node --test，零额外依赖
```

## Agent skills

### Issue tracker

Issues and specs live in this repo's GitHub Issues, driven through the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, using the default label strings verbatim. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` at the repo root, ADRs under `docs/adr/`. See `docs/agents/domain.md`.
