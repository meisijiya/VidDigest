"""平台模型清单（工单 #13 / ADR 0011）。

**这里没有凭据。** 表里只有「有哪些厂商、每个厂商开放哪些模型、哪个是平台默认」，
平台自己的 API Key 仍在 `backend/.env`，用户自带密钥（BYOK）按 ADR 0004 完全不动。
本模块不 import 任何凭据来源，也不接受任何凭据入参——将来有人想往这张表里加
一列 `api_key`，会先撞上 ADR 而不是撞上这里的某条 if。

## 为什么不放进 database.py

那张文件是 users / orders / videos 三张业务表的地基，迁移线索已经不少。
把第四张表塞进去，读它的人要先分辨哪些 DDL 属于哪一轮工单。
独立成文件也让本表可以被单独删掉而不牵动业务表（ADR 0011 记录的取舍：
清单一长就会过期，但过期现在是一行记录的问题，不是一次代码考古）。

## 表结构

`model_providers` 一行 = 一个厂商。`models` 存 JSON 数组文本而不是关联表：
可选模型是「这一行的一个属性」，不是需要被 JOIN、被引用、被单独授权的实体。
"""

import json
import logging

from database import get_db

logger = logging.getLogger("model_catalog")

#: 表名。刻意不叫 models——那张名字在前端已经指「一个模型」，
#: 而这里一行是「一个厂商及其可选模型」。
TABLE = "model_providers"


# ── 播种数据 ────────────────────────────────────────────────
#
# 逐字抄自改动前的 `frontend/src/lib/byok.js` 的 PROVIDERS 数组
# （2026-10-03 实测快照）。抄它不是为了保留那份硬编码，而是为了让
# 「入库」这一步**不产生任何行为变化**：迁移前后的厂商下拉必须长得一模一样，
# 否则这张表就成了第二个漂移源，而不是消除漂移的那个。
#
# 两处与原文件不同，且都是**新增**而非改写：
#   1. is_real —— platform 与 custom 不是真厂商（一个是平台自己，一个是
#      「随便什么 OpenAI 兼容端点」）。不给标记的话，后台的真厂商列表会把
#      这两行混进去，而它们的 base_url / default_model 本来就是空的。
#   2. models —— byok.js 每家只给了一个默认模型，这里额外把后端原本写死的
#      qwen-turbo 补进百炼的可选列表（summarizer.py 的文档串与报错文案
#      至今还把它列为百炼的合法模型名）。**只补有仓库内证据的那个**，
#      其余各家不编模型名——猜出来的清单会连同清单一长就过期这条一起烂掉。
#
# 平台默认模型由此**收敛到 qwen-plus**（前端 byok.js:50 的取值）：
# 改动前后端写死的是 qwen-turbo，两边漂移正是本工单要消除的那件事。

SEED_PROVIDERS = [
    {
        "id": "platform",
        "label": "平台 Key（默认）",
        "base_url": "",
        "default_model": "",
        "models": [],
        "hint": "用平台配置的模型服务，消耗每日免费额度。",
        "is_real": 0,
        "sort_order": 0,
    },
    {
        "id": "bailian",
        "label": "阿里云百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "models": ["qwen-plus", "qwen-turbo"],
        "hint": "阿里云百炼 OpenAI 兼容模式。",
        "is_real": 1,
        "sort_order": 10,
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat"],
        "hint": "DeepSeek 官方接口。",
        "is_real": 1,
        "sort_order": 20,
    },
    {
        "id": "moonshot",
        "label": "Moonshot / Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-32k",
        "models": ["moonshot-v1-32k"],
        "hint": "月之暗面 Kimi 开放平台。",
        "is_real": 1,
        "sort_order": 30,
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini"],
        "hint": "OpenAI 官方接口。",
        "is_real": 1,
        "sort_order": 40,
    },
    {
        "id": "ollama",
        "label": "Ollama（本地）",
        "base_url": "http://localhost:11434/v1",
        "default_model": "qwen2.5:7b",
        "models": ["qwen2.5:7b"],
        "hint": "本机推理服务。允许 http：自建服务通常没有证书。",
        "is_real": 1,
        "sort_order": 50,
    },
    {
        "id": "custom",
        "label": "自定义（OpenAI 兼容）",
        "base_url": "",
        "default_model": "",
        "models": [],
        "hint": "任何 OpenAI 兼容端点。http 与 https 都允许。",
        "is_real": 0,
        "sort_order": 60,
    },
]


