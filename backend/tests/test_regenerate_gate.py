"""覆盖闸门是**数据库级**的，不依赖「后端只有��个进程」（工单 #20）。

工单 #20 的原状：闸门是 `api_summarize.py` 里一个进程内的 `set[str]`，
于是它同时是一道部署硬约束——`--workers 2` 把它劈成两份互不可见的副本，
两个请求各自看见空 set、都判定「可以覆盖」，然后**两次调模型、两次扣额度、
后写覆盖先写，不报错不告警**。

现在抢锁是一条带条件的 UPDATE，SQLite 串行化写事务，跨进程同样只有一个能拿到。

## 三组用例，第三组才是本文件存在的理由

1. **升级路径**：闸门两列是后加的。老库里没有它们，而 `db` 夹具每次
   `init_db()` 都出一个**全新**的库 —— 于是「老库缺列、靠迁移补上」那条路径
   一次都没被执行过。补列排错了顺序，全量绿，真库一升级就 `no such column`。
   所以必须从 `legacy_db`（只换 DB_PATH + 清连接、不建表）启动。
2. **单进程语义**：抢、放、过期接管、越权、迟到放闸不清别人的锁。
3. **跨进程**：起**真子进程**。单进程测试里闸门本来就有效，它证明不了任何事。

## 跨进程那条不需要真的「同时」

子进程**抢到之后不放**，锁就留在库里。于是即便它们是完全顺序跑的，
第二个也必然看到锁被占着而抢不到 —— 这正是要断的那件事。

而这恰恰是它能判别旧实现的原因：旧的进程内 set 的话，每个子进程各自一份空 set，
**顺序跑也全部成功**。所以这条断言区分的是「锁存在库里」与「锁活在某个进程的
内存里」，不需要构造竞态窗口，也就永远不会 flaky。
"""
import os
import sqlite3
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone

import pytest

import database

URL = "https://example.com/v"


def seed_ready(url=URL, owner=1):
    """按真实协议造一条 ready、归 owner 的社区视频。"""
    outcome, _ = database.reserve_video(url, owner)
    assert outcome == "reserved", f"前提不成立：{url} 被 reserve 判成 {outcome}"
    assert database.complete_video(url, summary_md="原总结", mindmap_md="# 主题",
                                   tags=["科普"], subtitle_text="字幕") == 1
    return database.get_video_by_url(url)


def lock_of(url=URL):
    row = database.get_video_by_url(url)
    return row["regenerating_by"], row["regenerating_at"]


# ── 1. 升级路径：老库里没有那两列 ──────────────────────────

