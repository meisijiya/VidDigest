"""三条测试接缝自身的证明。

工单 #2 的核心不是「加了三个工具」，而是**证明它们真的守得住行为**。
所以每条接缝都配一组「接缝坏了就会红」的测试——接缝自己假通过的话，
后面八张工单的社区测试就全建在沙上了。

这里刻意不改现有 35 条测试：两种写法并存，等社区功能稳定再评估统一。
"""
import sqlite3
import threading

import pytest
import api_summarize
import auth
import database
from conftest import run_in_worker
from seams import (
MODEL_METHODS,
StubExtractor,
StubSummarizer,
auth_headers,
close_all_thread_connections,
make_client,
)


# ── 接缝一：桩按方法计数 ───────────────────────────────────

class TestStubCountsPerMethod:
    def test_each_method_counted_separately(self):
        s = StubSummarizer()
        list(s.summarize_stream("t", "zh"))
        s.generate_mindmap("t", "zh")
        list(s.chat_stream("t", "q"))

        assert s.calls_of("summarize_stream") == 1
        assert s.calls_of("generate_mindmap") == 1
        assert s.calls_of("chat_stream") == 1

    def test_mindmap_is_counted(self):
        """旧桩的思维导图方法不计数——这正是「未调用模型」假通过的根源。"""
        s = StubSummarizer()
        s.generate_mindmap("t", "zh")
        assert s.calls_of("generate_mindmap") == 1, "思维导图调用没有被计到"
        assert s.calls == 1, "只调了思维导图，总计数却是 0"

    def test_per_method_beats_total_when_one_method_skipped(self):
        """关键判据：只调了思维导图时，总计数非零但总结方法为零，
        逐方法断言能区分，总计数断言不能。"""
        s = StubSummarizer()
        s.generate_mindmap("t", "zh")

        assert s.calls_of("summarize_stream") == 0, "总结方法应为零"
        assert s.calls_of("generate_mindmap") == 1
        assert s.called_methods() == {"generate_mindmap"}

    def test_untouched_stub_counts_zero_everywhere(self):
        s = StubSummarizer()
        for m in MODEL_METHODS:
            assert s.calls_of(m) == 0
        assert s.calls == 0
        assert s.called_methods() == set()

    def test_unknown_method_name_raises(self):
        """拼错方法名必须报错，不能静默返回 0——
        静默 0 会让「没调过」这个断言永远成立。"""
        s = StubSummarizer()
        with pytest.raises(KeyError):
            s.calls_of("summarise_stream")  # 拼写差异

    def test_counts_accumulate_across_repeat_calls(self):
        s = StubSummarizer()
        for _ in range(3):
            list(s.summarize_stream("t", "zh"))
        assert s.calls_of("summarize_stream") == 3

    def test_extractor_counts_subtitle_extraction(self):
        """追问测试要断言「没有重跑字幕提取」，所以提取器也得计数。"""
        e = StubExtractor()
        assert e.calls == 0
        e.extract("u")
        e.extract("u")
        assert e.calls == 2


# ── 接缝二：所有工作线程的连接都被清理 ─────────────────────

def _write_in_worker(marker):
    """在工作线程里建连并写一行——模拟路由层派发到线程池。

    不在这里设 DB_PATH：那是模块级全局，由调用方在主线程设好。
    工作线程改它会造成「换库后又被改回去」的假象。
    """
    with database.get_db() as c:
        c.execute("INSERT INTO users (email, password_hash) VALUES (?, 'h')", (marker,))
    return marker


