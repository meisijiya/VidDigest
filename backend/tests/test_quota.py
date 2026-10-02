"""额度判定与扣减（工单 #4：解析额度与对话额度各自独立）。

原版这份文件锁的是「单一共用额度」，那套实现已随路由层切换删除。
这里留下的是它本来的两条回归——判定只读、没真调模型就不扣额度——
断言改到拆分后的 API 上，另加一条底线：两类额度各扣各的。

更细的拆分语义（跨天各自重置、回滚原子性、schema 迁移）见
test_quota_split.py；路由层的扣减与回滚见 test_quota_routes.py。
"""
import database


def _counts(uid):
    """一次读出两个计数器的当前值。"""
    with database.get_db() as c:
        row = c.execute(
            "SELECT daily_parse_count, daily_chat_count FROM users WHERE id=?", (uid,)
        ).fetchone()
    return row["daily_parse_count"], row["daily_chat_count"]


def _legacy_counts(uid):
    """拆分前的共用计数器——留着只为断言「新实现不再动它」。"""
    with database.get_db() as c:
        row = c.execute(
            "SELECT daily_summary_count, last_summary_date FROM users WHERE id=?", (uid,)
        ).fetchone()
    return row["daily_summary_count"], row["last_summary_date"]


class TestCheckIsReadOnly:
    def test_fresh_user_gets_full_quota(self, db, make_user):
        uid = make_user()
        assert db.check_quota(uid) == {
            "parse": (True, database.DAILY_PARSE_LIMIT),
            "chat": (True, database.DAILY_CHAT_LIMIT),
        }

    def test_check_never_writes(self, db, make_user):
        """判定是只读的 —— 这正是它能和扣减拆开的前提。"""
        uid = make_user()
        db.check_quota(uid)
        db.check_quota(uid)
        assert _counts(uid) == (0, 0)
        with database.get_db() as c:
            row = c.execute(
                "SELECT last_parse_date, last_chat_date FROM users WHERE id=?", (uid,)
            ).fetchone()
        assert row["last_parse_date"] is None and row["last_chat_date"] is None

    def test_check_after_exhaustion_still_writes_nothing(self, db, make_user):
        """额度用完之后反复判定也不能写库——否则「看一眼」就等于扣一次。"""
        uid = make_user()
        for _ in range(database.DAILY_PARSE_LIMIT):
            db.consume_quota(uid, "parse")
        for _ in range(3):
            assert db.check_quota(uid)["parse"] == (False, 0)
        assert _counts(uid) == (database.DAILY_PARSE_LIMIT, 0)


class TestOnlyConsumeSpends:
    """原 bug 1 的回归：扣减只发生在真要调模型的那一刻。"""

    def test_only_consume_moves_the_counter(self, db, make_user):
        uid = make_user()
        for _ in range(3):
            db.check_quota_kind(uid, "parse")
        assert _counts(uid) == (0, 0), "只判定不该扣额度"
        assert db.consume_quota(uid, "parse") == database.DAILY_PARSE_LIMIT - 1
        assert _counts(uid) == (1, 0)

    def test_unknown_user_refused(self, db):
        assert db.check_quota(9999) == {"parse": (False, 0), "chat": (False, 0)}
        assert db.consume_quota(9999, "parse") == 0
        assert db.consume_quota(9999, "chat") == 0


