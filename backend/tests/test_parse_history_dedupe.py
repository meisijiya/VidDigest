"""解析历史按 (user_id, video_url) 真的只有一行（工单 #17 第 4 项）。

两条独立的证据链，缺一不可：

1. **并发写**：原来的 `upsert_parse_history` 是「先 SELECT 再 INSERT」的
   读-改-写。两个并发请求各自查到「还没有这行」就各插一行——
   docstring 承诺的去重在并发下并不成立（实测 10 线程 → 2 行）。
   触发条件现实存在：``App.vue`` 的 persistParseRecord 与
   ``VideoSummary.vue`` 的 persistHistory 是两个互不相干的组件，
   都会对同一 URL 发 save。
2. **老库升级**：把索引改成 UNIQUE 之前必须先把存量重复行合成掉，
   否则 ``CREATE UNIQUE INDEX`` 直接失败、init_db 整段 abort。
   ``db`` 夹具每次都造一个**全新**库，这条路径在它下面一次都走不到，
   所以升级测试必须用 ``legacy_db``。

判据读的是 parse_history 里实际的行数与列值，以及 ``PRAGMA index_list``
报出来的唯一性——不测私有函数，不断言内部调用顺序。
"""
import threading

import database

URL = "https://example.com/v"


def rows_of(db, uid, url):
    with db.get_db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM parse_history WHERE user_id = ? AND video_url = ?",
            (uid, url),
        )]


def index_is_unique(db, name="idx_history_user_url"):
    with db.get_db() as c:
        return any(
            r["name"] == name and r["unique"]
            for r in c.execute("PRAGMA index_list(parse_history)")
        )


# ── 并发写 ─────────────────────────────────────────────────