class TestWorkerThreadConnectionCleanup:
    def test_worker_thread_connection_is_reachable_for_cleanup(self, db):
        """database 必须能报出工作线程那份连接，否则夹具清不掉。

        db 夹具把 DB_PATH 指到临时库——不取它的话这条会连真实的
        backend/data/app.db，而本类的邻居测试是会 INSERT 的。
        """
        main_ident = threading.get_ident()
        with database.get_db():
            pass  # 确保主线程有连接

        worker_ident = {}

        def _open_in_worker():
            worker_ident["id"] = threading.get_ident()
            database.get_db().__enter__()

        run_in_worker(_open_in_worker)

        conns = database.open_connections()
        owner_threads = database.open_connection_threads()

        assert len(conns) >= 2, (
        "连接清单里应有主线程与工作线程两份；"
        "只有一份说明工作线程的连接没被登记，夹具清不掉它"
        )
        assert worker_ident["id"] != main_ident, "前提：工作线程应是另一个线程"
        assert worker_ident["id"] in owner_threads, (
        "工作线程创建的连接没被登记；"
        "只登记主线程那份的话，夹具清不掉工作线程的旧库连接"
        )
        assert main_ident in owner_threads, "主线程自己的连接也应被登记"

    def test_next_test_cannot_read_previous_test_data(self, db, tmp_path, monkeypatch):
        """核心判据：工作线程写完库后，清理再换库，下一个测试读不到旧数据。

        db 夹具是第一道防线：它保证本测试开始时 DB_PATH 已指向临时库。
        本测试自己再切到 first/second 两个临时库，走完整的换库流程。
        """
        first = str(tmp_path / "first.db")
        second = str(tmp_path / "second.db")

        monkeypatch.setattr(database, "DB_PATH", first)
        close_all_thread_connections()
        database.init_db()

        # 同一个工作线程，先写第一个库
        run_in_worker(_write_in_worker, "worker@example.com")

        # 夹具做的事：清掉所有线程的连接
        close_all_thread_connections()

        # 下一个测试：换库。复用的工作线程不得再看到上一个测试的数据
        monkeypatch.setattr(database, "DB_PATH", second)
        close_all_thread_connections()
        database.init_db()

        def _read_all_emails():
            with database.get_db() as c:
                return [r["email"] for r in c.execute("SELECT email FROM users")]

        emails = run_in_worker(_read_all_emails)

        assert emails == [], f"工作线程读到了上一个测试的残留数据：{emails}"

    def test_worker_reuses_thread_not_new_connection_to_stale_db(self, db, tmp_path, monkeypatch):
        """工作线程被复用时必须重建连接，不能拿着旧库的死连接继续用。

        这条测的是本工单最难的一环：会话级单线程池保证多次调用落在同一个
        工作线程上，也就是最贴近「路由层线程池复用」的真实形状。
        """
        first = str(tmp_path / "first.db")
        second = str(tmp_path / "second.db")

        monkeypatch.setattr(database, "DB_PATH", first)
        close_all_thread_connections()
        database.init_db()

        run_in_worker(_write_in_worker, "reuse@example.com")
        close_all_thread_connections()

        monkeypatch.setattr(database, "DB_PATH", second)
        close_all_thread_connections()
        database.init_db()

        def _read():
            with database.get_db() as c:
                return [r["email"] for r in c.execute("SELECT email FROM users")]

        assert run_in_worker(_read) == []

    def test_generation_change_forces_worker_to_rebuild(self, db, tmp_path, monkeypatch):
        """代际号是这套机制的关键：不换新连接的旧库污染从这里被挡住。"""
        first = str(tmp_path / "first.db")
        second = str(tmp_path / "second.db")

        monkeypatch.setattr(database, "DB_PATH", first)
        close_all_thread_connections()
        database.init_db()
        before = database.get_connection_generation()

        run_in_worker(_write_in_worker, "gen@example.com")
        close_all_thread_connections()

        assert database.get_connection_generation() > before, "清理时必须递增代际号"

        monkeypatch.setattr(database, "DB_PATH", second)
        close_all_thread_connections()
        database.init_db()

        def _actual_file():
            with database.get_db() as c:
                return c.execute("PRAGMA database_list").fetchone()["file"]

        actual = run_in_worker(_actual_file)

        assert actual == second, f"工作线程连的仍是旧库：{actual}"

    def test_cleanup_closes_every_registered_connection(self, db):
        """清理后清单必须为空——不只是「代际号挡住了旧数据」。

        代际号能挡住跨测试的数据污染，但闭着的连接仍占着文件句柄。
        两者是不同的事，必须分别断言：只断言数据读不到，
        删掉 close 循环测试照样全绿（M3 变异存活）。
        """
        with database.get_db():
            pass
        run_in_worker(lambda: database.get_db().__enter__())

        assert len(database.open_connections()) >= 2, "前提：应有多份连接待清理"

        database.forget_all_connections()

        assert database.open_connections() == [], "清理后清单未清空，连接仍占着文件句柄"

    def test_main_thread_connection_is_really_closed(self, db):
        """主线程自己那份连接必须真的被关闭。

        为什么只断言主线程那份：sqlite3 的连接归创建它的线程所有，
        主线程对工作线程那份执行 execute 会抛
        `SQLite objects created in a thread can only be used in that same thread`，
        而对**已关闭**的连接抛的是 `Cannot operate on a closed database`。
        两者异常类型相同（都是 ProgrammingError），只靠类型断言的话，
        一份**根本没关**的工作线程连接也能让断言通过。

        所以这里只取主线程自己那份，并校验异常消息文本。
        工作线程连接的保证是代际号那条测试，不在这里假装能验。
        """
        # 先造一份工作线程连接——没有它的话，下面的过滤就没有意义：
        # 只剩主线程一份时，「全取」和「只取主线程」结果完全一样。
        run_in_worker(lambda: database.get_db().__enter__())

        with database.get_db():
            pass

        current = threading.get_ident()
        pairs = list(zip(database.open_connections(), database.open_connection_threads()))
        mine = [conn for conn, owner in pairs if owner == current]
        others = [owner for _, owner in pairs if owner != current]

        assert mine, "前提：应至少抓到主线程自己那份连接"
        assert others, (
            "前提：应存在工作线程的连接，否则「只取主线程那份」这个过滤"
            "与「全取」等价，断言会失去区分力"
        )

        database.forget_all_connections()

        for conn in mine:
            with pytest.raises(sqlite3.ProgrammingError) as exc:
                conn.execute("SELECT 1")
            assert "closed database" in str(exc.value), (
                f"拿到的是跨线程错误而非「连接已关闭」：{exc.value}"
            )

    def test_worker_drops_stale_connection_on_next_use(self, db, tmp_path, monkeypatch):
        """工作线程必须在下次取连接时丢弃旧连接——这才是实际保证。

        为什么不测「工作线程的连接被 close 了」：sqlite3 禁止跨线程使用
        连接，主线程对一份仍打开的连接执行 execute 同样抛
        ProgrammingError，与「已关闭」抛的异常类型完全一样，两者在主线程
        断言里区分不了。而且 threading 没有「按线程 ident 投递任务」的 API，
        主线程根本无法让工作线程去关它自己的连接。

        所以保证只能落在**可观察的后果**上：清理之后，工作线程下次取连接
        拿到的是新库的新连接，而不是继续用旧的。
        """
        first = str(tmp_path / "first.db")
        second = str(tmp_path / "second.db")

        monkeypatch.setattr(database, "DB_PATH", first)
        close_all_thread_connections()
        database.init_db()
        run_in_worker(_write_in_worker, "stale@example.com")

        # 清理 + 换库
        close_all_thread_connections()
        monkeypatch.setattr(database, "DB_PATH", second)
        close_all_thread_connections()
        database.init_db()

        # 工作线程在同一线程内确认自己连的是新库
        def _current_file():
            with database.get_db() as c:
                return c.execute("PRAGMA database_list").fetchone()["file"]

        actual = run_in_worker(_current_file)
        # 两条都断言：只写 `== second` 的话，把它中和成 `is not None`
        # 这条测试就废了，而「连的是哪个库」正是它要守的东西。
        assert actual != first, f"工作线程仍连着旧库：{actual}"
        assert actual == second, f"工作线程连的不是新库：{actual}"

        # 且新库里读不到旧数据
        def _emails():
            with database.get_db() as c:
                return [r["email"] for r in c.execute("SELECT email FROM users")]

        assert run_in_worker(_emails) == [], "工作线程读到了旧库的残留数据"

    def test_worker_connection_is_dropped_from_registry(self, db):
        """工作线程那份必须从清单里消失。

        与上面两条合起来才是完整保证：主线程连接真被关、工作线程连接被丢弃
        引用并靠代际号强制重建。少任何一条，下一个测试都可能读到旧库。
        """
        run_in_worker(lambda: database.get_db().__enter__())

        assert database.open_connections(), "前提：工作线程应已建立连接"

        database.forget_all_connections()

        assert database.open_connections() == [], "工作线程连接仍留在清单里"
        assert database.open_connection_threads() == [], "线程登记未一并清空"

    def test_prune_compares_identity_not_count(self, db):
        """剔除必须比对**身份**，不能只比数量。

        缺陷形态：快路径写成 `len(alive) == len(登记数)` 就整段跳过剔除。
        只要登记数恰好等于活线程数——哪怕登记内容全是死 ident——就跳过。

        观察点必须是 _prune_dead_threads_locked **本身**：
        换成 forget_all_connections 观察会恒真——那个函数末尾把整个
        列表 clear 了，之后读到的当然是空的。这正是「断言绑到错的对象」。

        造场景：往私有列表塞入绝无可能与活线程 ident 相同的假死登记，
        再把数量补到与活线程数相等，让错误快路径的判据恰好成立。
        """
        import threading as _t

        alive_idents = {t.ident for t in _t.enumerate()}

        with database._open_conns_lock:
            existing_conns = list(database._open_conns)
            existing_threads = list(database._open_conns_threads)
            # 一律用负 ident，与任何活线程 ident 都不可能相同
            fake_dead = [-1000 - k for k in range(max(4, len(alive_idents) + 4))]
            database._open_conns_threads.extend(fake_dead)
            database._open_conns.extend([None] * len(fake_dead))

            # 多退少补，让登记数恰好等于活线程数
            current = list(database._open_conns_threads)
            delta = len(current) - len(alive_idents)
            if delta > 0:
                drop = set(current[-delta:])
                database._open_conns_threads[:] = [
                    i for i in current if i not in drop
                ]
                fake_dead = [i for i in fake_dead if i not in drop]

        try:
            assert len(database._open_conns_threads) == len(alive_idents), (
                f"前提：登记数应与活线程数相等，"
                f"登记={len(database._open_conns_threads)} 活线程={len(alive_idents)}"
            )
            assert fake_dead, "前提：应至少留下一条假死登记"
            assert all(i not in alive_idents for i in fake_dead), (
                f"假死 ident 竟与活线程相同：{fake_dead} vs {alive_idents}"
            )

            # 观察点：剔除函数本身，不是 forget_all_connections
            with database._open_conns_lock:
                database._prune_dead_threads_locked()
                after = list(database._open_conns_threads)

            leftover = [i for i in after if i in set(fake_dead)]
            assert not leftover, (
                f"剔除后仍留有死线程的登记：{leftover}；"
                "快路径按数量比较会在这里整段跳过"
            )
        finally:
            with database._open_conns_lock:
                database._open_conns[:] = existing_conns
                database._open_conns_threads[:] = existing_threads
    def test_dead_thread_connections_are_evicted(self, db):
        """死线程的连接必须从登记里剔除，否则 fd 随短命线程无界增长。

        实测（200 个短命线程）：不剔除时注册表滞留 201 条、句柄净增 401；
        剔除后注册表只增 1 条、句柄净增 4（master 为 0）。

        Connection 不支持弱引用（weakref.WeakSet 抛 TypeError），
        只能靠 threading.enumerate() 判活——这条守住那个判断。
        """
        for _ in range(20):
            run_in_worker(lambda: database.get_db().__enter__())

        # 会话级池的线程仍活着，所以先记录基线
        baseline = len(database.open_connections())

        # 起一批短命线程：它们各建一条连接后即退出
        def _spawn_short_lived(n):
            import threading as _t
            for _ in range(n):
                th = _t.Thread(target=lambda: database.get_db().__enter__())
                th.start()
                th.join()

        _spawn_short_lived(30)

        # 剔除发生在下一次登记/查询时；open_connections() 内部就会剔除
        after = len(database.open_connections())

        assert after <= baseline + 2, (
            f"注册表从 {baseline} 涨到 {after}：20 个死线程的连接没有被剔除，"
            "fd 会随短命线程无界增长"
        )

    def test_same_generation_reuses_the_same_connection(self, db):
        """连接复用必须成立：同代际内 get_db() 拿到的是同一份连接。

        没有这条的话，删掉 _thread_local.generation 赋值只会让每次都重建
        连接——功能测试照样全绿，但生产上会丢掉连接复用的收益，
        且 _open_conns 变成无界增长。
        """
        with database.get_db() as c1:
            first = id(c1)
        with database.get_db() as c2:
            second = id(c2)

        assert first == second, "同代际内应复用同一份连接，而不是每次重建"

    def test_open_connections_returns_a_snapshot(self, db):
        """open_connections() 必须返回快照，外部改不动内部清单。"""
        with database.get_db():
            pass

        snapshot = database.open_connections()
        assert snapshot, "前提：应有已登记的连接"
        snapshot.clear()
        assert database.open_connections() != [], (
        "返回的是内部列表本身，调用方能清空它"
        )

    def test_cleanup_is_idempotent(self, db):
        close_all_thread_connections()
        close_all_thread_connections()
        with database.get_db() as c:
            assert c.execute("SELECT 1").fetchone()[0] == 1