class TestUpgradePath:
    def test_gate_works_on_a_db_that_predates_the_columns(self, legacy_db):
        """从一张**没有** regenerating_* 的老表升级，闸门必须仍然可用。

        `db` 夹具每次 init_db() 出一个全新库，这条路径一次都没被执行过。
        补列排错顺序的话，全量测试照样绿，真库一升级就打不开。
        """
        # 造出「工单 #20 之前」的那张表：列齐全，但没有闸门两列。
        conn = sqlite3.connect(database.DB_PATH)
        conn.execute("""
            CREATE TABLE videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                summary_md TEXT DEFAULT '',
                mindmap_md TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                subtitle_text TEXT DEFAULT '',
                parsed_by INTEGER,
                video_title TEXT DEFAULT '',
                cover_url TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE UNIQUE INDEX idx_videos_url ON videos(video_url)")
        conn.execute(
            "INSERT INTO videos (video_url, status, parsed_by, summary_md) "
            "VALUES (?, 'ready', 1, '老库里的总结')", (URL,)
        )
        conn.commit()
        conn.close()

        database.init_db()

        cols = {r["name"] for r in database.get_db().__enter__().execute(
            "PRAGMA table_info(videos)")}
        assert {"regenerating_by", "regenerating_at"} <= cols, (
            f"init_db 没给老库补上闸门列，实得 {sorted(cols)}")
        assert database.acquire_regenerate_gate(URL, 1) is True, (
            "补上列之后闸门仍不可用 —— 升级路径只补了 DDL，没接上语义")
        assert database.acquire_regenerate_gate(URL, 1) is False, (
            "老库升级来的这一行抢了两次都成功，说明闸门根本没生效")
        assert lock_of()[0] == 1

        # 老内容必须在覆盖期间仍然可读（ADR 0007）：闸门不碰 status。
        assert database.get_video_by_url(URL)["status"] == "ready"
        assert database.get_video_by_url(URL)["summary_md"] == "老库里的总结"


# ── 2. 单进程语义 ───────────────────────────────────────────

class TestGateSingleProcess:
    def test_only_one_acquire_wins(self, db):
        seed_ready()
        assert database.acquire_regenerate_gate(URL, 1) is True
        assert database.acquire_regenerate_gate(URL, 1) is False, (
            "同一个人连续抢两次都成功 —— 闸门没拦住")

    def test_release_lets_the_next_one_in(self, db):
        seed_ready()
        assert database.acquire_regenerate_gate(URL, 1) is True
        database.release_regenerate_gate(URL, 1)
        assert lock_of() == (None, None), "放闸没把锁清干净"
        assert database.acquire_regenerate_gate(URL, 1) is True, (
            "放闸之后仍然抢不到 —— 这个链接将永远覆盖不了")

    def test_wrong_owner_cannot_acquire(self, db, make_user):
        seed_ready(owner=make_user())
        other = make_user("other@example.com")
        assert database.acquire_regenerate_gate(URL, other) is False, (
            "别人的视频被抢到了 —— 覆盖闸门成了越权入口")

    def test_expired_lock_can_be_taken_over(self, db):
        """过期必须能被接管：持闸者被 kill 掉时跑不到 finally。

        TTL 调小的代价写在 VIDEO_REGENERATE_TTL_SECONDS 的注释里——
        调小它会在持闸者还在干活时把锁偷走，那就是本闸门要防的那件事。
        """
        seed_ready()
        database.acquire_regenerate_gate(URL, 1)
        stale = (datetime.now(timezone.utc)
                 - timedelta(seconds=database.VIDEO_REGENERATE_TTL_SECONDS + 60)).isoformat()
        with database.get_db() as conn:
            conn.execute("UPDATE videos SET regenerating_at = ? WHERE video_url = ?",
                         (stale, URL))

        assert database.acquire_regenerate_gate(URL, 1) is True, (
            "过期的锁接不了管 —— 持闸者进程死掉后，这个链接将永远覆盖不了")

    def test_fresh_lock_is_not_stolen(self, db):
        """上面那条的对照组：新鲜的不给偷。"""
        seed_ready()
        database.acquire_regenerate_gate(URL, 1)
        barely = (datetime.now(timezone.utc)
                  - timedelta(seconds=database.VIDEO_REGENERATE_TTL_SECONDS - 60)).isoformat()
        with database.get_db() as conn:
            conn.execute("UPDATE videos SET regenerating_at = ? WHERE video_url = ?",
                         (barely, URL))

        assert database.acquire_regenerate_gate(URL, 1) is False, (
            "TTL 之内就把锁偷走了 —— 于是两个人同时调模型、同时扣额度，"
            "正是这个闸门要防的那件事")

    def test_late_release_does_not_clear_someone_elses_lock(self, db):
        """过期的持闸者迟到放闸时，不能把**别人**的锁清掉。

        少了这条：一个过期的持闸者醒来后会清掉新持闸者的锁，于是第三个人
        以为没人占着而同时进来 —— 闸门从「晚放一次」变成「完全不挡」。
        """
        seed_ready()
        database.acquire_regenerate_gate(URL, 1)
        # 让 1 的锁过期，2 接管（这里只有一个用户，用 id=2 表示「新持闸者」）
        with database.get_db() as conn:
            conn.execute("UPDATE videos SET parsed_by = 2 WHERE video_url = ?", (URL,))
            conn.execute("UPDATE videos SET regenerating_at = ? WHERE video_url = ?",
                         ((datetime.now(timezone.utc)
                           - timedelta(seconds=database.VIDEO_REGENERATE_TTL_SECONDS + 60)
                           ).isoformat(), URL))
        assert database.acquire_regenerate_gate(URL, 2) is True
        assert lock_of()[0] == 2

        # 1 迟到地放闸
        database.release_regenerate_gate(URL, 1)

        assert lock_of()[0] == 2, (
            "过期的持闸者把别人的锁清掉了 —— 第三个人会以为没人占着而同时进来")
        assert database.acquire_regenerate_gate(URL, 2) is False, (
            "锁被别人清掉了，于是同一个持闸者又抢了一次")


# ── 3. 跨进程：本文件存在的理由 ─────────────────────────────

_CHILD = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, os.environ["BACKEND_DIR"])
    os.environ.setdefault("JWT_SECRET", "cross-process-test")
    import database
    database.DB_PATH = os.environ["DB_FILE"]
    url, owner = os.environ["VIDEO_URL"], int(os.environ["OWNER_ID"])
    ok = database.acquire_regenerate_gate(url, owner)
    # **不放**：锁留在库里，后面的子进程才看得到它。
    print("ACQUIRED" if ok else "BUSY")
""")


