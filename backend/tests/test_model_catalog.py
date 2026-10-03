"""模型清单入库（工单 #13 / ADR 0011）：播种、迁移幂等、两个读端点、默认模型收敛。

## 为什么播种数据要逐字抄 byok.js

这张表的第一版内容不是「设计」出来的，是**迁移**过来的：把
`frontend/src/lib/byok.js` 的 PROVIDERS 数组原样搬进库，迁移前后用户的
厂商下拉必须长得一模一样。任何一处「顺手改好看点」都会让本工单
从「消除漂移」变成「换了一个漂移源」。

所以下面的 EXPECTED 是从 byok.js 抄来的**独立副本**，不是 import 出来的
——import 同一份数据来和自己比，等于什么都没断言。
"""
import sqlite3

import pytest

import auth
import database
import model_catalog
import summarizer
from seams import auth_headers, make_client

#: byok.js PROVIDERS 的逐字快照（2026-10-03）。改这张表之前先改那个文件，
#: 两边对不上时这张表就是「谁在漂移」的证据。
EXPECTED = {
    "platform": {
        "label": "平台 Key（默认）", "base_url": "", "default_model": "",
        "models": [], "hint": "用平台配置的模型服务，消耗每日免费额度。",
        "is_real": 0,
    },
    "bailian": {
        "label": "阿里云百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "hint": "阿里云百炼 OpenAI 兼容模式。",
        "is_real": 1,
    },
    "deepseek": {
        "label": "DeepSeek", "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat", "hint": "DeepSeek 官方接口。",
        "is_real": 1,
    },
    "moonshot": {
        "label": "Moonshot / Kimi", "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-32k", "hint": "月之暗面 Kimi 开放平台。",
        "is_real": 1,
    },
    "openai": {
        "label": "OpenAI", "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini", "hint": "OpenAI 官方接口。",
        "is_real": 1,
    },
    "ollama": {
        "label": "Ollama（本地）", "base_url": "http://localhost:11434/v1",
        "default_model": "qwen2.5:7b",
        "hint": "本机推理服务。允许 http：自建服务通常没有证书。",
        "is_real": 1,
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）", "base_url": "", "default_model": "",
        "hint": "任何 OpenAI 兼容端点。http 与 https 都允许。",
        "is_real": 0,
    },
}


def _by_id(only_enabled=True, admin_view=False):
    return {p["id"]: p for p in
            model_catalog.list_model_providers(only_enabled=only_enabled,
                                               admin_view=admin_view)}


@pytest.fixture()
def client_app(db, make_user):
    import main as main_module

    admin_id = make_user(email="boss@example.com")
    with database.get_db() as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin_id,))
    return make_client(main_module.app), admin_id


def _admin_hdr(admin_id: int) -> dict:
    return auth_headers(auth.create_token(admin_id, "boss@example.com"))


# ── 播种：内容与前端那份硬编码逐字一致 ──────────────────────

def test_seeded_catalog_has_exactly_the_seven_frontend_providers(db):
    rows = _by_id()
    assert set(rows) == set(EXPECTED), (
        f"厂商集合与 byok.js 不一致，多了 {set(rows) - set(EXPECTED)}，"
        f"少了 {set(EXPECTED) - set(rows)}"
    )
    for pid, expected in EXPECTED.items():
        for field, want in expected.items():
            assert rows[pid][field] == want, (
                f"{pid}.{field} 与 byok.js 不一致："
                f"库里 {rows[pid][field]!r}，前端 {want!r}"
            )


def test_seeded_models_lists_contain_the_default(db):
    """每家的可选模型列表里必须有那个默认模型，否则清单自相矛盾。"""
    for pid, row in _by_id().items():
        if not row["default_model"]:
            continue
        assert row["default_model"] in row["models"], (
            f"{pid} 的默认模型 {row['default_model']} 不在自己的可选列表里"
        )


def test_catalog_is_ordered_by_sort_order(db):
    ids = [p["id"] for p in model_catalog.list_model_providers()]
    assert ids.index("platform") < ids.index("bailian") < ids.index("custom")
    assert ids[0] == "platform", "平台自身排第一——它是默认选项"


def test_platform_and_custom_are_not_marked_as_real_vendors(db):
    rows = _by_id()
    assert rows["platform"]["is_real"] == 0
    assert rows["custom"]["is_real"] == 0
    for pid in ("bailian", "deepseek", "moonshot", "openai", "ollama"):
        assert rows[pid]["is_real"] == 1, f"{pid} 是真厂商"


# ── 迁移：幂等、不丢数据、老库起得来 ────────────────────────

