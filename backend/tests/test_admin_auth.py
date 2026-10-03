"""管理后台地基（工单 #11）：is_admin + require_admin + JWT_SECRET 缺失即失败。

三条硬规矩，本文件逐条遵守：

1. **走真实 HTTP 层**（seams.make_client），且**不触发 lifespan**。
   make_client 不覆写鉴权依赖，所以带不带 Authorization 头决定身份。
   这里刻意不用 `with client:`：那会触发 lifespan → init_db() → 重建 schema，
   迁移类断言就会被「夹具把护栏重建了一遍」遮住（工单 #7 的假护栏就是这么来的）。
   本文件挂的探针 app **没有 lifespan**，迁移断言直接查 sqlite_master / PRAGMA。

2. **每条安全断言都有正反双向对照**。只测「非管理员 403」是不够的——
   「所有请求都 401」同样能让它绿。所以同一个 token 在 is_admin=0 时 403、
   置 1 后 200，两条都在同一文件里，任何「鉴权整体死掉」的改法都会撞红。

3. **只断言外部可观察的结果**：HTTP 状态码、响应体、PRAGMA 输出、进程退出码。
   不测私有函数、不断言内部调用顺序。
"""
import base64
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI

import auth
import database
from seams import auth_headers, close_all_thread_connections, make_client

#: backend 目录。子进程要用它才能 import 到 auth / database。
ROOT_BACKEND = Path(__file__).resolve().parent.parent


# ── 探针 app：只挂一个受 require_admin 保护的路由 ──────────────
#
# 为什么在测试里现搭而不复用 main.app：管理端点本身（api_admin.py）不属于
# 本工单。这一条路由走的是**真的** require_admin 依赖与真的鉴权链，
# 测的仍是「依赖在 HTTP 层被解析」这件要测的事。
# 刻意不给它 lifespan：任何 init_db() 都不该在断言期间发生。

def _build_admin_app() -> FastAPI:
    app = FastAPI()

    @app.get("/admin/_probe")
    async def probe(user: dict = Depends(auth.require_admin)):
        # 回显 DB 行里的字段：能区分「真的读到了用户」和「返回了个常量」。
        return {"email": user["email"], "is_admin": user["is_admin"]}

    return app


def _set_admin(user_id: int, value: int) -> None:
    with database.get_db() as conn:
        conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (value, user_id))


def _is_admin(user_id: int):
    with database.get_db() as conn:
        row = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    return None if row is None else row["is_admin"]


@pytest.fixture()
def probe_client(db, make_user):
    """真实 client（走真实鉴权依赖）+ 两个已存在的账号。"""
    admin_id = make_user(email="admin@example.com")
    plain_id = make_user(email="plain@example.com")
    return make_client(_build_admin_app()), admin_id, plain_id


def _get(client, user_id, email):
    return client.get("/admin/_probe", headers=auth_headers(auth.create_token(user_id, email)))


# ── AC 1：管理员 token → 200，且返回真实数据 ──────────────────

def test_admin_token_gets_200_with_real_row(probe_client, db):
    client, admin_id, _ = probe_client
    _set_admin(admin_id, 1)
    with client:
        r = _get(client, admin_id, "admin@example.com")
    assert r.status_code == 200, f"管理员应得 200，实得 {r.status_code}：{r.text}"
    body = r.json()
    # 非空 + 是**这个**账号的那一行，不是常量、不是别人的行
    assert body == {"email": "admin@example.com", "is_admin": 1}, body


# ── AC 2：非管理员 token → 403 ────────────────────────────────

def test_non_admin_token_gets_403(probe_client, db):
    client, _, plain_id = probe_client
    with client:
        r = _get(client, plain_id, "plain@example.com")
    assert r.status_code == 403, f"非管理员应得 403，实得 {r.status_code}：{r.text}"


# ── AC 3：不带 token → 401（不是 403）────────────────────────

def test_missing_token_gets_401_not_403(probe_client, db):
    client, admin_id, _ = probe_client
    _set_admin(admin_id, 1)
    with client:
        r = client.get("/admin/_probe")
    assert r.status_code == 401, f"缺 token 应得 401，实得 {r.status_code}：{r.text}"
    # 401 与 403 都表示「进不来」，但含义不同：401 是没证明身份，403 是证明了但不够。
    # 混淆两者会让前端把「请重新登录」提示给一个已登录的普通用户。
    assert r.status_code != 403


