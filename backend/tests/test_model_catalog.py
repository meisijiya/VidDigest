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


# ── 模型清单**可改**（ADR 0010）────────────────────────────────

def test_update_changes_the_row_and_rereads_it(db):
    """改 label / hint，**回读**确认（不是回显请求里的值）。"""
    out = model_catalog.update_model_provider(
        "bailian", {"label": "百炼（改名过）", "hint": "新的提示文案"})
    assert out is not None, "存在的厂商不该返回 None"
    assert out["label"] == "百炼（改名过）"
    assert out["hint"] == "新的提示文案"
    # 回读：绕过刚返回的值，直接从库里再读一次
    again = model_catalog.get_model_provider("bailian", only_enabled=False)
    assert again["label"] == "百炼（改名过）", "返回值不是库里的真值 —— 那是回显不是回读"


def test_update_can_change_the_platform_default_model(db):
    """改 default_model 后，平台默认模型读到的就是新值。"""
    assert model_catalog.platform_default_model("bailian") == "qwen-plus"
    out = model_catalog.update_model_provider("bailian", {"default_model": "qwen-turbo"})
    assert out["default_model"] == "qwen-turbo"
    assert model_catalog.platform_default_model("bailian") == "qwen-turbo", (
        "表改了但 platform_default_model 仍读旧值 —— 默认模型那条链没接上"
    )


def test_update_can_extend_the_models_list(db):
    out = model_catalog.update_model_provider(
        "bailian", {"models": ["qwen-plus", "qwen-turbo", "qwen-max"]})
    assert out["models"] == ["qwen-plus", "qwen-turbo", "qwen-max"]
    assert model_catalog.platform_default_model("bailian") == "qwen-plus", (
        "只改 models 不该顺手改掉默认模型"
    )


def test_disabling_a_provider_drops_the_platform_default(db):
    """下架 → platform_default_model 回落为 None。

    这条连着 summarizer：它读不到表时会回落硬编码并打 warning，
    而「静默用一个已下架的模型名」正是工单 #13 要消灭的行为。
    """
    assert model_catalog.platform_default_model("bailian") == "qwen-plus"
    out = model_catalog.update_model_provider("bailian", {"enabled": 0})
    assert out["enabled"] == 0
    assert model_catalog.platform_default_model("bailian") is None, (
        "已下架的厂商仍在提供平台默认模型 —— enabled=1 那条过滤没生效"
    )
    # 重新上架要能恢复
    again = model_catalog.update_model_provider("bailian", {"enabled": 1})
    assert again["enabled"] == 1
    assert model_catalog.platform_default_model("bailian") == "qwen-plus"


def test_update_rejects_a_default_model_outside_the_list(db):
    """指着一个不存在的模型名，是把漂移从代码搬进了数据。"""
    with pytest.raises(model_catalog.ModelValueError) as e:
        model_catalog.update_model_provider("bailian", {"default_model": "qwen-ultra"})
    assert "不在 models 里" in str(e.value)


def test_update_rejects_dropping_the_default_without_naming_a_new_one(db):
    """只改 models、默认模型被排除掉且没同时指定新的 → 400。

    不拦的话，「换个模型列表」这一步会静默留下一个失效的默认。
    """
    with pytest.raises(model_catalog.ModelValueError) as e:
        model_catalog.update_model_provider("bailian", {"models": ["qwen-max"]})
    assert "原默认模型" in str(e.value)
    # 同时指定新的就应该放行
    out = model_catalog.update_model_provider(
        "bailian", {"models": ["qwen-max"], "default_model": "qwen-max"})
    assert out["models"] == ["qwen-max"] and out["default_model"] == "qwen-max"


@pytest.mark.parametrize("bad", [
    [],                       # 空数组
    ["qwen-plus", "qwen-plus"],  # 重复
    ["  "],                   # 纯空白项
    ["a", 1],                 # 非字符串
    "qwen-plus",              # 不是数组
])
def test_update_rejects_bad_models_lists(db, bad):
    with pytest.raises(model_catalog.ModelValueError):
        model_catalog.update_model_provider("bailian", {"models": bad})


def test_update_rejects_empty_default_model_when_models_are_empty(db):
    """platform 这类「本来就没有模型」的行，允许 default_model 为空串。"""
    out = model_catalog.update_model_provider("platform", {"default_model": ""})
    assert out["default_model"] == ""


