"""`users` 表的老库升级路径（工单 #68）。

## 为什么这条路径此前一次都没跑过

``db`` 夹具每次 ``init_db()`` 造出一张**全新**的表，于是「老库缺列、靠迁移
补上」那条路径在它下面永远走不到。补列排错了顺序，全量测试照样绿，真库一升级
就 ``no such column`` 打不开（``docs/TESTING-STRATEGY.md`` §6.5）。

## 这次覆盖的是哪几条迁移

``users`` 表上今天有几条迁移在跑（``backend/database.py`` 的 ``init_db()`` 里
依次调用），它们各有各的语义，靠不同的列去承载：

  · ``_migrate_quota_columns``（:424）——额度拆分成「解析 / 追问」两个计数器，
    **expand**：只加列，旧列 ``daily_summary_count`` / ``last_summary_date``
    留着不动。
  · ``_migrate_quota_override_columns``（:444）——补单人额度覆盖两列，
    **默认 NULL**：NULL 是「回落全局上限」，0 是「一条都不能用」。
  · ``_migrate_admin_column``（:467）——补 ``is_admin``，
    **NOT NULL DEFAULT 0**：没有「未设置」这个状态。
  · ``_migrate_history_favorite_column``（:911）——在 ``parse_history`` 上补
    ``is_favorite``（不是 users 表，但落在同一次升级里）。

额度那几列**此前**有一份专门的断言（``test_quota_split.py`` 造过一张老 users
表），但它只钉住了额度列有没有补齐；``is_admin`` 与覆盖两列在同一次升级里跑，
却**没有任何用例**：「列补上了」与「升级之后这个库还能用」是两件事。

## 老库长什么样：从提交历史里取，不凭记忆

``users`` 表诞生于 ``afab2b6``（``feat: 恢复仓库 - VidDownAI 全量代码``），
当时的 DDL 抄在 ``_LEGACY_USERS_DDL`` 下面，与该提交逐字一致。差异都是**查
出来的**，不是猜的：

  · 缺 ``is_admin``
  · 缺 ``parse_limit_override`` / ``chat_limit_override``
  · 缺额度拆分那四列
  · **有** ``daily_summary_count`` / ``last_summary_date`` —— 那时它们就是
    唯一的额度计数器，老库里**有存量数据**（``daily_summary_count`` 不为 0）。

## 判据读的是外部可观察的东西

升级后的库里**老行长什么样**、**管理员那条路还能不能走**、**额度覆盖还能不能
写与读**、**重复升级会不会把数据改坏**。不测私有函数，不断言迁移函数的调用
顺序。

## 明确不测的

  · 真实上一版本的 ``app.db``：仓库没有 tag / release，取不到那份文件，
    只能取「结构上等价」的老库（``docs/TESTING-STRATEGY.md`` §6.5 的约定）。
  · ``videos`` 表的老库升级：那是 ``test_videos_legacy_upgrade.py`` 的地盘。
"""
import auth
import database
import main as main_module
from seams import auth_headers, make_client

LEGACY_EMAIL = "old@x.com"