# ── AC 4：伪造 / 篡改签名的 token → 401 ───────────────────────

def test_token_signed_with_wrong_key_gets_401(probe_client, db):
    client, admin_id, _ = probe_client
    _set_admin(admin_id, 1)
    forged = pyjwt.encode(
        {
            "sub": str(admin_id),
            "email": "admin@example.com",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "iat": datetime.now(timezone.utc),
        },
        "attacker-does-not-know-the-secret",
        algorithm=auth.JWT_ALGORITHM,
    )
    with client:
        r = client.get("/admin/_probe", headers=auth_headers(forged))
    assert r.status_code == 401, f"伪造签名应得 401，实得 {r.status_code}：{r.text}"


def test_tampered_signature_gets_401(probe_client, db):
    client, admin_id, _ = probe_client
    _set_admin(admin_id, 1)
    valid = auth.create_token(admin_id, "admin@example.com")
    head, payload, signature = valid.split(".")
    # 篡改 **payload**（抬高位）而不是签名的末位字符。
    #
    # 为什么不能翻末位：32 字节 HS256 编成 43 个 base64url 字符，最后一个
    # 字符只有 4 位是有效数据（43*6=258 位 > 256 位），另外 2 位是填充。
    # 换一个末位字符有约 1/16 的概率**解码回同一组字节** —— 也就是
    # 「篡改」等于没篡改，签名照旧合法，于是 200 而不是 401。
    # 实测 2000 次里出现 23 次假红（≈1.15%），而且它只在换到那两个特定字符
    # 时才发作，看起来就是「偶发红、单独跑又全过」。
    #
    # 改 payload 则没有任何随机性：payload 一变，签名必然对不上。
    # 这也正是它该守的攻击：伪造 claim（本例是把自己抬成管理员），
    # 与上面 test_forged_signature_gets_401 守的「拿错密钥签名」是两类。
    import base64

    def _b64d(seg: str) -> bytes:
        return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))

    claims = json.loads(_b64d(payload))
    claims["role"] = "admin"
    tampered_payload = (
        base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    )
    with client:
        r = client.get("/admin/_probe", headers=auth_headers(f"{head}.{tampered_payload}.{signature}"))
    assert r.status_code == 401, f"篡改签名应得 401，实得 {r.status_code}：{r.text}"


# ── AC 5 / 6：提权与撤权即时生效（不重新登录）────────────────
#
# 这两条是 ADR 0010 的核心主张，也是本设计唯一的理由：
# 谁把 is_admin 塞进 JWT payload，下面的测试立刻红。

def test_promotion_takes_effect_on_next_request_without_relogin(probe_client, db):
    client, admin_id, _ = probe_client
    token = auth.create_token(admin_id, "admin@example.com")
    headers = auth_headers(token)

    before = client.get("/admin/_probe", headers=headers)
    assert before.status_code == 403, f"提权前应得 403，实得 {before.status_code}"

    _set_admin(admin_id, 1)

    # 同一个 token，不重新登录
    after = client.get("/admin/_probe", headers=headers)
    assert after.status_code == 200, f"提权应立即生效，实得 {after.status_code}：{after.text}"
    assert token == auth.create_token(admin_id, "admin@example.com"), "对照组：token 未变"


def test_revocation_takes_effect_on_next_request_without_relogin(probe_client, db):
    client, admin_id, _ = probe_client
    _set_admin(admin_id, 1)
    token = auth.create_token(admin_id, "admin@example.com")
    headers = auth_headers(token)

    before = client.get("/admin/_probe", headers=headers)
    assert before.status_code == 200, f"撤权前应得 200，实得 {before.status_code}"

    _set_admin(admin_id, 0)

    after = client.get("/admin/_probe", headers=headers)
    assert after.status_code == 403, f"撤权应立即生效，实得 {after.status_code}：{after.text}"


# ── AC 7 / 8：JWT_SECRET 缺失即启动失败 ───────────────────────
#
# 必须起真进程验：这条 AC 的可观察结果是**进程起不来**，
# 在 pytest 进程里 monkeypatch 验不到（conftest 已经把密钥设好了）。