@pytest.mark.parametrize("bad", [True, False, 2, -1, "1", None])
def test_update_rejects_enabled_outside_0_or_1(db, bad):
    """bool 必须显式拒：Python 里 True == 1，`{"enabled": true}` 会变成上架。"""
    if bad is None:
        with pytest.raises(model_catalog.ModelValueError):
            model_catalog.update_model_provider("bailian", {"enabled": None})
    else:
        with pytest.raises(model_catalog.ModelValueError):
            model_catalog.update_model_provider("bailian", {"enabled": bad})


@pytest.mark.parametrize("bad", [
    "ftp://x.com", "ws://x.com", "not-a-url",
    "https://user:pw@x.com/v1",
])
def test_update_rejects_bad_base_url(db, bad):
    with pytest.raises(model_catalog.ModelValueError):
        model_catalog.update_model_provider("bailian", {"base_url": bad})


def test_update_accepts_an_empty_base_url(db):
    """清空端点是合法操作（标为「不是真厂商」的行就靠这个）。"""
    out = model_catalog.update_model_provider("custom", {"base_url": ""})
    assert out["base_url"] == ""


@pytest.mark.parametrize("bad", [True, 1.5, "3", 1001, -1001])
def test_update_rejects_out_of_domain_sort_order(db, bad):
    with pytest.raises(model_catalog.ModelValueError):
        model_catalog.update_model_provider("bailian", {"sort_order": bad})


def test_update_rejects_unknown_and_empty_fields(db):
    with pytest.raises(model_catalog.ModelValueError) as e1:
        model_catalog.update_model_provider("bailian", {"api_key": "sk-xxx"})
    assert "不可改" in str(e1.value), "传了不可改字段必须报错，不能静默忽略"
    with pytest.raises(model_catalog.ModelValueError):
        model_catalog.update_model_provider("bailian", {})


def test_update_rejects_empty_label(db):
    with pytest.raises(model_catalog.ModelValueError):
        model_catalog.update_model_provider("bailian", {"label": "   "})


def test_update_unknown_provider_returns_none(db):
    """返回 None 而不是抛错 —— 路由层据此回 404，且不静默成功。"""
    assert model_catalog.update_model_provider("nope", {"label": "x"}) is None


def test_update_leaves_untouched_fields_alone(db):
    """PATCH 语义：没传的键不该被清空。"""
    before = model_catalog.get_model_provider("bailian", only_enabled=False)
    out = model_catalog.update_model_provider("bailian", {"label": "只改这一项"})
    for key in ("base_url", "default_model", "models", "hint", "is_real"):
        assert out[key] == before[key], f"PATCH 把没传的 {key} 也动了"


# ── 端点层 ────────────────────────────────────────────────────

def test_patch_endpoint_changes_the_row(client_app, db):
    client, admin_id = client_app
    r = client.patch("/api/admin/models/moonshot", json={"label": "月之暗面"},
                     headers=_admin_hdr(admin_id))
    assert r.status_code == 200, f"{r.status_code}：{r.text}"
    body = r.json()
    assert body["item"]["label"] == "月之暗面", "端点回显了请求值而不是回读值"
    assert model_catalog.get_model_provider(
        "moonshot", only_enabled=False)["label"] == "月之暗面"
    assert body["platform_default"] == "moonshot-v1-32k", (
        "响应里带了 platform_default，是为了前端改完能立刻看到影响面"
    )


def test_patch_endpoint_returns_400_on_bad_value(client_app, db):
    client, admin_id = client_app
    r = client.patch("/api/admin/models/bailian", json={"default_model": "nope"},
                     headers=_admin_hdr(admin_id))
    assert r.status_code == 400, f"值域非法应为 400，实得 {r.status_code}：{r.text}"


def test_patch_endpoint_returns_404_for_unknown_provider(client_app, db):
    client, admin_id = client_app
    r = client.patch("/api/admin/models/nope", json={"label": "x"},
                     headers=_admin_hdr(admin_id))
    assert r.status_code == 404, f"厂商不存在应为 404，实得 {r.status_code}：{r.text}"