# ── 接缝三：真实 HTTP 层能区分未登录与已登录 ───────────────

@pytest.fixture()
def client_app(db, make_user):
    """真实 app + 真实鉴权依赖，只把数据库指向临时库。"""
    import main as main_module
    return main_module.app, make_user


class TestHttpSeamResolvesAuth:
    def test_anonymous_protected_route_gets_401(self, client_app):
        app, _make_user = client_app
        with make_client(app) as c:
            r = c.get("/api/auth/me")
        assert r.status_code == 401, (
        f"未登录访问受保护路由应得 401，实得 {r.status_code}；"
        "得 200 说明鉴权依赖根本没被执行"
        )

    def test_logged_in_protected_route_gets_200(self, client_app):
        app, make_user = client_app
        uid = make_user("real@example.com")
        token = auth.create_token(uid, "real@example.com")
        with make_client(app) as c:
            r = c.get("/api/auth/me", headers=auth_headers(token))
        assert r.status_code == 200
        assert r.json()["data"]["email"] == "real@example.com"

    def test_seam_distinguishes_the_two_states(self, client_app):
        """接缝自身的判据：同一个客户端，未登录与已登录必须得到不同结果。

        只断言「401」不够——一个永远返回 401 的假接缝也能满足它。
        """
        app, make_user = client_app
        uid = make_user("both@example.com")
        token = auth.create_token(uid, "both@example.com")

        with make_client(app) as c:
            anon = c.get("/api/auth/me")
            authed = c.get("/api/auth/me", headers=auth_headers(token))

        assert anon.status_code != authed.status_code, "两种状态结果相同，鉴权没被执行"
        assert (anon.status_code, authed.status_code) == (401, 200)

    def test_anonymous_public_route_gets_200(self, client_app):
        app, _make_user = client_app
        with make_client(app) as c:
            r = c.get("/api/health")
        assert r.status_code == 200, "公开路由对未登录应放行"

    def test_protected_route_rejects_invalid_token(self, client_app):
        """受保护路由对无效 token 返回 401。

        注意口径：这条只证明「无效 token 不得放行」。
        区分「无效 token」与「根本没带凭据」的是可选路由那条
        test_invalid_token_on_optional_route_falls_back_to_anonymous——
        两边都返回 401 的话，在强制登录路由上分不开。
        """
        app, _make_user = client_app
        with make_client(app) as c:
            r = c.get("/api/auth/me", headers=auth_headers("not-a-real-token"))
        assert r.status_code == 401, "无效 token 不应被当作已登录"

    def test_optional_auth_route_sees_none_when_anonymous(self, client_app):
        """可选登录依赖：未登录时真的是 None，而不是依赖对象本身。"""
        app, _make_user = client_app
        with make_client(app) as c:
            r = c.get("/api/quota")
        assert r.status_code == 200
        assert r.json()["logged_in"] is False

    def test_optional_auth_route_sees_user_when_logged_in(self, client_app):
        app, make_user = client_app
        uid = make_user("quota@example.com")
        token = auth.create_token(uid, "quota@example.com")
        with make_client(app) as c:
            r = c.get("/api/quota", headers=auth_headers(token))
        assert r.json()["logged_in"] is True
        assert r.json()["parse"]["remaining"] == database.DAILY_PARSE_LIMIT

    def test_optional_auth_route_binds_to_the_right_user(self, client_app):
        """可选鉴权必须解析成 token 里那个用户，不只是「某个已登录用户」。

        只断言 logged_in is True 的话，把任意 token 解析成 user id=1
        也能通过——而社区工单的「未登录看列表 / 登录看详情」正走这条路。
        """
        app, make_user = client_app
        first = make_user("first@example.com")
        second = make_user("second@example.com")
        # 只有 second 用掉额度：若鉴权错绑到 first，first 的额度会变
        database.consume_quota(second, "parse")

        token = auth.create_token(first, "first@example.com")
        with make_client(app) as c:
            first_view = c.get("/api/quota", headers=auth_headers(token)).json()
            second_view = c.get(
            "/api/quota",
            headers=auth_headers(auth.create_token(second, "second@example.com")),
            ).json()

        assert first_view["parse"]["remaining"] == database.DAILY_PARSE_LIMIT, (
        "first 没用过额度，不该被扣"
        )
        assert second_view["parse"]["remaining"] == database.DAILY_PARSE_LIMIT - 1, (
        "second 用掉过一次，读到的却是别人的额度"
        )

    def test_invalid_token_on_optional_route_falls_back_to_anonymous(self, client_app):
        """无效 token 在**可选**鉴权路由上应降级为未登录，而不是 401。

        受保护路由返回 401 已由另一条覆盖；可选路由的行为不同且没被测过，
        社区的公开列表接口正依赖它。
        """
        app, make_user = client_app
        make_user("real@example.com")

        with make_client(app) as c:
            r = c.get("/api/quota", headers=auth_headers("not-a-real-token"))

        assert r.status_code == 200, "可选鉴权不应因无效 token 报 401"
        assert r.json()["logged_in"] is False, "无效 token 不应被当作已登录"