_CHILD_SOURCE = """
import auth
token = auth.create_token(1, "a@b.co")
assert auth.decode_token(token)["sub"] == 1
print("IMPORT_OK")
"""


def _import_auth_in_subprocess(tmp_path, env_overrides):
    """在干净的子进程里 import auth，返回 CompletedProcess。

    JWT_SECRET 一律先从环境里删掉，再按需塞入——否则开发机上的真实配置
    会让「缺失」那条 AC 假绿。
    """
    script = tmp_path / "child_import_auth.py"
    script.write_text(_CHILD_SOURCE, encoding="utf-8")
    env = dict(os.environ)
    env.pop("JWT_SECRET", None)
    # 子进程从脚本所在目录（tmp_path）起 sys.path，必须显式给 backend 路径
    env["PYTHONPATH"] = str(ROOT_BACKEND)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(script)],
        env=env, capture_output=True, text=True, timeout=120,
    )


def test_missing_jwt_secret_makes_process_fail_to_start(tmp_path):
    proc = _import_auth_in_subprocess(tmp_path, {})
    assert proc.returncode != 0, f"缺 JWT_SECRET 时进程应启动失败，却正常退出了：\n{proc.stdout}"
    assert "JWT_SECRET" in proc.stderr, f"报错信息应点名 JWT_SECRET，实际：\n{proc.stderr}"
    assert "IMPORT_OK" not in proc.stdout, "import 竟然成功了"


def test_blank_jwt_secret_counts_as_missing(tmp_path):
    """纯空白视同未配置——否则 'JWT_SECRET= ' 能绕过这道闸。"""
    proc = _import_auth_in_subprocess(tmp_path, {"JWT_SECRET": "   "})
    assert proc.returncode != 0, f"纯空白的 JWT_SECRET 应视为缺失，却通过了：\n{proc.stdout}"


def test_present_jwt_secret_starts_normally(tmp_path):
    proc = _import_auth_in_subprocess(tmp_path, {"JWT_SECRET": "a-secret-set-for-this-test"})
    assert proc.returncode == 0, f"配了 JWT_SECRET 应正常启动：\n{proc.stderr}"
    assert "IMPORT_OK" in proc.stdout


# ── AC 9：迁移幂等，且老库真的被补上列 ────────────────────────
#
# 断言直接读 PRAGMA，**不靠发请求触发**：本文件的探针 app 没有 lifespan，
# 这一整段没有任何 init_db 被「夹具顺手重建」掩盖。
# 用裸 sqlite3 读，不走 database.get_db()，避免污染线程本地的连接缓存。

def _user_columns(path):
    with sqlite3.connect(str(path)) as conn:
        return [(row[1], row[3]) for row in conn.execute("PRAGMA table_info(users)")]


LEGACY_USERS_DDL = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_vip INTEGER DEFAULT 0,
        vip_expire_at TEXT
    );