@pytest.fixture()
def child_script(tmp_path):
    p = tmp_path / "child_acquire.py"
    p.write_text(_CHILD, encoding="utf-8")
    return p


def _run_child(script, db_path, url, owner):
    # `database.__file__` 是 .../backend/database.py，所以它的 dirname 就是 backend。
    # 多套一层会指到仓库根，子进程于是 import 不到 database（实测踩过）。
    backend_dir = os.path.dirname(os.path.abspath(database.__file__))
    env = {**os.environ,
           "BACKEND_DIR": backend_dir,
           "DB_FILE": str(db_path),
           "VIDEO_URL": url,
           "OWNER_ID": str(owner)}
    out = subprocess.run([sys.executable, str(script)], env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, (
        f"子进程退出了 {out.returncode}：\nstdout={out.stdout}\nstderr={out.stderr}")
    return out.stdout.strip().splitlines()[-1]


class TestGateAcrossProcesses:
    def test_second_process_cannot_take_a_lock_the_first_process_holds(self, db, child_script):
        """票面的关键分界：**两个 worker 之间也能拦住**。

        只做到单进程内有效，等于把静默失效缩小成一个更难复现的形状。
        """
        seed_ready()
        db_path = database.DB_PATH

        first = _run_child(child_script, db_path, URL, 1)
        assert first == "ACQUIRED", f"第一个子进程本该抢到，实得 {first}"

        second = _run_child(child_script, db_path, URL, 1)
        assert second == "BUSY", (
            "第二个**进程**在第一个还占着锁时抢到了 —— 闸门只活在某个进程的内存里。"
            + "这正是工单 #20 描述的失效形状：两次调模型、两次扣额度、不报错")

        # 库里那一列也必须是它 —— 断言的是可观察的结果，不是「函数返回了 False」
        assert lock_of()[0] == 1

    def test_release_in_one_process_frees_the_lock_for_the_next(self, db, child_script):
        """对照组：跨进程也是**能放**的，否则闸门就是单向门。"""
        seed_ready()
        db_path = database.DB_PATH

        assert _run_child(child_script, db_path, URL, 1) == "ACQUIRED"
        database.release_regenerate_gate(URL, 1)
        assert _run_child(child_script, db_path, URL, 1) == "ACQUIRED", (
            "前一个进程放了闸，另一个进程仍抢不到 —— 闸门成了单向门")

    def test_many_processes_still_yield_exactly_one_holder(self, db, child_script):
        """N 个进程依次抢，恰好一个拿到。

        顺序执行就够：子进程不放闸，锁留在库里，所以「恰好一个」与它们是否
        真的同时到达无关。这条因此**不会 flaky**，而它断的正是旧实现
        （各自一份空 set → N 个全部成功）。
        """
        seed_ready()
        db_path = database.DB_PATH
        results = [_run_child(child_script, db_path, URL, 1) for _ in range(5)]

        assert results.count("ACQUIRED") == 1, (
            f"5 个进程里 {results.count('ACQUIRED')} 个抢到了：{results}")
        assert results.count("BUSY") == 4