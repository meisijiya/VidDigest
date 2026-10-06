"""测试夹具：每个测试用独立的临时 SQLite，绝不碰 backend/data/app.db。"""
import os
import sys
from datetime import datetime, timezone

# auth.py 在 JWT_SECRET 缺失时 **import 即失败**（工单 #11）。
# conftest 是 pytest 收集阶段第一批执行的模块，早于任何 test_*.py，
# 所以测试专用密钥必须在这里设，且早于 import database / auth。
# setdefault：外部环境真的配了密钥时不覆盖，测试不会盖掉真实配置。
os.environ.setdefault(
    "JWT_SECRET", "test-only-jwt-secret-0123456789abcdef0123456789abcdef"
)

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
def legacy_db(tmp_path, monkeypatch):
    """一个**先有旧结构**的库，不是空库。

    ``db`` 夹具会先 ``init_db()``，也就是造出一张**全新**的表——于是
    「老库没有 is_favorite、靠迁移补上」那条路径在它下面一次都走不到。
    补列排错了顺序，全量测试照样绿，真库一升级就打不开。

    所以这里只做 ``db`` 夹具的前半段：换库路径 + 清掉所有线程的连接，
    **不建表**。隔离强度与 ``db`` 完全一样，区别只是把建表这件事交还给
    测试自己，好让它能先摆一张缺列的表出来。

    名字进了 ``check_db_fixture.DB_FIXTURE_ARGS``：门禁豁免的是「显式
    声明我要自己管库结构」，不是「碰库可以不隔离」——不取任何夹具的
    测试照样会被报出来。
    """
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "legacy.db"))
    close_all_thread_connections()
    yield database
    close_all_thread_connections()


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


#: 钉死时钟用的那一刻：一年里的**正午**。
#:
#: 正午而不是 00:00：午夜前后那一格最难躲——预置与判定只要有一次落在
#: 跨午夜的十几毫秒里，日期就翻了一页，而被测代码并没有错。
#: 正午离两端都最远，且与「跨天重置」那类逻辑隔着整整半天。
FROZEN_MOMENT = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def frozen_clock(monkeypatch):
    """把 ``database`` 看到的「现在」钉死，返回那一刻本身。

    为什么需要：额度按「``last_*_date`` 等于今天」判重置，而**今天**
    由两处各自算一次——测试预置的时候一次，被测代码判定的时候又一次。
    两次之间跨过 UTC 午夜，预置的日期就变成了「昨天」，计数器被当成
    「今天还没用过」而重置。症状是「额度明明用光了却还能用」，
    而被测代码没有任何问题——**偶发红的门禁比没有门禁更糟**：
    它会训练所有人忽略红色。

    假类**继承** ``datetime`` 而不是顶替它：``database`` 别处还要用
    ``fromisoformat``，一个只实现了 ``now`` 的替身会让那些路径
    **因错误的原因**抛错——那种红不是护栏在响，是测错了东西。

    配套的变异义务：把「假时钟改成空操作」这条要能转红，
    否则「钉时钟」本身没人守，将来会被悄悄改回掷骰子。
    """
    moment = FROZEN_MOMENT

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return moment.replace(tzinfo=None)
            return moment.astimezone(tz)

    monkeypatch.setattr(database, "datetime", _Clock)
    return moment