#: 对外投影（GET /api/models）。**白名单，不靠逐个剔除。**
#:
#: 凭据一律不在表里（ADR 0004 / 0011），但白名单仍然是承重的那道：
#: 将来有人给表加了一列 `api_key`，白名单不会因为那列存在就把它吐出去。
#: 这与 database._project_video 同一套路——那里管社区内容，那里管凭据。
PUBLIC_FIELDS = ("id", "label", "base_url", "default_model", "models",
                 "hint", "is_real")

#: 后台投影（GET /api/admin/models）。多 enabled 与 sort_order，
#: 因为「哪些行上了架」本身就是后台要管的事。
#:
#: 仍然**不含**任何 key / token / 凭据字段：管理员也不需要看它，
#: 平台 Key 留在 .env（ADR 0011）。
ADMIN_FIELDS = PUBLIC_FIELDS + ("enabled", "sort_order")


def _parse_models(raw) -> list:
    """把 models 列解析成 list[str]。脏数据当没有，不让下游 TypeError。"""
    try:
        parsed = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [m for m in parsed if isinstance(m, str)]


def _project(row, fields: tuple) -> dict:
    item = {name: row[name] for name in fields}
    item["models"] = _parse_models(item.get("models"))
    return item


# ── 建表与播种 ──────────────────────────────────────────────

_CREATE_DDL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        base_url TEXT NOT NULL DEFAULT '',
        default_model TEXT NOT NULL DEFAULT '',
        models TEXT NOT NULL DEFAULT '[]',
        hint TEXT NOT NULL DEFAULT '',
        is_real INTEGER NOT NULL DEFAULT 1,
        enabled INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