class TestTwoCountersShareNothing:
    """拆分后的底线：两类额度各扣各的，连旧的共用列都不碰。"""

    def test_each_kind_writes_only_its_own_column(self, db, make_user):
        uid = make_user()
        db.consume_quota(uid, "parse")
        assert _counts(uid) == (1, 0), "扣解析额度不该动对话额度"
        db.consume_quota(uid, "chat")
        assert _counts(uid) == (1, 1), "扣对话额度不该动解析额度"

    def test_new_quota_never_touches_the_legacy_counter(self, db, make_user):
        """旧的共用计数器还留在列里（存量库不能丢列），但新实现不许写它。

        一旦新路径改回落回旧列，用户看到的仍是「额度少了但说不清是解析
        还是追问扣的」——正是这次拆分要消灭的现象。

        每类都扣两次：首扣走「日期不是今天 → 置 1」那条分支，续扣走
        「计数 +1」那条。只扣一次的话，后面那条分支根本没被执行到。
        """
        uid = make_user()
        for _ in range(2):
            db.consume_quota(uid, "parse")
            db.consume_quota(uid, "chat")
        assert _counts(uid) == (2, 2), "前提：两次扣减都记在各自的列上"
        assert _legacy_counts(uid) == (0, None), "新实现写到了拆分前的共用计数器"

    def test_parsing_exhausted_leaves_chat_full(self, db, make_user):
        uid = make_user()
        for _ in range(database.DAILY_PARSE_LIMIT):
            db.consume_quota(uid, "parse")
        assert db.check_quota_kind(uid, "parse") == (False, 0)
        assert db.check_quota_kind(uid, "chat") == (True, database.DAILY_CHAT_LIMIT), (
            "解析额度用完，不该影响对话额度"
        )

    def test_chatting_exhausted_leaves_parse_full(self, db, make_user):
        uid = make_user()
        for _ in range(database.DAILY_CHAT_LIMIT):
            db.consume_quota(uid, "chat")
        assert db.check_quota_kind(uid, "chat") == (False, 0)
        assert db.check_quota_kind(uid, "parse") == (True, database.DAILY_PARSE_LIMIT), (
            "对话额度用完，不该影响解析额度"
        )


class TestDailyReset:
    def test_both_counters_reset_on_their_own_day(self, db, make_user):
        """跨天后两个计数器各自回到满额。"""
        uid = make_user()
        db.consume_quota(uid, "parse")
        db.consume_quota(uid, "chat")
        with database.get_db() as c:
            c.execute(
                "UPDATE users SET last_parse_date='1999-01-01', "
                "last_chat_date='1999-01-01' WHERE id=?", (uid,),
            )
        assert db.check_quota(uid) == {
            "parse": (True, database.DAILY_PARSE_LIMIT),
            "chat": (True, database.DAILY_CHAT_LIMIT),
        }


class TestVipExpiryParsing:
    """`is_vip_active` 的日期解析健壮性——守的是两个真实修过的 bug，
    不是「会员制」这个产品语义。

    项目已决定不做会员制（前端入口已关），所以这里**不**断言 VIP 享受
    无限额度；只断言「无论 vip_expire_at 里存的是什么，都不会把请求打挂」。
    这两条回归曾各自对应一次线上事故：

    - naive datetime 触发 naive/aware 比较抛 TypeError，SSE 一直转圈
    - 脏数据（存的不是日期）直接往上抛，没有兜底
    """

    def _is_active(self, db, uid):
        with db.get_db() as c:
            row = c.execute(
                "SELECT is_vip, vip_expire_at FROM users WHERE id=?", (uid,)
            ).fetchone()
        return db.is_vip_active(row)

    def test_naive_datetime_is_recognised(self, db, make_user):
        """回归：naive datetime 曾抛 TypeError，SSE 一直转圈。"""
        uid = make_user(is_vip=True, vip_expire_at="2099-01-01T00:00:00")
        assert self._is_active(db, uid) is True

    def test_garbage_expiry_does_not_raise(self, db, make_user):
        """回归：脏数据曾无 try/except 直接抛。"""
        uid = make_user(is_vip=True, vip_expire_at="not-a-date")
        assert self._is_active(db, uid) is False

    def test_expired_is_not_active(self, db, make_user):
        uid = make_user(is_vip=True, vip_expire_at="2000-01-01T00:00:00+00:00")
        assert self._is_active(db, uid) is False

    def test_flag_without_expiry_is_not_active(self, db, make_user):
        """只有 is_vip 位、没有到期时间时，不该当成有效会员。"""
        uid = make_user(is_vip=True)
        assert self._is_active(db, uid) is False

    def test_non_vip_row_is_not_active(self, db, make_user):
        uid = make_user()
        assert self._is_active(db, uid) is False

    def test_malformed_expiry_never_reaches_the_quota_path(self, db, make_user):
        """脏数据落到额度判定上也必须是「有限额」，而不是把请求打挂。"""
        uid = make_user(is_vip=True, vip_expire_at="not-a-date")
        assert db.check_quota_kind(uid, "parse") == (
            True, database.DAILY_PARSE_LIMIT
        ), "脏 vip_expire_at 让额度判定抛异常了"