def test_fresh_schema_creates_and_seeds_the_table(db):
    with database.get_db() as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert model_catalog.TABLE in names, f"新库缺 {model_catalog.TABLE}：{sorted(names)}"
    assert len(_by_id()) == 7


def test_repeated_init_keeps_row_count_and_never_duplicates(db):
    database.init_db()
    database.init_db()
    model_catalog.init_model_catalog()
    rows = model_catalog.list_model_providers(only_enabled=False, admin_view=True)
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)) == 7, ids


def test_repeated_init_does_not_wipe_edits(db):
    """重复跑**不能**把改过的行刷回播种值。

    播种用 ON CONFLICT DO NOTHING 就是为了这条：每次启动都会跑这里，
    upsert 会让后台的写操作全部变成「改了也白改」。
    """
    with database.get_db() as conn:
        conn.execute(
            "UPDATE model_providers SET label = '改过的名字', enabled = 0 "
            "WHERE id = 'bailian'"
        )

    database.init_db()
    database.init_db()

    row = next(p for p in model_catalog.list_model_providers(
        only_enabled=False, admin_view=True) if p["id"] == "bailian")
    assert row["label"] == "改过的名字", "重启把后台改过的值刷回了播种值"
    assert row["enabled"] == 0, "下架状态也被刷回了"


