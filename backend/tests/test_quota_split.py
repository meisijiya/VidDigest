"""额度拆分：解析额度与对话额度是两个独立计数器（工单 #4）。

与 test_quota.py 的分工：那边锁的是「单一额度」的既有行为，
这边锁的是拆分后的新行为。两者并存，不互相替代——
同一个仓库里两种写法并存，是工单 #2 明确认可的状态。
"""
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

import database


def _limits_in_fresh_interpreter(**env) -> tuple[int, int]:
    """在带指定环境变量的全新解释器里读出 (DAILY_PARSE_LIMIT, DAILY_CHAT_LIMIT)。

    必须开子进程：两个上限都是 import 时求值的模块常量，
    同一进程内改 os.environ 不会让它们重新求值。
    """
    backend = pathlib.Path(__file__).resolve().parent.parent
    child_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("VIDDIGEST_DAILY_PARSE_LIMIT", "VIDDIGEST_DAILY_CHAT_LIMIT")
    }
    child_env.update(env)
    proc = subprocess.run(
        [sys.executable, "-c",
         "import database; print(database.DAILY_PARSE_LIMIT, database.DAILY_CHAT_LIMIT)"],
        cwd=str(backend), env=child_env,
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, f"子进程读取上限失败：{proc.stderr}"
    parse, chat = proc.stdout.split()
    return int(parse), int(chat)


def _counts(uid):
    """一次读出两个计数器的当前值。"""
    with database.get_db() as c:
        row = c.execute(
            "SELECT daily_parse_count, daily_chat_count FROM users WHERE id=?", (uid,)
        ).fetchone()
    return row["daily_parse_count"], row["daily_chat_count"]


class TestTwoIndependentCounters:
    def test_fresh_user_has_both_counters(self, db, make_user):
        uid = make_user()
        assert db.check_quota(uid) == {
            "parse": (True, database.DAILY_PARSE_LIMIT),
            "chat": (True, database.DAILY_CHAT_LIMIT),
        }

    def test_parse_and_chat_counters_are_separate_columns(self, db, make_user):
        uid = make_user()
        assert _counts(uid) == (0, 0), "两个计数器应各自独立存在"

        db.consume_quota(uid, "parse")
        assert _counts(uid) == (1, 0), "扣解析额度不应动对话额度"

        db.consume_quota(uid, "chat")
        assert _counts(uid) == (1, 1), "扣对话额度不应动解析额度"

    def test_parsing_exhausts_only_parse_quota(self, db, make_user):
        uid = make_user()
        for _ in range(database.DAILY_PARSE_LIMIT):
            db.consume_quota(uid, "parse")

        assert db.check_quota(uid)["parse"][0] is False
        assert db.check_quota(uid)["chat"] == (True, database.DAILY_CHAT_LIMIT), (
            "解析额度用完，不该影响对话额度"
        )

    def test_chatting_exhausts_only_chat_quota(self, db, make_user):
        uid = make_user()
        for _ in range(database.DAILY_CHAT_LIMIT):
            db.consume_quota(uid, "chat")

        assert db.check_quota(uid)["chat"][0] is False
        assert db.check_quota(uid)["parse"] == (True, database.DAILY_PARSE_LIMIT), (
            "对话额度用完，不该影响解析额度"
        )

    def test_chat_limit_can_differ_from_parse_limit(self, db, make_user):
        """两个上限是独立的值，不是同一个常量换个名字。"""
        assert database.DAILY_PARSE_LIMIT != database.DAILY_CHAT_LIMIT, (
            "解析与对话上限应有各自的值；相等会让「拆成两个计数器」失去意义"
        )

    def test_limits_come_from_environment(self, db, make_user, monkeypatch):
        """上限从环境变量读取，改配置后行为随之改变。"""
        monkeypatch.setattr(database, "DAILY_PARSE_LIMIT", 1)
        monkeypatch.setattr(database, "DAILY_CHAT_LIMIT", 7)
        uid = make_user()

        assert db.check_quota(uid)["parse"] == (True, 1)
        assert db.check_quota(uid)["chat"] == (True, 7)

        assert db.consume_quota(uid, "parse") == 0
        assert db.check_quota(uid)["parse"] == (False, 0)
        assert db.check_quota(uid)["chat"] == (True, 7), "解析上限改了不该波及对话"

    def test_unknown_user_refused(self, db):
        assert db.check_quota(9999) == {"parse": (False, 0), "chat": (False, 0)}
        assert db.consume_quota(9999, "parse") == 0
        assert db.consume_quota(9999, "chat") == 0

    def test_check_never_writes(self, db, make_user):
        """判定是只读的——扣减与判定拆开是「字幕失败不扣额度」的前提。"""
        uid = make_user()
        db.check_quota(uid)
        db.check_quota(uid)
        assert _counts(uid) == (0, 0)
        with database.get_db() as c:
            row = c.execute(
                "SELECT last_parse_date, last_chat_date FROM users WHERE id=?", (uid,)
            ).fetchone()
        assert row["last_parse_date"] is None and row["last_chat_date"] is None


class TestDailyResetPerCounter:
    def test_each_counter_resets_on_its_own_date(self, db, make_user):
        """两个计数器各自记日期，互不牵连。

        注意口径：check_quota 是只读的，日期变了它只是「当作满额」，
        并不真的把计数列清零——真正重置发生在下一次 consume 时。
        所以这里断言的是**判定结果**，以及 consume 之后计数确实回到 1。
        """
        uid = make_user()
        db.consume_quota(uid, "parse")
        db.consume_quota(uid, "chat")

        # 只把解析额度的日期推到昨天
        with database.get_db() as c:
            c.execute("UPDATE users SET last_parse_date='1999-01-01' WHERE id=?", (uid,))

        assert db.check_quota(uid)["parse"] == (True, database.DAILY_PARSE_LIMIT), (
            "解析额度应已按自己的日期重置"
        )

        # 真正重置发生在 consume：计数从 1 起算，而不是接着昨天的 1 往上加
        db.consume_quota(uid, "parse")
        assert _counts(uid) == (1, 1), (
            "解析重置后应从 1 起算，对话计数不该被牵连"
        )

    def test_chat_date_alone_does_not_reset_parse(self, db, make_user):
        uid = make_user()
        db.consume_quota(uid, "parse")
        db.consume_quota(uid, "chat")

        with database.get_db() as c:
            c.execute("UPDATE users SET last_chat_date='1999-01-01' WHERE id=?", (uid,))

        db.consume_quota(uid, "chat")
        assert _counts(uid) == (1, 1), "只应重置对话计数，解析计数应接着往上加"
        assert db.check_quota(uid)["parse"] == (
            True, database.DAILY_PARSE_LIMIT - 1
        ), "解析额度不该被对话额度的日期变更重置"


class TestRollback:
    def test_refund_restores_the_counter(self, db, make_user):
        """模型调用失败时把额度还回去。"""
        uid = make_user()
        before = db.check_quota(uid)["parse"]

        db.consume_quota(uid, "parse")
        assert db.check_quota(uid)["parse"] == (True, before[1] - 1)

        db.refund_quota(uid, "parse")
        assert db.check_quota(uid)["parse"] == before, "回滚后应与调用前一致"

    def test_refund_does_not_cross_contaminate(self, db, make_user):
        """回滚解析额度不该动对话额度。"""
        uid = make_user()
        db.consume_quota(uid, "parse")
        db.consume_quota(uid, "chat")

        db.refund_quota(uid, "parse")

        assert _counts(uid) == (0, 1), "只应回滚解析计数"

    def test_refund_never_goes_negative(self, db, make_user):
        """回滚到 0 就不再减——否则一次失败能让额度凭空多出来。"""
        uid = make_user()
        db.consume_quota(uid, "parse")
        db.refund_quota(uid, "parse")
        db.refund_quota(uid, "parse")
        db.refund_quota(uid, "parse")

        assert _counts(uid) == (0, 0), "超额回滚把额度加出了正数"

    def test_refund_after_reset_does_not_credit_new_day(self, db, make_user):
        """跨天后再回滚上一次的扣减，不该动那个计数。

        这里必须断言**列里的具体值**而不是「今天看到的额度」：
        日期不匹配时 check 一律返回满额，无论列里是 1 还是 0，
        所以只断言「额度没变多」的话，跨天回滚与不回滚完全无法区分。
        """
        uid = make_user()
        db.consume_quota(uid, "parse")
        with database.get_db() as c:
            c.execute("UPDATE users SET last_parse_date='1999-01-01' WHERE id=?", (uid,))

        assert database.refund_quota(uid, "parse") == database.DAILY_PARSE_LIMIT

        with database.get_db() as c:
            row = c.execute(
                "SELECT daily_parse_count FROM users WHERE id=?", (uid,)
            ).fetchone()

        # 跨天回滚等于「今天没扣过」，昨天的计数原样留着、不该被改动
        assert row["daily_parse_count"] == 1, (
            f"跨天回滚把昨天的计数改成了 {row['daily_parse_count']}，应为 1；"
            "它对今天不生效，但下次 consume 会以它为起点，"
            "被改动就等于凭空多出或少掉一次额度"
        )
        assert database.check_quota_kind(uid, "parse") == (
            True, database.DAILY_PARSE_LIMIT
        )

    def test_refund_unknown_user_is_noop(self, db):
        assert db.refund_quota(9999, "parse") == 0

    def test_refund_ignores_yesterdays_charge_when_today_unused(self, db, make_user):
        """跨天回滚**不能碰列里的计数**——今天压根还没扣过。

        断言具体值而不是「额度没变多」：日期不匹配时 check 一律返回满额，
        列里是 1 还是 0 在判定上完全看不出差别。
        """
        uid = make_user()
        db.consume_quota(uid, "parse")
        with database.get_db() as c:
            c.execute("UPDATE users SET last_parse_date='1999-01-01' WHERE id=?", (uid,))

        db.refund_quota(uid, "parse")

        with database.get_db() as c:
            row = c.execute(
                "SELECT daily_parse_count FROM users WHERE id=?", (uid,)
            ).fetchone()
        # 昨天的 1 仍留在列里（对今天不生效），但绝不能变成 0 或 -1
        assert row["daily_parse_count"] == 1, (
            f"跨天回滚把计数写成了 {row['daily_parse_count']}，应为 1；"
            "负数或 0 会让下次 consume 少扣一次，等于白送额度"
        )

    def test_refund_after_another_charge_today_is_kept(self, db, make_user):
        """跨天场景下若今天已经有扣减，回滚也不能动今天的计数。"""
        uid = make_user()
        db.consume_quota(uid, "parse")
        with database.get_db() as c:
            c.execute("UPDATE users SET last_parse_date='1999-01-01' WHERE id=?", (uid,))

        # 今天第一次扣减：计数从 1 重新起算
        db.consume_quota(uid, "parse")
        with database.get_db() as c:
            before = c.execute(
                "SELECT daily_parse_count FROM users WHERE id=?", (uid,)
            ).fetchone()["daily_parse_count"]

        db.refund_quota(uid, "parse")

        with database.get_db() as c:
            after = c.execute(
                "SELECT daily_parse_count FROM users WHERE id=?", (uid,)
            ).fetchone()["daily_parse_count"]
        assert before == 1 and after == 0, (
            f"今天的扣减应被回滚：before={before} after={after}"
        )


class TestRefundAtomicity:
    """回滚与另一次扣减交叉时，不能把那次消耗覆盖掉。

    「读-改-写」写法（先 SELECT 出 count，在 Python 里减一，再把绝对值
    UPDATE 回去）在两个动作之间留了窗口：窗口里另一次扣减把 count 加 1，
    回滚随后把算好的旧绝对值写回去，那次消耗就凭空消失了——等于白送额度。
    正确写法是让减法在数据库内部一次完成（UPDATE ... MAX(count-1, 0)），
    根本没有可交叉的窗口。
    """

    def test_refund_does_not_clobber_a_concurrent_consume(self, db, make_user):
        uid = make_user()
        db.consume_quota(uid, "parse")

        fired = {"n": 0}

        def interleave(stmt, *_rest):
            # 注入点定在回滚自己那条 UPDATE 上。sqlite3 的 trace 回调在语句
            # **执行前**触发，所以此刻外层已经把旧值读进内存、正准备写回——
            # 正好落在「读」与「写」之间。挂在 SELECT 上无效：那样外层会读到
            # 注入之后的新值，等于什么都没交错。
            #
            # 注入必须走**另一条连接**。回调是在外层那条连接的语句执行前触发的，
            # 若在同一条连接上写，内层 get_db() 的 commit/rollback 会和外层
            # 尚未落地的隐式事务缠在一起，交错根本不会发生，测试恒绿。
            if fired["n"] == 0 and stmt.strip().upper().startswith(
                "UPDATE USERS SET DAILY_PARSE_COUNT"
            ):
                fired["n"] = 1
                other = sqlite3.connect(database.get_db_path())
                try:
                    other.execute(
                        "UPDATE users SET daily_parse_count = daily_parse_count + 1 "
                        "WHERE id = ?", (uid,),
                    )
                    other.commit()
                finally:
                    other.close()

        with database.get_db() as conn:
            conn.set_trace_callback(interleave)
            try:
                db.refund_quota(uid, "parse")
            finally:
                conn.set_trace_callback(None)

        assert fired["n"] == 1, "没能把并发扣减插进读写之间，这条测试等于没测"
        assert _counts(uid)[0] == 1, (
            "1 次扣减 - 1 次回滚 + 1 次并发扣减 应为 1，"
            f"实际 {_counts(uid)[0]}（回滚覆盖掉了并发的扣减）"
        )

    def test_concurrent_refund_never_drives_count_negative(self, db, make_user):
        """多个回滚叠在一起也不能把计数压到负数。"""
        uid = make_user()
        db.consume_quota(uid, "parse")
        for _ in range(5):
            db.refund_quota(uid, "parse")
        assert _counts(uid)[0] == 0, "重复回滚把计数压到了负数"
        assert db.check_quota_kind(uid, "parse") == (True, db.DAILY_PARSE_LIMIT)


class TestEnvConfigRobustness:
    def test_bad_env_value_falls_back_to_default(self, db, monkeypatch):
        """环境变量写错时退回默认值，而不是让整个应用起不来。

        一个环境变量写错不该让整个站点不可用——那是可用性事故。
        """
        monkeypatch.setenv("VIDDIGEST_DAILY_PARSE_LIMIT", "not-a-number")
        assert database._env_int("VIDDIGEST_DAILY_PARSE_LIMIT", 3) == 3

    def test_empty_env_value_falls_back_to_default(self, db, monkeypatch):
        monkeypatch.setenv("VIDDIGEST_DAILY_PARSE_LIMIT", "   ")
        assert database._env_int("VIDDIGEST_DAILY_PARSE_LIMIT", 3) == 3

    def test_valid_env_value_is_used(self, db, monkeypatch):
        monkeypatch.setenv("VIDDIGEST_DAILY_PARSE_LIMIT", "7")
        assert database._env_int("VIDDIGEST_DAILY_PARSE_LIMIT", 3) == 7

    def test_unset_env_uses_default(self, db, monkeypatch):
        monkeypatch.delenv("VIDDIGEST_DAILY_PARSE_LIMIT", raising=False)
        assert database._env_int("VIDDIGEST_DAILY_PARSE_LIMIT", 3) == 3


class TestEnvConfigWiring:
    """工单 AC：上限可通过环境变量配置，改配置后行为随之改变。

    这组守的是「常量确实由那个环境变量名产出」，而不是 `_env_int`
    这个纯函数本身。只测 _env_int 的话，把两个上限改回硬编码、
    或把变量名拼错，都不会让任何一条测试变红——AC 形同虚设。
    """

    def test_parse_limit_comes_from_its_env_var(self):
        assert _limits_in_fresh_interpreter(VIDDIGEST_DAILY_PARSE_LIMIT="42")[0] == 42

    def test_chat_limit_comes_from_its_env_var(self):
        assert _limits_in_fresh_interpreter(VIDDIGEST_DAILY_CHAT_LIMIT="77")[1] == 77

    def test_both_limits_are_read_independently(self):
        assert _limits_in_fresh_interpreter(
            VIDDIGEST_DAILY_PARSE_LIMIT="42", VIDDIGEST_DAILY_CHAT_LIMIT="77"
        ) == (42, 77)

    def test_neither_limit_borrows_the_other_env_var(self):
        """各读各的，互不串味。"""
        assert _limits_in_fresh_interpreter(VIDDIGEST_DAILY_PARSE_LIMIT="42") == (42, 10)
        assert _limits_in_fresh_interpreter(VIDDIGEST_DAILY_CHAT_LIMIT="77") == (3, 77)

    def test_unset_env_yields_documented_defaults(self):
        """默认值也钉住：.env.example 里写的 3 / 10。"""
        assert _limits_in_fresh_interpreter() == (3, 10)

    def test_unparsable_env_falls_back_to_default_at_construction(self):
        """子进程里配错值，退回默认而不是崩在 import。"""
        assert _limits_in_fresh_interpreter(
            VIDDIGEST_DAILY_PARSE_LIMIT="oops", VIDDIGEST_DAILY_CHAT_LIMIT=""
        ) == (3, 10)


class TestVipUnlimited:
    def test_vip_unlimited_on_both_counters(self, db, make_user):
        uid = make_user(is_vip=True, vip_expire_at="2099-01-01T00:00:00+00:00")
        for _ in range(5):
            assert db.consume_quota(uid, "parse") == -1
            assert db.consume_quota(uid, "chat") == -1
        assert _counts(uid) == (0, 0), "VIP 不应消耗任何额度"

    def test_expired_vip_falls_back_to_free(self, db, make_user):
        uid = make_user(is_vip=True, vip_expire_at="2000-01-01T00:00:00+00:00")
        assert db.check_quota(uid)["parse"] == (True, database.DAILY_PARSE_LIMIT)
        assert db.check_quota(uid)["chat"] == (True, database.DAILY_CHAT_LIMIT)


class TestSchemaMigration:
    def test_init_db_adds_columns_to_existing_table(self, db, make_user):
        """已建好的库再次 init_db 也要拿到新列。

        CREATE TABLE IF NOT EXISTS 不会给已存在的表加列，
        所以必须有独立的 ALTER TABLE 步骤，否则老库直接崩。
        """
        with database.get_db() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}

        assert "daily_parse_count" in cols, "users 表缺少 daily_parse_count"
        assert "daily_chat_count" in cols, "users 表缺少 daily_chat_count"
        assert "last_parse_date" in cols
        assert "last_chat_date" in cols

    def test_migration_is_idempotent(self, db):
        """重复跑 init_db 不能报错。"""
        database.init_db()
        database.init_db()

    def test_legacy_columns_untouched(self, db, make_user):
        """expand 阶段不动旧列——它是旧测试与回滚路径的依托。"""
        uid = make_user()
        db.consume_summary_quota(uid)
        with database.get_db() as c:
            row = c.execute(
                "SELECT daily_summary_count FROM users WHERE id=?", (uid,)
            ).fetchone()
        assert row["daily_summary_count"] == 1, (
            "旧的 daily_summary_count 应仍可独立使用；"
            "原地改它会让所有旧测试与回滚路径同时失效"
        )

    def test_legacy_columns_survive_migration(self, db):
        """迁移**不得**删改任何旧列——expand 原则的硬约束。

        只断言「新列在」不够：实现完全可能一边加新列一边把旧列 drop 掉，
        而那样所有旧测试与回滚路径会同时失效。所以这里逐个确认旧列还在。
        """
        with database.get_db() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}

        for legacy in ("daily_summary_count", "last_summary_date",
                       "is_vip", "vip_expire_at", "email", "password_hash"):
            assert legacy in cols, (
                f"迁移把旧列 {legacy} 弄丢了——expand 阶段只能加不能删；"
                "旧列是回滚路径与存量测试的依托"
            )

    def test_migration_upgrades_a_legacy_database(self, db, tmp_path):
        """真实场景：库里**只有旧结构**时，init_db 必须把它补齐。

        只测「新库有列」是弱的——新建库时 CREATE TABLE 直接带上了新列，
        根本没走 ALTER 路径。老库升级才是迁移存在的理由。
        """
        legacy_db = tmp_path / "legacy.db"
        legacy_db.write_bytes(b"")

        with database.get_db() as c:
            c.execute("DROP TABLE users")

        # 造一张只有旧结构的表——模拟本工单之前的老库
        with database.get_db() as c:
            c.executescript("""
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
            """)
            cols_before = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
            assert "daily_parse_count" not in cols_before, "前提：老库不该已有新列"

            # 跑迁移
            database.init_db()

            cols_after = {r["name"] for r in c.execute("PRAGMA table_info(users)")}

        for added in ("daily_parse_count", "daily_chat_count",
                      "last_parse_date", "last_chat_date"):
            assert added in cols_after, f"老库升级后仍缺列 {added}"
        assert "daily_summary_count" in cols_after, "老库升级不该丢旧列"

    def test_migration_is_safe_to_run_twice_on_legacy_db(self, db):
        """对老库重复跑 init_db 不能报 duplicate column。

        SQLite 没有 ADD COLUMN IF NOT EXISTS，靠的是先查列再 ALTER；
        少了那一步，第二次执行就会炸。
        """
        with database.get_db() as c:
            c.execute("DROP TABLE users")
            c.executescript("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    daily_summary_count INTEGER DEFAULT 0,
                    last_summary_date TEXT
                )
            """)

        database.init_db()  # 第一次：加列
        database.init_db()  # 第二次：必须无事发生

        with database.get_db() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        assert "daily_parse_count" in cols and "daily_chat_count" in cols

    def test_unknown_quota_kind_rejected(self, db, make_user):
        """写错 kind 必须立刻失败，而不是静默不动。"""
        uid = make_user()
        with pytest.raises(ValueError):
            db.consume_quota(uid, "typo")
        with pytest.raises(ValueError):
            db.refund_quota(uid, "typo")
        with pytest.raises(ValueError):
            db.check_quota_kind(uid, "typo")
