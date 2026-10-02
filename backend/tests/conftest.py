"""测试夹具：每个测试用独立的临时 SQLite，绝不碰 backend/data/app.db。"""
import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import database  # noqa: E402
from seams import close_all_thread_connections  # noqa: E402

#: 会话级工作线程池，**故意长生命周期**。
#:
#: 每次测试新建 ThreadPoolExecutor 的话，线程随测试结束被回收，
#: 它缓存的连接也跟着被 GC——那样根本复现不出泄漏，断言会恒真。
#: 生产上 uvicorn 的线程池是长生命周期的，线程被复用、连接被复用，
#: 所以测试必须用同样形状的池子，否则测的是另一回事。
_worker_pool = None

#: 上一个 db 夹具是否在工作线程写过数据。
#: 用于在**夹具层**验证隔离真的生效——只在测试体里手工调用清理的话，
#: 夹具被改回「只清主线程」也不会有人发现。
_previous_db = {"was_written": False}


def get_worker_pool():
    """会话级单线程池，跨测试复用同一个工作线程。"""
    global _worker_pool
    if _worker_pool is None:
        from concurrent.futures import ThreadPoolExecutor
        _worker_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="seam-worker")
    return _worker_pool


def run_in_worker(fn, *args):
    """在那个长生命周期的工作线程上执行。"""
    return get_worker_pool().submit(fn, *args).result()


def _emulate_worker_thread_write(marker="probe@example.com"):
    """在工作线程里建连并写一行，用于验证跨线程隔离。"""
    def _write():
        with database.get_db() as c:
            c.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, 'h')", (marker,)
            )

    run_in_worker(_write)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """每个测试一份独立临时库；退出时清掉**所有**线程的连接。

    只清主测试线程是不够的：路由层用 run_in_executor 把工作派发到线程池，
    那些工作线程各自缓存了指向本临时库的连接。下一个测试换 DB_PATH 后，
    复用的工作线程仍会拿着旧连接写进上一个测试的库。
    """
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    close_all_thread_connections()
    database.init_db()

    # 夹具层自检：上一个测试若在工作线程写过库，本测试不得从**那个工作线程**
    # 读到它的数据。读必须发生在工作线程上——主线程每次都拿新建的连接，
    # 无论清理是否正确都读到空库，在那儿断言等于什么都没测。
    if _previous_db["was_written"]:
        _previous_db["was_written"] = False

        def _read():
            with database.get_db() as c:
                return [r["email"] for r in c.execute("SELECT email FROM users")]

        leaked = run_in_worker(_read)
        assert leaked == [], (
            f"db 夹具没有隔离上一个测试的数据，工作线程读到了：{leaked}。"
            "close_all_thread_connections 只清了主线程，"
            "工作线程仍连着上一个测试的临时库"
        )

    yield database
    close_all_thread_connections()


@pytest.fixture()
def write_in_worker_then_next_test_sees_nothing():
    """在本测试的工作线程里写库，交给下一个 db 夹具去验证隔离。

    这样「写」和「验」分属两个测试，才真的守得住夹具接线；
    单个测试内自写自验的话，夹具改回只清主线程也不会有人发现。
    """
    _emulate_worker_thread_write()
    with database.get_db() as c:
        wrote = [r["email"] for r in c.execute("SELECT email FROM users")]
    assert wrote == ["probe@example.com"], "前置写入失败，后续隔离断言无意义"
    _previous_db["was_written"] = True
    return True


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