#: 逐字取自 `git show afab2b6:backend/database.py`（users 表诞生时的 DDL）。
_LEGACY_USERS_DDL = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_vip INTEGER DEFAULT 0,
    vip_expire_at TEXT,
    daily_summary_count INTEGER DEFAULT 0,
    last_summary_date TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
)
"""


def _columns(db):
    with db.get_db() as c:
        return {r["name"] for r in c.execute("PRAGMA table_info(users)")}


def _row(db, uid):
    with db.get_db() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row) if row else None


def _seed_legacy_users(db):
    """摆出 users 表诞生时的结构与一行存量数据，返回那行的 id。

    先 ``init_db()`` 拿一份**真实的**现行 DDL（orders 等表要齐全，外键与
    播种都依赖它们），再把 ``users`` 整张换成老样子——只换这一张，其余表保持
    现状：这正是「升级现场」的样子。

    那行数据刻意是**非管理员、非 VIP、旧额度计数器不为零**：
      · 非管理员——老账号不该因为升级而拿到权限；
      · 非 VIP——VIP 会让额度判定走「无限」那条支，测不出「回落全局上限」；
      · 旧计数器为 7——额度拆分是 expand，存量计数不能被抹掉。
    """
    db.init_db()
    with db.get_db() as c:
        c.execute("DROP TABLE users")
        c.execute(_LEGACY_USERS_DDL)
        c.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        uid = c.execute(
            "INSERT INTO users (email, password_hash, is_vip, vip_expire_at,"
            " daily_summary_count, last_summary_date)"
            " VALUES (?, 'h', 0, NULL, 7, '2026-01-01')",
            (LEGACY_EMAIL,),
        ).lastrowid
    return uid


class TestTheSeededShapeIsReallyLegacy:
    """前提自检。老库摆得不对，后面的断言全都失去意义。"""

    def test_it_lacks_the_admin_column(self, legacy_db):
        _seed_legacy_users(legacy_db)
        assert "is_admin" not in _columns(legacy_db)

    def test_it_lacks_both_override_columns(self, legacy_db):
        _seed_legacy_users(legacy_db)
        cols = _columns(legacy_db)
        assert "parse_limit_override" not in cols
        assert "chat_limit_override" not in cols

    def test_it_lacks_the_split_quota_columns(self, legacy_db):
        _seed_legacy_users(legacy_db)
        cols = _columns(legacy_db)
        assert "daily_parse_count" not in cols
        assert "last_parse_date" not in cols

    def test_it_still_has_the_old_quota_columns_with_data(self, legacy_db):
        _seed_legacy_users(legacy_db)
        cols = _columns(legacy_db)
        assert "daily_summary_count" in cols
        assert "last_summary_date" in cols


class TestUpgradeAddsWhatTheCodeReads:
    """升级后，代码要读的列必须都在。"""

    def test_init_db_adds_the_admin_column(self, legacy_db):
        _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        assert "is_admin" in _columns(legacy_db), (
            "升级后没有 is_admin —— auth.require_admin 一读就报 no such column"
        )

    def test_init_db_adds_both_override_columns(self, legacy_db):
        _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        cols = _columns(legacy_db)
        assert "parse_limit_override" in cols
        assert "chat_limit_override" in cols

    def test_init_db_adds_the_split_quota_columns(self, legacy_db):
        _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        cols = _columns(legacy_db)
        assert "daily_parse_count" in cols
        assert "daily_chat_count" in cols
        assert "last_parse_date" in cols
        assert "last_chat_date" in cols


class TestOldRowsKeepTheirMeaning:
    """老行补出来的默认值必须是**对的那个**，不是随便一个。"""

    def test_old_rows_become_non_admins(self, legacy_db):
        uid = _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        row = _row(legacy_db, uid)
        assert row["is_admin"] == 0, (
            f"老行补出来的 is_admin 是 {row['is_admin']!r}，不是 0 —— "
            "老账号因为升级拿到了管理员权限"
        )

    def test_old_rows_get_null_overrides_not_zero(self, legacy_db):
        """0 与 NULL 在额度语义里是两件事（见模块 docstring）。"""
        uid = _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        row = _row(legacy_db, uid)
        assert row["parse_limit_override"] is None, (
            "覆盖列被补成了 0 —— 这条老记录等于被封死（0 的语义是「一条都不能用」）"
        )
        assert row["chat_limit_override"] is None

    def test_old_rows_keep_their_data(self, legacy_db):
        uid = _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        row = _row(legacy_db, uid)
        assert row["email"] == LEGACY_EMAIL
        assert row["daily_summary_count"] == 7, "额度拆分把旧计数器弄丢了"
        assert row["last_summary_date"] == "2026-01-01"


class TestUpgradeKeepsTheQuotaFallback:
    """升级后，老账号的额度必须还是「回落全局上限」。"""

    def test_the_limit_falls_back_to_the_global_one(self, legacy_db):
        uid = _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        limit = legacy_db.quota_limit("parse", uid)
        assert limit == legacy_db.DAILY_PARSE_LIMIT, (
            f"升级后老账号的解析上限是 {limit}，不是全局上限 "
            f"{legacy_db.DAILY_PARSE_LIMIT} —— 覆盖列被补成了 0"
        )

    def test_the_user_can_still_parse(self, legacy_db):
        uid = _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        allowed, remaining = legacy_db.check_quota_kind(uid, "parse")
        assert allowed is True
        assert remaining == legacy_db.DAILY_PARSE_LIMIT

    def test_an_override_written_after_upgrade_takes_effect(self, legacy_db):
        uid = _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        with legacy_db.get_db() as c:
            c.execute(
                "UPDATE users SET parse_limit_override = ? WHERE id = ?", (5, uid)
            )
        assert legacy_db.quota_limit("parse", uid) == 5


class TestUpgradeKeepsTheAdminPathWorking:
    """管理后台那条路要能真的走通——它读 is_admin 与覆盖两列。"""

    def test_admin_route_refuses_an_upgraded_non_admin(self, legacy_db):
        uid = _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        client = make_client(main_module.app)
        token = auth.create_token(uid, LEGACY_EMAIL)
        r = client.get("/api/admin/users", headers=auth_headers(token))
        assert r.status_code == 403, (
            f"老库升级出来的账号拿到了管理员权限：{r.status_code} {r.text[:200]}"
        )

    def test_admin_route_works_after_promotion(self, legacy_db):
        """反向对照：上一条若只是「所有请求都 403」，这一条会红。"""
        uid = _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        with legacy_db.get_db() as c:
            c.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (uid,))
        client = make_client(main_module.app)
        token = auth.create_token(uid, LEGACY_EMAIL)
        r = client.get("/api/admin/users", headers=auth_headers(token))
        assert r.status_code == 200, r.text[:300]
        items = r.json()["items"]
        assert any(item["email"] == LEGACY_EMAIL for item in items), (
            "升级后的账号升了管理员，却在列表里看不到自己那一行"
        )


class TestUpgradeIsIdempotent:
    """init_db 每次启动都跑；重复跑不许把数据改坏。"""

    def test_running_init_db_twice_changes_nothing(self, legacy_db):
        uid = _seed_legacy_users(legacy_db)
        legacy_db.init_db()
        legacy_db.init_db()
        legacy_db.init_db()
        row = _row(legacy_db, uid)
        assert row is not None, "重复 init_db 把老行弄没了"
        assert row["is_admin"] == 0
        assert row["parse_limit_override"] is None
        assert row["daily_summary_count"] == 7

    def test_a_current_shaped_table_is_left_alone(self, legacy_db):
        """另一头：已经是现行结构的库（所有开发机与新部署）不许被迁移改动。"""
        legacy_db.init_db()
        with legacy_db.get_db() as c:
            uid = c.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, 'h')",
                ("new@x.com",),
            ).lastrowid
        legacy_db.init_db()
        row = _row(legacy_db, uid)
        assert row["is_admin"] == 0
        assert row["parse_limit_override"] is None
        assert row["chat_limit_override"] is None