# ── 现有 35 条测试不被本工单改动 ───────────────────────────

class TestFixtureWiringIsLoadBearing:
    """守护「夹具真的在做跨线程清理」，而不是测试体内手写清理。

    两条形状互补：

    1. 跨测试——上一条在工作线程写库并交给 conftest 记账，下一条挂载 db
    夹具时由 conftest 自己断言读不到。守卫写在 conftest 而非这里，
    所以它对任何用 db 夹具的测试都生效。
    2. 测试内——换库 + 走夹具那套清理，模拟「测试边界」，
    这样单跑 -k 也有意义，不依赖执行顺序。

    夹具若被改回「只清主线程」版本，两种形状都会转红。
    """

    def test_db_fixture_calls_thread_cleanup_on_both_ends(self):
        """db 夹具的源码里，挂载与拆卸两端都必须调清理。

        这是最外层的守护：把夹具改回 master 的「只清主线程」版本、
        或干脆把清理调用删掉，都必须在这里红。
        """
        import inspect

        import conftest

        src = inspect.getsource(conftest.db)
        cleanup_calls = src.count("close_all_thread_connections()")
        assert cleanup_calls >= 2, (
            f"db 夹具源码里只找到 {cleanup_calls} 处清理调用，"
            "挂载与拆卸两端都应有——少一端就会跨测试泄漏"
        )

    def test_db_fixture_self_check_is_present_and_worker_scoped(self):
        """夹具的挂载期自检必须还在，且读数据必须发生在工作线程上。

        上一版把这段删掉、或改成在主线程读，全量测试照样全绿——
        说明它当时完全没有承重。现在由本条直接守护。
        """
        import inspect

        import conftest

        src = inspect.getsource(conftest.db)
        assert "was_written" in src, "db 夹具里的跨测试自检被删掉了"
        assert "run_in_worker(_read)" in src, (
            "自检必须在**工作线程**上读数据；"
            "主线程每次都新建连接，无论清理是否正确都读到空库"
        )

    def test_worker_pool_is_session_scoped(self):
        """工作线程池必须是长生命周期的。

        每次新建池子的话，线程随测试结束被回收、连接被 GC，
        那就复现不出跨测试泄漏，所有相关断言会空转。
        本条把这个设计选择变成可执行的约束。
        """
        from concurrent.futures import ThreadPoolExecutor

        from conftest import get_worker_pool

        pool = get_worker_pool()
        assert isinstance(pool, ThreadPoolExecutor)
        assert pool is get_worker_pool(), "每次调用都新建了池子，线程不跨测试复用"
        assert pool._max_workers == 1, "单线程池才能保证复用同一个工作线程"

    def test_worker_writes_then_fixture_records_it(self, db, write_in_worker_then_next_test_sees_nothing):
        """在工作线程写库；conftest 记账，下一个 db 夹具会验证隔离。"""
        assert write_in_worker_then_next_test_sees_nothing is True

    def test_next_test_sees_clean_db(self, db):
        """只取 db 夹具。conftest 会在挂载时验证上一个测试的数据不可见。

        先断言「标记确实被上一条置位过」：夹具自检在挂载期就把标记清掉了，
        所以到这里必须是 False。若为 True 说明上一条没跑，本条断言会空转。
        """
        import conftest

        assert conftest._previous_db["was_written"] is False, (
            "前置未满足：夹具自检没有执行过，本条断言可能空转"
        )
        with database.get_db() as c:
            emails = [r["email"] for r in c.execute("SELECT email FROM users")]
        assert emails == [], f"夹具未隔离上一个测试的数据：{emails}"

    def _simulate_next_test(self, db, monkeypatch, tmp_path):
        """在同一个测试内模拟「测试边界」：换库 + 走夹具那套清理。"""
        monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "next.db"))
        close_all_thread_connections()
        database.init_db()

    def test_worker_written_data_does_not_leak_across_fixture_boundary(self, db, monkeypatch, tmp_path):
        """工作线程写入 → 模拟下一个测试 → 同一个工作线程读不到旧数据。"""
        def _emails():
            with database.get_db() as c:
                return [r["email"] for r in c.execute("SELECT email FROM users")]

        run_in_worker(_write_in_worker, "leak@example.com")

        # 确认第一段确实写进去了，否则后半段是空转。
        # 必须**在同一个工作线程上**确认：写入发生在工作线程，
        # 在主线程确认证明不了它真的落到了那个线程的连接里。
        assert run_in_worker(_emails) == ["leak@example.com"], (
            "前置写入失败，后续断言无意义"
        )

        self._simulate_next_test(db, monkeypatch, tmp_path)

        def _read_from_worker():
            with database.get_db() as c:
                return [r["email"] for r in c.execute("SELECT email FROM users")]

        emails = run_in_worker(_read_from_worker)

        assert emails == [], f"工作线程读到了上一个测试的残留数据：{emails}"

    def test_main_thread_data_does_not_leak_across_fixture_boundary(self, db, monkeypatch, tmp_path):
        """主线程同样必须隔离——夹具两件事都要做。"""
        with database.get_db() as c:
            c.execute("INSERT INTO users (email, password_hash) VALUES ('main@x.com','h')")

        self._simulate_next_test(db, monkeypatch, tmp_path)

        with database.get_db() as c:
            emails = [r["email"] for r in c.execute("SELECT email FROM users")]

        assert emails == [], f"主线程读到了上一个测试的残留数据：{emails}"


class TestExistingSuiteUntouched:
    def test_route_level_suite_still_passes(self, db, make_user, monkeypatch):
        """抽样复核：路由层旧写法仍然通过，说明本工单没改变被测行为。"""
        import asyncio
        import json

        s = StubSummarizer()
        ex = StubExtractor(has_subtitle=True)
        monkeypatch.setattr(api_summarize, "_get_extractor", lambda: ex)
        monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: s)

        uid = make_user()

        async def _run():
            out = []
            async for e in api_summarize.summarize_video(
            api_summarize.SummarizeRequest(url="u", language="zh"),
            user={"id": uid},
            ):
                raw = e.raw_data
                out.append((e.event, raw if raw in (None, "", "[DONE]") else json.loads(raw)))
            return out

        events = asyncio.run(_run())
        assert [e[0] for e in events] == [
        "subtitle", "quota", "summary", "summary", "mindmap", "done",
        ]
        # 逐方法断言在这里才第一次真正可用
        assert s.calls_of("summarize_stream") == 1
        assert s.calls_of("generate_mindmap") == 1
        assert s.calls_of("chat_stream") == 0