class TestConcurrentUpsertLeavesOneRow:
    THREADS = 10

    def test_ten_threads_writing_the_same_pair_leave_exactly_one_row(
        self, db, make_user
    ):
        uid = make_user("a@example.com")
        barrier = threading.Barrier(self.THREADS)
        returned, errors = [], []
        lock = threading.Lock()

        def write(i):
            try:
                barrier.wait()
                hid = db.upsert_parse_history(uid, URL, video_title=f"标题-{i}")
                with lock:
                    returned.append(hid)
            except Exception as e:  # noqa: BLE001 — 失败要原样带出去，不能吞
                with lock:
                    errors.append(e)

        ts = [threading.Thread(target=write, args=(i,)) for i in range(self.THREADS)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        assert errors == [], f"并发写抛了异常：{errors!r}"
        rows = rows_of(db, uid, URL)
        assert len(rows) == 1, (
            f"{self.THREADS} 个线程写同一个 (user_id, video_url)，"
            f"库里却有 {len(rows)} 行"
        )

    def test_every_caller_gets_the_same_record_id(self, db, make_user):
        """并发下每个调用方拿到的必须是**同一条**记录的 id。

        这条比「只有一行」更贴后果：前端拿到这个 id 之后会去收藏、
        会去改标签。返回错 id 时库里有几行都无所谓——收藏会落到别的行上，
        而界面上看不出任何异常。
        """
        uid = make_user("a@example.com")
        barrier = threading.Barrier(self.THREADS)
        returned, errors = [], []
        lock = threading.Lock()

        def write(i):
            try:
                barrier.wait()
                hid = db.upsert_parse_history(uid, URL, video_title=f"标题-{i}")
                with lock:
                    returned.append(hid)
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append(e)

        ts = [threading.Thread(target=write, args=(i,)) for i in range(self.THREADS)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        assert errors == [], f"并发写抛了异常：{errors!r}"
        assert len(set(returned)) == 1, (
            f"不同调用方拿到了不同的记录 id：{sorted(set(returned))}"
        )
        assert returned[0] == rows_of(db, uid, URL)[0]["id"]


# ── 合并语义（这条是新写的 SQL 承诺的，不能只靠 docstring）─────

class TestMergeSemanticsOnTheUpsertPath:
    """`ON CONFLICT DO UPDATE` 里那串 COALESCE(NULLIF(excluded.x,''), t.x)。

    它顶掉的正是原来那两个 `_merge` / `_merge_text` 助手，
    所以它不是「顺便实现的」，而是 upsert 的全部承诺本身：
    视频源保存不覆盖 AI 结果，反之亦然。
    """

    def test_two_partial_saves_add_up_to_one_record(self, db, make_user):
        """两个组件各写一次，任何一次都拿不全，合并才有意义。

        App.vue 的 persistParseRecord 只带视频源信息，
        VideoSummary 的 persistHistory 只带 AI 产出。
        """
        uid = make_user("a@example.com")
        db.upsert_parse_history(
            uid, URL, video_title="标题", video_data={"title": "标题"},
        )
        db.upsert_parse_history(
            uid, URL, summary_md="总结", subtitle_data={"segments": [1]},
        )

        rows = rows_of(db, uid, URL)
        assert len(rows) == 1
        row = rows[0]
        assert row["video_title"] == "标题"
        assert row["summary_md"] == "总结"
        assert "标题" in row["video_data"], "第二次保存把视频源信息抹掉了"
        assert "segments" in row["subtitle_data"], "第二次保存把字幕抹掉了"

    def test_a_save_with_nothing_set_erases_nothing(self, db, make_user):
        """第二次什么都不带：内容一个都不许被空值抹掉。

        这是最危险的一种写法：把 `COALESCE(NULLIF(excluded.x,''), t.x)`
        简化成 `excluded.x` 时，全部用例照样绿——因为**每一次**调用
        都至少带了一个非空字段，空值覆盖根本轮不到它上场。
        """
        uid = make_user("a@example.com")
        db.upsert_parse_history(
            uid, URL, video_title="标题", summary_md="总结",
            video_data={"a": 1}, subtitle_data={"segments": [1]},
        )

        db.upsert_parse_history(uid, URL)  # 全部是默认值

        row = rows_of(db, uid, URL)[0]
        assert row["video_title"] == "标题", "空值把标题抹掉了"
        assert row["summary_md"] == "总结", "空值把总结抹掉了"
        assert row["video_data"] != "", "空值把视频源信息抹掉了"
        assert row["subtitle_data"] != "", "空值把字幕抹掉了"


# ── 老库升级 ───────────────────────────────────────────────

def _seed_duplicated_legacy_library():
    """在 legacy_db 指向的库里摆出「上一版」的结构与三行重复数据。

    三行刻意是**互补**的：一条只有视频源信息（对应 App.vue 的
    persistParseRecord），一条只有 AI 产出（对应 VideoSummary 的
    persistHistory），一条只有追问记录，只留最新那条会抹掉另外两半。
    """
    database.init_db()  # 先要一份**真实**的现行 DDL，再从里面造老库
    with database.get_db() as c:
        ddl = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='parse_history'"
        ).fetchone()["sql"]
        uid = c.execute(
            "INSERT INTO users (email, password_hash) VALUES ('old@x.com', 'h')"
        ).lastrowid

        c.execute("DROP TABLE parse_history")  # 连同它自己的索引一起掉
        c.execute(ddl)
        # 老版就是一条**普通**索引——唯一性当时只写在 docstring 里。
        c.execute(
            "CREATE INDEX idx_history_user_url ON parse_history(user_id, video_url)"
        )
        c.executemany(
            """INSERT INTO parse_history
               (user_id, video_url, video_title, summary_md, chat_history,
                is_favorite, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (uid, URL, "老记录的视频标题", "", "[]", 0, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
                (uid, URL, "", "老记录的总结", "[]", 1, "2026-01-02T00:00:00", "2026-01-02T00:00:00"),
                (uid, URL, "", "", '[{"question":"问过","answer":"答过"}]', 0,
                 "2026-01-03T00:00:00", "2026-01-03T00:00:00"),
            ],
        )
    return uid


class TestLegacyDatabaseUpgrade:
    def test_init_db_merges_duplicates_without_losing_anything(self, legacy_db):
        """从一个已经有重复行的老库启动。

        这一行以前会直接抛 ``sqlite3.OperationalError: UNIQUE constraint failed``：
        唯一索引建在合成之前，老库里那三行让它根本建不出来。
        """
        uid = _seed_duplicated_legacy_library()

        database.init_db()

        rows = rows_of(legacy_db, uid, URL)
        assert len(rows) == 1, f"重复行没被合成，还剩 {len(rows)} 行"
        row = rows[0]
        # 每列取组内最新的非空值：两条重复行往往是互补的，
        # 只留最新那条会把另一半悄悄抹掉。
        assert row["video_title"] == "老记录的视频标题", "视频源信息被抹掉了"
        assert row["summary_md"] == "老记录的总结", "AI 产出被抹掉了"
        assert "问过" in row["chat_history"], "追问记录被抹掉了"
        # 收藏是唯一一处用户明说了「别删它」的地方，合成时必须取并集。
        assert row["is_favorite"] == 1, "组里任一条被收藏，幸存的那条就必须是收藏"
        # created_at 是「我第一次解析它」的时间，取最新会把它伪装成新记录，
        # 进而在滚动裁剪里多占一格。
        assert row["created_at"] == "2026-01-01T00:00:00", row["created_at"]

    def test_the_index_is_really_unique_after_upgrade(self, legacy_db):
        """「建了索引」与「索引是唯一的」是两件事。

        `CREATE UNIQUE INDEX IF NOT EXISTS` 遇到同名索引是**空操作**：
        老库里那条同名普通索引不删掉，这句就静默地什么都没做，
        而代码与注释都以为唯一性已经成立。
        """
        _seed_duplicated_legacy_library()
        assert not index_is_unique(legacy_db), "前提不成立：老库里已经有唯一索引了"

        database.init_db()

        assert index_is_unique(legacy_db), (
            "升级后 (user_id, video_url) 仍然没有唯一约束：并发去重没有兜底"
        )

    def test_running_init_db_twice_changes_nothing_more(self, legacy_db):
        """init_db 每次启动都跑。第二次必须是无操作，且不再报重复。"""
        uid = _seed_duplicated_legacy_library()
        database.init_db()
        first = rows_of(legacy_db, uid, URL)

        database.init_db()

        assert rows_of(legacy_db, uid, URL) == first, "重复跑 init_db 又动了数据"
        assert index_is_unique(legacy_db), "重复跑 init_db 把唯一索引弄丢了"