def test_patch_endpoint_requires_admin(client_app, db):
    """正反对照：非管理员 403、未登录 401。"""
    client, admin_id = client_app
    other = database.create_user("plain@example.com", "h")
    plain = auth.create_token(other["id"], other["email"])

    # 正向：管理员 200
    ok = client.patch("/api/admin/models/deepseek", json={"label": "DeepSeek"},
                      headers=_admin_hdr(admin_id))
    # 反向：非管理员 403
    no = client.patch("/api/admin/models/deepseek", json={"label": "x"},
                      headers=auth_headers(plain))
    # 反向：未登录 401
    anon = client.patch("/api/admin/models/deepseek", json={"label": "x"})
    assert ok.status_code == 200, f"管理员应得 200，实得 {ok.status_code}"
    assert no.status_code == 403, f"非管理员应得 403，实得 {no.status_code}"
    assert anon.status_code == 401, f"未登录应得 401，实得 {anon.status_code}"
    # 正向那次必须真生效，否则「全员 403」也能让上面两条绿
    assert model_catalog.get_model_provider(
        "deepseek", only_enabled=False)["label"] == "DeepSeek"


def test_patch_endpoint_never_returns_a_credential(client_app, db):
    client, admin_id = client_app
    r = client.patch("/api/admin/models/bailian", json={"label": "百炼"},
                     headers=_admin_hdr(admin_id))
    raw = r.text.lower()
    for w in ("api_key", "apikey", "token", "secret", "password"):
        assert w not in raw, f"响应体里出现了 {w}"


# ── HTTP 边界的未知字段 ──────────────────────────────────────
#
# 数据层的 EDITABLE_FIELDS 会拒不可改字段，但在 HTTP 边界那道拦截
# **曾经根本走不到**：pydantic 默认悄悄丢掉未声明的键，于是
# {"api_key": ..., "label": ...} 只剩 label 生效并返回 200。
# 下面两条把这个洞钉住。

def test_patch_endpoint_rejects_an_unknown_field(client_app, db):
    """整包只有不可改字段：必须报错，且报错原因不能是「请求体为空」。"""
    client, admin_id = client_app
    before = model_catalog.get_model_provider("bailian", only_enabled=False)

    r = client.patch("/api/admin/models/bailian", json={"api_key": "sk-nope"},
                     headers=_admin_hdr(admin_id))

    assert r.status_code == 422, (
        f"未声明字段应被 pydantic 拒为 422，实得 {r.status_code}：{r.text}")
    after = model_catalog.get_model_provider("bailian", only_enabled=False)
    assert after == before, "被拒的请求不应留下任何改动"


def test_patch_endpoint_does_not_partially_apply_a_mixed_body(client_app, db):
    """合法字段 + 不可改字段混在一起：整包拒，**合法的那个也不能生效**。

    这是「静默成功」的真正形态——调用方看到 200，以为两件事都做了。
    只断状态码不够，必须回读确认 label 也没变。
    """
    client, admin_id = client_app
    before = model_catalog.get_model_provider("bailian", only_enabled=False)

    r = client.patch("/api/admin/models/bailian",
                     json={"label": "改了但你看不见", "api_key": "sk-nope"},
                     headers=_admin_hdr(admin_id))

    assert r.status_code == 422, (
        f"混合请求应整包拒为 422，实得 {r.status_code}：{r.text}")
    after = model_catalog.get_model_provider("bailian", only_enabled=False)
    assert after["label"] == before["label"], (
        f"合法字段被部分写入了：{before['label']} -> {after['label']}")


def test_patch_endpoint_rejects_an_empty_body_with_400(client_app, db):
    """空包与「只有不可改字段」是两回事，诊断文案必须能分开。

    实测踩过：多传一个 api_key 时，pydantic 先把键丢掉，请求体真的成了空的，
    于是回的是「请求体为空」——原因指错了地方。
    """
    client, admin_id = client_app
    r = client.patch("/api/admin/models/bailian", json={},
                     headers=_admin_hdr(admin_id))
    assert r.status_code == 400, f"空包应得 400，实得 {r.status_code}：{r.text}"
    assert "空" in r.text, f"空包的文案要说清是空，实得：{r.text}"


# ── 漂移守卫不能反着长 ────────────────────────────────────────
#
# 曾经无论 patch 带不带 models，只要库里 default_model 已经漂移出 models，
# 任何编辑都 400 —— 包括**下架**这个管理员对着一行脏数据最想做的事。
# 存量漂移不是这次请求造成的，不该由这次请求来背。

def _make_drifted(db, provider_id="bailian"):
    """直接把 default_model 指到一个不存在的模型名，造出脏状态。"""
    with database.get_db() as conn:
        conn.execute(
            "UPDATE model_providers SET default_model = ? WHERE id = ?",
            ("ghost-model", provider_id),
        )


