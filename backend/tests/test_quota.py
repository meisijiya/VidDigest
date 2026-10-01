"""配额判定与扣减（bug 1：字幕失败不该扣额度）。"""
from conftest import count_of

FAR_FUTURE = "2099-01-01T00:00:00+00:00"
PAST = "2000-01-01T00:00:00+00:00"


class TestCheckDoesNotWrite:
    def test_fresh_user_gets_full_quota(self, db, make_user):
        uid = make_user()
        assert db.check_summary_quota(uid) == (True, db.FREE_DAILY_SUMMARY_LIMIT)

    def test_check_never_writes(self, db, make_user):
        """判定是只读的 —— 这正是它能和扣减拆开的前提。"""
        uid = make_user()
        db.check_summary_quota(uid)
        db.check_summary_quota(uid)
        assert count_of(uid) == 0
        with db.get_db() as c:
            row = c.execute("SELECT last_summary_date FROM users WHERE id=?", (uid,)).fetchone()
        assert row["last_summary_date"] is None

    def test_failed_extraction_leaves_quota_intact(self, db, make_user):
        """字幕提取失败的那条路径不会调用 consume。"""
        uid = make_user()
        allowed, remaining = db.check_summary_quota(uid)
        assert (allowed, remaining) == (True, db.FREE_DAILY_SUMMARY_LIMIT)
        assert count_of(uid) == 0
        assert db.check_summary_quota(uid) == (True, db.FREE_DAILY_SUMMARY_LIMIT)


class TestConsume:
    def test_consume_decrements_and_reports_remaining(self, db, make_user):
        uid = make_user()
        limit = db.FREE_DAILY_SUMMARY_LIMIT
        assert [db.consume_summary_quota(uid) for _ in range(limit)] == [2, 1, 0]
        assert count_of(uid) == limit

    def test_fourth_check_refused(self, db, make_user):
        uid = make_user()
        for _ in range(db.FREE_DAILY_SUMMARY_LIMIT):
            db.consume_summary_quota(uid)
        assert db.check_summary_quota(uid) == (False, 0)

    def test_daily_reset(self, db, make_user):
        """跨天后额度重置。"""
        uid = make_user()
        db.consume_summary_quota(uid)
        with db.get_db() as c:
            c.execute("UPDATE users SET last_summary_date='1999-01-01' WHERE id=?", (uid,))
        assert db.check_summary_quota(uid) == (True, db.FREE_DAILY_SUMMARY_LIMIT)

    def test_unknown_user_refused(self, db):
        assert db.check_summary_quota(9999) == (False, 0)
        assert db.consume_summary_quota(9999) == 0


class TestVip:
    def test_vip_unlimited(self, db, make_user):
        uid = make_user(is_vip=True, vip_expire_at=FAR_FUTURE)
        assert db.check_summary_quota(uid) == (True, -1)
        assert db.consume_summary_quota(uid) == -1
        assert count_of(uid) == 0

    def test_expired_vip_falls_back_to_free(self, db, make_user):
        uid = make_user(is_vip=True, vip_expire_at=PAST)
        assert db.check_summary_quota(uid) == (True, db.FREE_DAILY_SUMMARY_LIMIT)

    def test_naive_datetime_still_recognised(self, db, make_user):
        """回归：旧事故是 naive datetime 抛 TypeError 导致 SSE 一直转圈。"""
        uid = make_user(is_vip=True, vip_expire_at="2099-01-01T00:00:00")
        assert db.check_summary_quota(uid) == (True, -1)

    def test_garbage_expiry_does_not_raise(self, db, make_user):
        """回归：旧实现此处无 try/except，脏数据直接抛。"""
        uid = make_user(is_vip=True, vip_expire_at="not-a-date")
        assert db.check_summary_quota(uid) == (True, db.FREE_DAILY_SUMMARY_LIMIT)

    def test_vip_flag_without_expiry(self, db, make_user):
        uid = make_user(is_vip=True, vip_expire_at=None)
        assert db.check_summary_quota(uid) == (True, db.FREE_DAILY_SUMMARY_LIMIT)