"""


def test_migration_backfills_legacy_table_and_is_idempotent(db, tmp_path, monkeypatch):
    """老库（建表时没有 is_admin）跑两次 init_db：不报错、只加一次、老行为 0。"""
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(str(legacy)) as conn:
        conn.executescript(LEGACY_USERS_DDL)
        conn.execute("INSERT INTO users (email, password_hash) VALUES ('old@example.com','h')")

    # 本测试会中途改 DB_PATH，因此借 db 夹具的连接清理纪律。
    # 顺序照 db 夹具：先指路径，再清连接，最后 init_db。
    monkeypatch.setattr(database, "DB_PATH", str(legacy))
    close_all_thread_connections()

    database.init_db()
    database.init_db()  # 第二次必须不抛 duplicate column name

    names = [name for name, _ in _user_columns(legacy)]
    assert names.count("is_admin") == 1, f"is_admin 应恰好一列，实得 {names}"
    notnull = dict(_user_columns(legacy))["is_admin"]
    assert notnull == 1, f"is_admin 应是 NOT NULL，PRAGMA notnull={notnull}"

    with sqlite3.connect(str(legacy)) as conn:
        value = conn.execute("SELECT is_admin FROM users WHERE email='old@example.com'").fetchone()[0]
    assert value == 0, f"加列不得把老账号变成管理员，老行 is_admin={value}"


def test_fresh_schema_has_is_admin_not_null_default_0(db):
    """新建库（走 CREATE TABLE 分支）同样要有这一列。"""
    columns = dict(_user_columns(db.DB_PATH))
    assert "is_admin" in columns, f"新建库缺 is_admin：{list(columns)}"
    assert columns["is_admin"] == 1, "is_admin 应是 NOT NULL"


# ── AC 10 / 11：播种只作用于列表内账号 ────────────────────────

def test_seed_promotes_only_listed_accounts(db, make_user, monkeypatch):
    listed = make_user(email="boss@example.com")
    unlisted = make_user(email="intern@example.com")
    monkeypatch.setenv("VIDDIGEST_ADMIN_EMAILS", "boss@example.com")

    database.seed_admin_emails_from_env()

    assert _is_admin(listed) == 1, "列表内的账号应被置为管理员"
    assert _is_admin(unlisted) == 0, "不在列表里的账号**不能**被置为管理员"


def test_seed_without_env_is_a_no_op_not_an_error(db, make_user, monkeypatch):
    listed = make_user(email="boss@example.com")
    monkeypatch.delenv("VIDDIGEST_ADMIN_EMAILS", raising=False)

    assert database.seed_admin_emails_from_env() == 0
    assert _is_admin(listed) == 0, "未配置就一个都不该动"


def test_seed_with_blank_env_is_a_no_op(db, make_user, monkeypatch):
    """空串与纯空白都是「没配」，不是「配了一个空邮箱」。"""
    listed = make_user(email="boss@example.com")
    monkeypatch.setenv("VIDDIGEST_ADMIN_EMAILS", "  ,  ")

    assert database.seed_admin_emails_from_env() == 0
    assert _is_admin(listed) == 0


def test_seed_tolerates_spaces_around_commas(db, make_user, monkeypatch):
    """运维手写 'a@x.com, b@x.com' 是常态，那个空格不该让播种静默失效。"""
    boss = make_user(email="boss@example.com")
    second = make_user(email="second@example.com")
    monkeypatch.setenv("VIDDIGEST_ADMIN_EMAILS", " boss@example.com , second@example.com ")

    database.seed_admin_emails_from_env()

    assert _is_admin(boss) == 1
    assert _is_admin(second) == 1


def test_seed_twice_keeps_exactly_one_admin(db, make_user, monkeypatch):
    """重复播种不改变结果：每次启动都会跑它。"""
    boss = make_user(email="boss@example.com")
    intern = make_user(email="intern@example.com")
    monkeypatch.setenv("VIDDIGEST_ADMIN_EMAILS", "boss@example.com")

    database.seed_admin_emails_from_env()
    database.seed_admin_emails_from_env()

    assert _is_admin(boss) == 1
    assert _is_admin(intern) == 0


# ── 单元测试：邮箱列表解析（本仓新立的约定）───────────────────
#
# 只给纯函数写单测。逗号分隔 env 列表在本仓没有先例（_env_int 只取 int），
# 解析规则是这轮新定的，必须有单测钉住。

@pytest.mark.parametrize("raw, expected", [
    (None, []),
    ("", []),
    ("   ", []),
    ("a@x.com", ["a@x.com"]),
    ("a@x.com,b@x.com", ["a@x.com", "b@x.com"]),
    (" a@x.com , b@x.com ", ["a@x.com", "b@x.com"]),
    ("a@x.com,,b@x.com", ["a@x.com", "b@x.com"]),
    (",,", []),
    ("  boss@x.com,  ", ["boss@x.com"]),
    # 大小写**保持原样**：注册时 email 原样入库，折叠会让配错变静默命中
    ("Boss@X.com", ["Boss@X.com"]),
])
def test_parse_admin_emails(raw, expected):
    assert database._parse_admin_emails(raw) == expected


# ── 前端的管理入口判据：/api/auth/me 必须吐 is_admin ──────
#
# 为什么这组断言存在：管理后台的入口可见性最终依赖这个字段，而
# `_build_user_response` 曾经只返回 id / email / is_vip / vip_expire_at。
# 前端于是无论怎么回查都拿不到「我是不是管理员」，「管理」入口永不出现 ——
# 症状像渲染 bug，根因是接口少一个键。
#
# 正反双向：is_admin=0 时必须是 False，置 1 后必须是 True。
# 只断「键存在」不够——恒返回 False 也满足「键存在」，而那正是原症状。
def test_me_response_carries_is_admin(db, make_user):
    import api_auth

    # db 夹具指向 tmp_path 上的全新空库，得先造一个用户出来
    email = "me-probe@example.com"
    uid = make_user(email)

    payload = api_auth._build_user_response(
        {"id": uid, "email": email, "is_admin": 0,
         "is_vip": 0, "vip_expire_at": None}
    )
    assert "is_admin" in payload, (
        f"/api/auth/me 的响应里没有 is_admin，实际键集 {sorted(payload)}。"
        "前端因此无法判断该不该显示「管理」入口。"
    )
    assert payload["is_admin"] is False, (
        f"is_admin=0 的用户应得 is_admin=False，实得 {payload['is_admin']!r}"
    )

    payload = api_auth._build_user_response(
        {"id": uid, "email": email, "is_admin": 1,
         "is_vip": 0, "vip_expire_at": None}
    )
    assert payload["is_admin"] is True, (
        f"is_admin=1 的用户应得 is_admin=True，实得 {payload['is_admin']!r}"
    )


def test_me_is_admin_is_a_real_boolean_not_the_raw_column():
    """必须是 bool，不能是库里的 0/1 —— 前端要拿它做 v-if 判据。

    `0` 在 JS 里 falsy 所以能用，但类型混着会让「=== true」这类判据
    在某一侧悄悄失效；而 None（老行没这列时）同样 falsy，三种值共用
    一个字段是迟早出事的那种设计。
    """
    import api_auth

    for raw in (0, 1):
        payload = api_auth._build_user_response(
            {"id": 1, "email": "a@b.c", "is_admin": raw,
             "is_vip": 0, "vip_expire_at": None}
        )
        assert isinstance(payload["is_admin"], bool), (
            f"库里 is_admin={raw} 时响应给的是 {type(payload['is_admin']).__name__}，"
            "不是 bool"
        )

    # 老行没有该列时也必须是 False，而不是 None 或抛 KeyError
    payload = api_auth._build_user_response(
        {"id": 1, "email": "a@b.c", "is_vip": 0, "vip_expire_at": None}
    )
    assert payload["is_admin"] is False, (
        f"缺 is_admin 键时应回落 False，实得 {payload['is_admin']!r}"
    )


def test_me_endpoint_over_http_exposes_is_admin(db, make_user):
    """走真实 HTTP 层：确认它真的进了响应体，不只是函数返回了。

    刻意**不**用 `with client:` —— 那会触发 lifespan -> init_db() ->
    重建 schema。本文件第 1 条规矩就是这个：不靠夹具把护栏重搭一遍。
    路由本身不需要 lifespan 才能解析（鉴权发生在依赖层，见文件头）。
    """
    import auth as auth_mod
    import main as main_module

    email = "me-http@example.com"
    uid = make_user(email)

    client = make_client(main_module.app)
    for db_value, expected in ((0, False), (1, True)):
        _set_admin(uid, db_value)
        token = auth_mod.create_token(uid, email)
        r = client.get("/api/auth/me", headers=auth_headers(token))
        assert r.status_code == 200, f"GET /api/auth/me -> {r.status_code}：{r.text}"
        # 响应是包了一层的 {"success": true, "data": {...}}。
        # 前端 api/auth.js 的 fetchMe() 正是取 res.data.data 再存进
        # localStorage，所以断的是 data 那一层，不是平铺的顶层。
        body = r.json()
        assert "data" in body, f"/api/auth/me 少了 data 包装层：{body}"
        user = body["data"]
        assert "is_admin" in user, (
            f"/api/auth/me 的 data 里没有 is_admin，实际 {user}。"
            "管理入口的判据就断在这里。"
        )
        assert user["is_admin"] is expected, (
            f"库里 is_admin={db_value}，响应却给 is_admin={user['is_admin']!r}"
        )