def _admin_row(provider_id):
    """管理视图回读。公开投影里没有 enabled / sort_order，
    用 get_model_provider 会 KeyError —— 那不是 bug，是投影在收窄字段。"""
    for m in model_catalog.list_model_providers(only_enabled=False, admin_view=True):
        if m["id"] == provider_id:
            return m
    raise AssertionError(f"清单里没有 {provider_id}")


def test_drift_does_not_block_unrelated_edits(db):
    """default 已漂移出 models：改显示名、下架、改排序都应当照常成功。"""
    _make_drifted(db)
    for patch in ({"label": "只改显示名"}, {"enabled": 0}, {"sort_order": 7}):
        model_catalog.update_model_provider("bailian", patch)
    row = _admin_row("bailian")
    assert row["label"] == "只改显示名"
    assert row["enabled"] == 0
    assert row["sort_order"] == 7


def test_drift_is_still_blocked_when_the_patch_replaces_the_model_list(db):
    """**换列表**才是漂移的成因，这次请求要负责——守卫必须仍在。"""
    _make_drifted(db)
    with pytest.raises(model_catalog.ModelValueError):
        model_catalog.update_model_provider("bailian", {"models": ["a", "b"]})


def test_drift_can_be_repaired_by_naming_a_new_default(db):
    """给得出显式 default_model 就能修好——这是留给运维的出口，不能一起堵死。"""
    _make_drifted(db)
    model_catalog.update_model_provider(
        "bailian", {"models": ["a", "b"], "default_model": "a"})
    row = model_catalog.get_model_provider("bailian", only_enabled=False)
    assert row["default_model"] == "a"
    assert row["models"] == ["a", "b"]


# ── 键出现 vs 键缺席 ──────────────────────────────────────────
#
# BM5 变异（model_dump(exclude_unset=True) 改成 exclude_none）曾经全绿：
# 「传了 null」和「没传」这两种 patch 语义当时没有任何测试区分它们。

def test_explicit_null_clears_the_field(db):
    """显式传 null = 清空这一项（hint/base_url 允许为空）。"""
    assert model_catalog.get_model_provider(
        "bailian", only_enabled=False)["hint"] != ""
    model_catalog.update_model_provider("bailian", {"hint": None})
    assert model_catalog.get_model_provider(
        "bailian", only_enabled=False)["hint"] == ""


def test_omitting_the_key_leaves_the_field_alone(db):
    """不传这个键 = 不改这一项。显式 null 与缺席必须是两件事。"""
    before = model_catalog.get_model_provider("bailian", only_enabled=False)["hint"]
    assert before != "", "前置条件：hint 本来就该有值"
    model_catalog.update_model_provider("bailian", {"label": "别的改动"})
    assert model_catalog.get_model_provider(
        "bailian", only_enabled=False)["hint"] == before


# ── 「没传」不等于「传了 null」——这一层才测得出来 ──────────────
#
# exclude_unset 与 exclude_none 的差别发生在**端点的 model_dump** 上，
# 数据层拿到的已经是一个 dict 了，在那里测永远分不出两者。
# 上一版把这两条写在了数据层，变异 BM10 全绿。

def test_patch_endpoint_omitting_a_key_leaves_it_untouched(client_app, db):
    """只传 label：其余六项**一个都不该被清空**。"""
    client, admin_id = client_app
    seeded = client.patch("/api/admin/models/bailian", json={"hint": "有内容"},
                          headers=_admin_hdr(admin_id))
    assert seeded.status_code == 200
    before = _admin_row("bailian")
    assert before["hint"] == "有内容", "前置条件：先给它一个非空 hint"

    r = client.patch("/api/admin/models/bailian", json={"label": "只改名字"},
                     headers=_admin_hdr(admin_id))
    assert r.status_code == 200, r.text

    after = _admin_row("bailian")
    assert after["hint"] == before["hint"], (
        f"没传的键被清空了：{before['hint']!r} -> {after['hint']!r}")
    assert after["models"] == before["models"], "没传的 models 也被动到了"
    assert after["default_model"] == before["default_model"]
    assert after["enabled"] == before["enabled"]


def test_patch_endpoint_explicit_null_clears_the_field(client_app, db):
    """显式传 null = 清空。两种语义都要有，端点层与数据层各测一次。"""
    client, admin_id = client_app
    client.patch("/api/admin/models/bailian", json={"hint": "有内容"},
                 headers=_admin_hdr(admin_id))

    r = client.patch("/api/admin/models/bailian", json={"hint": None},
                     headers=_admin_hdr(admin_id))
    assert r.status_code == 200, r.text
    assert _admin_row("bailian")["hint"] == "", "显式 null 应当清空"
