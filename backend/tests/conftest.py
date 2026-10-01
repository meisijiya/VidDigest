"""测试夹具：每个测试用独立的临时 SQLite，绝不碰 backend/data/app.db。"""
import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import database  # noqa: E402


def _drop_thread_conn():
    """get_db() 用线程本地缓存连接，不清掉会连到上一个临时库。"""
    tl = database._thread_local
    if getattr(tl, "conn", None) is not None:
        tl.conn.close()
        del tl.conn


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    _drop_thread_conn()
    database.init_db()
    yield database
    _drop_thread_conn()


@pytest.fixture()
def make_user(db):
    def _make(email="u@example.com", is_vip=False, vip_expire_at=None):
        user = db.create_user(email, "hash")
        if is_vip or vip_expire_at:
            with db.get_db() as c:
                c.execute(
                    "UPDATE users SET is_vip=?, vip_expire_at=? WHERE id=?",
                    (1 if is_vip else 0, vip_expire_at, user["id"]),
                )
        return user["id"]
    return _make


def count_of(uid):
    with database.get_db() as c:
        row = c.execute("SELECT daily_summary_count FROM users WHERE id=?", (uid,)).fetchone()
    return row["daily_summary_count"]