"""


def _seed(conn) -> int:
    """补齐**缺失**的厂商行，返回新插入的行数。

    刻意用 `ON CONFLICT(id) DO NOTHING` 而不是 upsert：每次启动都会跑这里，
    upsert 会把后台改过的显示名、模型列表、下架状态**全部刷回播种值**——
    那等于「改了也白改」，后台的写操作（#13 之后）就没有意义了。
    只补不存在的行，重复跑幂等，也不丢任何已改过的数据。
    """
    inserted = 0
    for row in SEED_PROVIDERS:
        cursor = conn.execute(
            f"""INSERT INTO {TABLE}
                (id, label, base_url, default_model, models, hint, is_real,
                 enabled, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(id) DO NOTHING""",
            (
                row["id"], row["label"], row["base_url"], row["default_model"],
                json.dumps(row["models"], ensure_ascii=False), row["hint"],
                row["is_real"], row["sort_order"],
            ),
        )
        inserted += cursor.rowcount
    return inserted


def init_model_catalog(conn=None) -> int:
    """建表 + 播种。幂等。返回新插入的行数。

    由 ``database.init_db`` 在**同一份连接、同一批事务里**调用，所以
    「建表」与「播种」要么都成功要么都回滚，不会留下空表。
    单独调用时（测试、或将来别的入口）自己开一份连接。
    """
    if conn is not None:
        conn.executescript(_CREATE_DDL)
        return _seed(conn)
    with get_db() as own:
        own.executescript(_CREATE_DDL)
        return _seed(own)


# ── 查询 ────────────────────────────────────────────────────

def list_model_providers(only_enabled: bool = True, admin_view: bool = False) -> list[dict]:
    """厂商清单，按 sort_order 升序、id 兜底排序。

    带上 id 兜底是为了让顺序在两行 sort_order 相同时仍然确定——否则翻页
    与「改完再读」可能给出两种顺序，前端会看到列表自己跳了一下。
    """
    fields = ADMIN_FIELDS if admin_view else PUBLIC_FIELDS
    where = "WHERE enabled = 1" if only_enabled else ""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM {TABLE} {where} ORDER BY sort_order ASC, id ASC"
        ).fetchall()
    return [_project(row, fields) for row in rows]


def get_model_provider(provider_id: str, only_enabled: bool = True) -> dict | None:
    """单个厂商行（公共投影）。不存在或已下架时返回 None。"""
    where = "AND enabled = 1" if only_enabled else ""
    with get_db() as conn:
        row = conn.execute(
            f"SELECT * FROM {TABLE} WHERE id = ? {where}", (provider_id,)
        ).fetchone()
    return _project(row, PUBLIC_FIELDS) if row else None


def platform_default_model(provider_id: str) -> str | None:
    """该厂商的平台默认模型名。表不可用或该行没配时返回 None。

    返回 None 而不是兜一个硬编码字符串，是为了让调用方**必须**决定
    「表读不到时怎么办」——而决定的结果被记进日志（见 summarizer）。
    静默回落一个可能已下架的模型名，正是工单 #13 点名要消灭的那种行为。
    """
    try:
        row = get_model_provider(provider_id)
    except Exception as exc:  # 表还没建、库打不开——都不该让调用方崩
        logger.warning("读模型清单失败（provider=%s）：%s", provider_id, exc)
        return None
    if row is None:
        logger.warning("模型清单里没有 provider=%s 这一行", provider_id)
        return None
    return row.get("default_model") or None
# ── 写入（ADR 0010 的「模型清单可改」）─────────────────────────
#
# 只做**改已有行**，不提供新增端点：新增一个平台厂商还得在 .env 里配凭据
# 并重启（ADR 0011 记录的代价），所以「在后台建一个行」出来的是一条
# 立刻不可用的记录 —— 那是比「不能建」更糟的体验。改已有行则是纯配置操作，
# 效果当场可见。
#
# 下面每条规则都在挡一类「改完看着对、跑起来不对」：
#   · default_model 必须在 models 里 —— 表里指着一个不存在的模型名，
#     正是工单 #13 要消灭的那种漂移，只是从代码搬进了数据
#   · models 去重 —— 重复项在下拉里会出现两次，而 deepEqual 断言
#     分不出「重复」和「顺序不同」
#   · enabled 显式拒 bool —— Python 里 True == 1，
#     `{"enabled": true}` 会静静地变成「上架」
#   · base_url 只收 http/https 且不含 userinfo —— 与前端
#     validateBaseUrl 同一套口径，避免后端收下一条前端会拒的端点


class ModelValueError(ValueError):
    """清单字段值域非法。路由层翻译成 400。"""


#: 可改的字段白名单。不在名单里的键一律拒绝——「多传一个字段就静默忽略」
#: 会让调用方以为改成功了。
EDITABLE_FIELDS = ("label", "hint", "base_url", "models",
                   "default_model", "enabled", "sort_order")

#: 单个字段的长度上界。都是「够用又不至于把接口当数据库用」的量级。
MAX_LABEL = 60
MAX_HINT = 200
MAX_MODELS = 40
SORT_ORDER_MIN, SORT_ORDER_MAX = -1000, 1000


def _validate_models(value) -> list[str]:
    if not isinstance(value, list):
        raise ModelValueError(f"models 必须是数组，实得 {type(value).__name__}")
    if not value:
        raise ModelValueError("models 不能为空数组——一个可选模型都没有的厂商没法用")
    if len(value) > MAX_MODELS:
        raise ModelValueError(f"models 最多 {MAX_MODELS} 项，实得 {len(value)}")
    out = []
    for item in value:
        if not isinstance(item, str):
            raise ModelValueError(f"models 里的每一项都必须是字符串，实得 {item!r}")
        text = item.strip()
        if not text:
            raise ModelValueError("models 里不能有空字符串")
        out.append(text)
    dupes = sorted({m for m in out if out.count(m) > 1})
    if dupes:
        raise ModelValueError(f"models 里有重复项：{dupes}（重复项在下拉里会出现两次）")
    return out


def _validate_base_url(value) -> str:
    text = "" if value is None else str(value).strip().rstrip("/")
    if not text:
        return ""
    from urllib.parse import urlparse
    parts = urlparse(text)
    if parts.scheme not in ("http", "https"):
        raise ModelValueError("base_url 只支持 http 或 https")
    if parts.username or parts.password:
        raise ModelValueError("base_url 里不能带用户名或密码")
    if not parts.hostname:
        raise ModelValueError("base_url 缺少主机名")
    return text


def _validate_enabled(value) -> int:
    if isinstance(value, bool):
        raise ModelValueError("enabled 必须是 0 或 1，不能是布尔值")
    if value not in (0, 1):
        raise ModelValueError(f"enabled 只能取 0（下架）或 1（上架），实得 {value!r}")
    return int(value)


def _validate_sort_order(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelValueError(f"sort_order 必须是整数，实得 {value!r}")
    if not SORT_ORDER_MIN <= value <= SORT_ORDER_MAX:
        raise ModelValueError(
            f"sort_order 只能取 {SORT_ORDER_MIN}..{SORT_ORDER_MAX}，实得 {value}"
        )
    return value


def _validate_text(value, field: str, limit: int, allow_empty: bool) -> str:
    text = "" if value is None else str(value).strip()
    if not text and not allow_empty:
        raise ModelValueError(f"{field} 不能为空")
    if len(text) > limit:
        raise ModelValueError(f"{field} 最长 {limit} 字符，实得 {len(text)}")
    return text


def update_model_provider(provider_id: str, patch: dict) -> dict | None:
    """改一个厂商行。**只改 patch 里出现的字段**，返回回读后的管理视图。

    返回 None 表示 provider_id 不存在——路由层据此回 404，不静默成功。

    ## 为什么要「回读」而不是「回显」

    回显的是「我们请求写进去的是什么」，回读的是「库里现在是什么」。
    后者才在 `default_model` 被规范化、或并发改动发生时仍然为真。

    ## 为什么 `default_model` 必须在 `models` 里

    表里指着一个不存在的模型名，是工单 #13 点名要消灭的那种漂移，
    只是从写死的代码搬进了数据。而它比代码漂移更难发现：代码漂移
    grep 得到，数据漂移只在真的发起模型调用时才炸。
    """
    if not isinstance(patch, dict):
        raise ModelValueError("请求体必须是对象")

    unknown = sorted(set(patch) - set(EDITABLE_FIELDS))
    if unknown:
        raise ModelValueError(
            f"这些字段不可改：{unknown}；可改的是 {list(EDITABLE_FIELDS)}"
        )
    if not patch:
        raise ModelValueError("请求体为空，没有任何要改的字段")

    with get_db() as conn:
        row = conn.execute(
            f"SELECT * FROM {TABLE} WHERE id = ?", (provider_id,)
        ).fetchone()
        if row is None:
            return None
        current = dict(row)
        try:
            current_models = json.loads(current.get("models") or "[]")
        except (ValueError, TypeError):
            current_models = []

        # 先算「改动之后」的 models 与 default_model，再校验二者的关系 ——
        # 只改 models 不改 default_model 时，默认值也必须仍在新列表里，
        # 否则「换个模型列表」这一步就会静默留下一个失效的默认。
        if "models" in patch:
            new_models = _validate_models(patch["models"])
        else:
            new_models = current_models

        if "default_model" in patch:
            raw_default = patch["default_model"]
            new_default = "" if raw_default is None else str(raw_default).strip()
            if new_default and new_default not in new_models:
                raise ModelValueError(
                    f"default_model {new_default!r} 不在 models 里，"
                    f"当前可选项是 {new_models}。指着一个不存在的模型名正是"
                    "工单 #13 要消灭的漂移，只是从代码搬进了数据。"
                )
        elif "models" in patch:
            # 只有**这次请求真的换了列表**才拦。models 没出现在
            # patch 里时，new_models 就是库里原样那份，
            # 此时的 default 不在列表中是**存量漂移**，不是这次改出来的。
            existing_default = (current.get("default_model") or "").strip()
            if existing_default and existing_default not in new_models:
                raise ModelValueError(
                    f"换了 models 之后，原默认模型 {existing_default!r} "
                    f"不在新列表里：{new_models}。要么把它加回去，"
                    "要么同时传 default_model 指定新的。"
                )
            new_default = existing_default
        else:
            # models 与 default_model 都没传：两列原样保留，**不校验**。
            # 拿存量漂移去挡「改显示名」「下架」是反的——下架恰恰是
            # 管理员对着一行脏数据最想做的事。修复漂移的入口是显式传
            # default_model（上面那个分支会拿它对着 models 校验）。
            new_default = (current.get("default_model") or "").strip()

        if "enabled" in patch:
            new_enabled = _validate_enabled(patch["enabled"])
        else:
            new_enabled = current.get("enabled", 1)

        assignments = {
            "models": json.dumps(new_models, ensure_ascii=False),
            "default_model": new_default,
            "enabled": new_enabled,
        }
        if "label" in patch:
            assignments["label"] = _validate_text(
                patch["label"], "label", MAX_LABEL, allow_empty=False)
        if "hint" in patch:
            assignments["hint"] = _validate_text(
                patch["hint"], "hint", MAX_HINT, allow_empty=True)
        if "base_url" in patch:
            assignments["base_url"] = _validate_base_url(patch["base_url"])
        if "sort_order" in patch:
            assignments["sort_order"] = _validate_sort_order(patch["sort_order"])

        conn.execute(
            f"UPDATE {TABLE} SET "
            + ", ".join(f"{k} = ?" for k in assignments)
            + " WHERE id = ?",
            (*assignments.values(), provider_id),
        )
        fresh = conn.execute(
            f"SELECT * FROM {TABLE} WHERE id = ?", (provider_id,)
        ).fetchone()

    return _project(fresh, ADMIN_FIELDS)