def test_legacy_db_without_the_table_gets_it_created_and_seeded(db, tmp_path, monkeypatch):
    """老库里根本没有这张表：升级后必须被正确建出并播种，老数据一行不丢。

    刻意不 `with client`：lifespan 会跑一遍 init_db，把「迁移有没有真跑」
    这件事遮住（工单 #7 的假护栏）。
    """
    from seams import close_all_thread_connections

    legacy = tmp_path / "legacy_catalog.db"
    with sqlite3.connect(str(legacy)) as conn:
        conn.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            );
        """)
        conn.execute("INSERT INTO users (email, password_hash) VALUES ('old@x.com','h')")

    monkeypatch.setattr(database, "DB_PATH", str(legacy))
    close_all_thread_connections()

    database.init_db()   # 第一次：建表 + 播种
    database.init_db()   # 第二次：必须不报 duplicate、不重复插

    with sqlite3.connect(str(legacy)) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert model_catalog.TABLE in names, sorted(names)
        seeded = conn.execute(f"SELECT count(*) FROM {model_catalog.TABLE}").fetchone()[0]
        assert seeded == 7, f"老库升级后没有播种，实得 {seeded} 行"
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1, "老用户被弄丢了"


# ── 两个读端点：公开 vs 管理 ────────────────────────────────

def test_public_endpoint_returns_only_enabled_rows_without_admin_fields(client_app):
    client, _ = client_app
    with database.get_db() as conn:
        conn.execute("UPDATE model_providers SET enabled = 0 WHERE id = 'ollama'")

    body = client.get("/api/models").json()
    ids = [p["id"] for p in body["items"]]
    assert "ollama" not in ids, "已下架的厂商不该出现在公开清单里"
    assert set(ids) == set(EXPECTED) - {"ollama"}
    for item in body["items"]:
        assert set(item) == {"id", "label", "base_url", "default_model",
                             "models", "hint", "is_real"}, (
            f"公开投影多出了字段：{sorted(item)}"
        )
        assert "enabled" not in item and "sort_order" not in item


def test_admin_endpoint_returns_everything_including_disabled(client_app):
    client, admin_id = client_app
    with database.get_db() as conn:
        conn.execute("UPDATE model_providers SET enabled = 0 WHERE id = 'ollama'")

    r = client.get("/api/admin/models", headers=_admin_hdr(admin_id))
    assert r.status_code == 200, r.text
    ids = [p["id"] for p in r.json()["items"]]
    assert "ollama" in ids, "管理视图必须看得见已下架的行"
    assert set(ids) == set(EXPECTED)
    ollama = next(p for p in r.json()["items"] if p["id"] == "ollama")
    assert ollama["enabled"] == 0
    assert isinstance(ollama["sort_order"], int)


def test_admin_models_requires_admin(client_app):
    client, _ = client_app
    assert client.get("/api/admin/models").status_code == 401
    other = database.create_user("plain@example.com", "h")
    r = client.get("/api/admin/models",
                   headers=auth_headers(auth.create_token(other["id"], other["email"])))
    assert r.status_code == 403, f"非管理员应得 403，实得 {r.status_code}"


def test_admin_models_returns_data_for_admin(client_app):
    """反向对照：管理员必须 200 且非空（防「全员 403 也能绿」）。"""
    client, admin_id = client_app
    r = client.get("/api/admin/models", headers=_admin_hdr(admin_id))
    assert r.status_code == 200 and r.json()["items"]


def test_either_endpoint_never_returns_a_credential(client_app, db):
    """两个响应体都不含 key / token（ADR 0004 / 0011）。

    将来有人给表加一列 api_key，白名单投影不会因为那列存在就吐出去。
    """
    client, admin_id = client_app
    for payload in (client.get("/api/models").json(),
                    client.get("/api/admin/models",
                               headers=_admin_hdr(admin_id)).json()):
        for item in payload["items"]:
            for name in item:
                assert not any(h in name.lower() for h in
                               ("key", "token", "secret", "credential")), name


# ── 默认模型：从表里读，且真的收敛了漂移 ───────────────────

def _make_summarizer(monkeypatch) -> summarizer.VideoSummarizer:
    """构造一个用平台百炼配置的 summarizer（不联网、不花钱）。"""
    monkeypatch.setenv("ALIYUN_BAILIAN_API_KEY", "sk-test-placeholder-not-real")
    monkeypatch.delenv("ALIYUN_BAILIAN_MODEL", raising=False)
    return summarizer.VideoSummarizer()


def test_platform_default_model_is_read_from_the_table(db, monkeypatch):
    """改表里的默认模型 → summarizer 立刻跟着变。

    这条是「不再写死」的行为证明。静态断言（源码里没有那个字符串）也能过，
    但改回去的人只要同时改回硬编码就能骗过它；这条不行——
    硬编码的实现在这里必然红。
    """
    with database.get_db() as conn:
        conn.execute(
            "UPDATE model_providers SET default_model = 'qwen-max' WHERE id = 'bailian'"
        )

    assert _make_summarizer(monkeypatch).model == "qwen-max"


def test_default_model_converged_on_qwen_plus(db, monkeypatch):
    """前后端漂移的那一处，收敛到表里的 qwen-plus（= 改动前 byok.js 的取值）。"""
    assert _by_id()["bailian"]["default_model"] == "qwen-plus"
    assert _make_summarizer(monkeypatch).model == "qwen-plus"
    assert summarizer._FALLBACK_BAILIAN_MODEL == "qwen-plus", (
        "兜底值必须和表里的默认值一致，否则读表失败时会静默换回旧模型"
    )


def test_env_var_still_wins_over_the_table(db, monkeypatch):
    """表管默认，env 覆盖默认——既有优先级不能被这张表改掉。"""
    monkeypatch.setenv("ALIYUN_BAILIAN_MODEL", "qwen-flash")
    monkeypatch.setenv("ALIYUN_BAILIAN_API_KEY", "sk-test-placeholder-not-real")
    assert summarizer.VideoSummarizer().model == "qwen-flash"


def test_missing_provider_row_falls_back_with_a_warning(db, monkeypatch, caplog):
    """表里没有这一行 → 回落兜底值，**并且留痕**。

    静默回落一个可能已下架的模型名，正是工单 #13 点名要消灭的那种行为。
    """
    with database.get_db() as conn:
        conn.execute("DELETE FROM model_providers WHERE id = 'bailian'")

    with caplog.at_level("WARNING", logger="model_catalog"):
        assert model_catalog.platform_default_model("bailian") is None
    assert any("bailian" in r.getMessage() for r in caplog.records), (
        f"读不到表却没有 warning：{[r.getMessage() for r in caplog.records]}"
    )
    # 兜底本身仍然可用——回落是明确发生的，不是彻底不可用
    assert _make_summarizer(monkeypatch).model == "qwen-plus"


def test_platform_default_model_is_none_for_unknown_provider(db):
    assert model_catalog.platform_default_model("no-such-provider") is None


def test_empty_default_model_is_treated_as_unset(db):
    """platform / custom 天然没有默认模型——空串不能被当成模型名用。"""
    assert model_catalog.platform_default_model("platform") is None
    assert model_catalog.platform_default_model("custom") is None


def test_dirty_models_column_degrades_to_empty_list(db):
    """脏数据不该变成下游的 TypeError。"""
    with database.get_db() as conn:
        conn.execute("UPDATE model_providers SET models = 'not json' WHERE id = 'openai'")
        conn.execute("UPDATE model_providers SET models = '{\"a\": 1}' WHERE id = 'ollama'")
        conn.execute("UPDATE model_providers SET models = '[1, \"ok\", null]' WHERE id = 'moonshot'")

    rows = _by_id()
    assert rows["openai"]["models"] == []
    assert rows["ollama"]["models"] == []
    assert rows["moonshot"]["models"] == ["ok"], "非字符串元素要滤掉，而不是原样吐出去"
