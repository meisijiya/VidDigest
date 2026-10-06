# VidDigest API 文档

> 基础地址：`http://localhost:8000`

## 认证方式

需要认证的接口在请求头中携带 Token：

```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

Token 有效期 **72 小时**，过期需重新登录。

## API 列表

> 本文是**完整**清单。此前只覆盖 13 条，history / community / admin / models
> 四层整层缺席（工单 #15 补齐）。

### 公开 / 用户

| 方法 | 路径 | 认证 | 说明 |
|:----:|:-----|:----:|------|
| GET | `/api/health` | — | 健康检查 |
| POST | `/api/parse` | — | 解析视频信息 |
| POST | `/api/download` | — | 服务端下载视频 |
| POST | `/api/direct-url` | — | 获取视频直链 |
| GET | `/api/proxy/thumbnail` | — | 代理缩略图 |
| POST | `/api/summarize` | 可选 | AI 总结（SSE 流式） |
| POST | `/api/chat` | 可选 | AI 问答（SSE 流式） |
| POST | `/api/auth/register` | — | 用户注册 |
| POST | `/api/auth/login` | — | 用户登录 |
| GET | `/api/auth/me` | 必需 | 获取用户信息 |
| GET | `/api/models` | — | **公开**模型清单（只给 `enabled=1` 的行，供 BYOK 下拉） |

### 社区（`api_community.py`）

| 方法 | 路径 | 认证 | 说明 |
|:----:|:-----|:----:|------|
| GET | `/api/community/videos` | 可选 | 社区卡片列表。未登录可读；登录时带 `token` 可回填归属标记。支持 `limit` / `offset` / `tag`（**多选取并集**） |
| GET | `/api/community/tags` | — | 标签全量清单（带条数），不随当前页汇总 |
| GET | `/api/community/videos/by-url` | — | 按 URL 查社区是否已有结果。用于「零成本自动展示」判定 |
| GET | `/api/community/videos/{video_id}` | 必需 | 单条详情。⚠️ **前端当前不调用**（详情统一走 summarize 复用回放），见工单 #17 |
| GET | `/api/community/search` | 必需 | 社区搜索。未登录返回 `need_login` 而非 401 |
| POST | `/api/community/cards` | 必需 | 回填卡片标题 / 封面（先到先得，不覆盖已有值） |

### 历史（`api_history.py`）

| 方法 | 路径 | 认证 | 说明 |
|:----:|:-----|:----:|------|
| GET | `/api/history` | 必需 | 解析历史列表。支持 `limit` / `offset` / `q` / `tag`（并集）/ `ai_status` / `favorite_only` |
| POST | `/api/history/save` | 必需 | 写入 / 更新一条解析历史，并滚动裁剪到上限 1000（**收藏不参与裁剪**） |
| GET | `/api/history/chat` | 必需 | 取某个 URL 的追问会话，返回 `[{question, answer}]`；无记录返回 `[]` |
| GET | `/api/history/facets` | 必需 | 筛选用的分面数据（标签清单等），供筛选器渲染 |
| GET | `/api/history/{id}` | 必需 | 单条历史详情 |
| PATCH | `/api/history/{id}/favorite` | 必需 | 设为收藏 / 取消收藏（语义是「设为」不是「翻转」） |
| DELETE | `/api/history/{id}` | 必需 | 删一条。**收藏项需 `force=true`**，否则 409 |
| DELETE | `/api/history` | 必需 | 清空当前用户全部历史 |

### 管理后台（`admin_api.py`，全部需管理员）

| 方法 | 路径 | 说明 |
|:----:|:-----|------|
| GET | `/api/admin/models` | 全部厂商清单（**含** `enabled=0` 的行） |
| PATCH | `/api/admin/models/{provider_id}` | 改已有行的显示名 / 提示 / 端点 / 可选模型 / 平台默认 / 上下架 / 排序。不提供新增端点 |
| GET | `/api/admin/users` | 用户列表。`limit` / `offset` / `q`（按邮箱模糊） |
| POST | `/api/admin/users` | 建号。直接设初始密码；`is_admin` 可选。**不预置额度**，一律回落全局上限 |
| PATCH | `/api/admin/users/{id}` | 改管理员标记。**只做 `is_admin` 开关，VIP 一点不碰** |
| DELETE | `/api/admin/users/{id}` | 删号。名下有订单或解析历史 → **409，绝不级联**；响应带结构化 `blockers: {表名: 行数}` |
| POST | `/api/admin/users/{id}/quota` | 改额度覆盖。`null`=清除覆盖回落全局；给有效 VIP 设覆盖会返回 `note: "vip_not_effective"`（值仍写库，VIP 到期后生效） |
| GET | `/api/admin/community` | 社区记录列表（**不过滤 status** —— pending 占位恰是后台最该看的），行上带 `status` |
| PATCH | `/api/admin/community/{id}` | 改标签。**词表外 → 400 并点名被拒的标签**（不静默归入「其他」）；上限由服务端给 |
| DELETE | `/api/admin/community/{id}` | 删条目。**只删 `videos` 那一行**，用户的解析历史一行不动 |
| GET | `/api/admin/tags/vocabulary` | 标签词表（前端不抄第二份） |

### 支付（会员制当前关闭）

| 方法 | 路径 | 认证 | 说明 |
|:----:|:-----|:----:|------|
| POST | `/api/payment/create-checkout` | 必需 | 创建 Stripe 支付会话 |
| POST | `/api/payment/webhook` | — | Stripe Webhook 回调（验签） |
| GET | `/api/payment/orders` | 必需 | 查看订单历史 |

> 会员制入口已关闭（`frontend/src/config/features.js` 的 `MEMBERSHIP_ENABLED = false`）。
> 上述三个端点与 `orders` 表属于**契约的一部分**，动它们前先读 ADR 0010 / 0012。

---

## 接口详情

### POST `/api/parse` — 解析视频

**请求：**
```json
{"url": "https://www.bilibili.com/video/BV1uT4y1P7CX"}
```

**响应：**
```json
{
  "success": true,
  "data": {
    "id": "BV1uT4y1P7CX",
    "title": "视频标题",
    "thumbnail": "https://...",
    "duration": 212,
    "duration_string": "3:32",
    "uploader": "上传者",
    "platform": "BiliBili",
    "formats": [
      {
        "format_id": "30216",
        "ext": "mp4",
        "height": 1080,
        "label": "1080p MP4 (12.3MB)",
        "has_audio": true
      }
    ],
    "subtitles": ["zh-Hans"],
    "automatic_captions": []
  }
}
```

### POST `/api/summarize` — AI 总结（SSE 流式）

**请求：**
```json
{"url": "https://www.bilibili.com/video/BV1uT4y1P7CX", "language": "zh"}
```

**SSE 事件流（按顺序接收）：**

| 事件 | 数据 | 说明 |
|:----:|:----|:-----|
| `subtitle` | `{"has_subtitle": true, "language": "zh", "segments": [...], "full_text": "..."}` | 字幕元数据 |
| `summary` | `"流式输出的文本片段..."` | 总结内容（多次推送，前端累加） |
| `mindmap` | `{"markdown": "# 标题\n## 章节\n..."}` | 思维导图 Markdown |
| `quota` | `{"remaining": 2, "limit": 3}` | 剩余使用次数 |
| `error` | `{"message": "...", "need_login": false, "need_vip": false}` | 错误信息 |
| `done` | `[DONE]` | 流结束标记 |

### POST `/api/auth/register` — 注册

**请求：**
```json
{"email": "user@example.com", "password": "123456"}
```

**响应：**
```json
{
  "success": true,
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIs...",
    "user": {"id": 1, "email": "user@example.com", "is_vip": false}
  }
}
```

### POST `/api/auth/login` — 登录

与注册相同的请求/响应格式。

### POST `/api/payment/create-checkout` — 创建支付

需要认证。创建一个 Stripe Checkout Session 并返回 URL。

**响应：**
```json
{
  "success": true,
  "data": {
    "url": "https://checkout.stripe.com/c/pay_xxx"
  }
}
```

前端应重定向到 `url`，支付完成后自动跳回 `FRONTEND_URL`。
