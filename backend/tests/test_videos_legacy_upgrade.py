"""`videos` 表的老库升级路径（工单 #19 第 4 项）。

## 为什么这条路径此前一次都没跑过

``db`` 夹具每次 ``init_db()`` 造出一张**全新**的表，于是「老库缺列、靠迁移
补上」那条路径在它下面永远走不到。补列排错了顺序，全量绿，真库一升级就
``no such column`` 打不开——AGENTS.md 已经记着一次这样的实测（2026-10-03，
索引排在补列迁移前面，老库直接 abort）。

而 ``videos`` 上这个风险是**刚变实**的：工单 #17 第 2 项给
``reserve_video`` 加了 ``video_title`` / ``cover_url`` 两个入参，占位时就写
进去（ADR 0014），两列由 ``_migrate_video_card_columns`` 补。而
``complete_video`` / ``publish_video_card`` / ``list_community_videos``
/ ``search_community_videos`` 全都 SELECT 这两列——**老库升级打不开是真会
发生的，不是假想**。

## 老库长什么样：从提交历史里取，不凭记忆

``videos`` 表诞生于 ``4a72696``（工单 #6），当时的 DDL 抄在
``_LEGACY_VIDEOS_DDL`` 下面，与该提交逐字一致。三处差异都是**查出来的**，
不是猜的：

  · 缺 ``video_title`` / ``cover_url``（工单 #7 的 ``70f9fa0`` 才补）
  · **有** ``tags``——``git log -S "tags TEXT DEFAULT '[]'"`` 显示它与
    videos 表同在 ``4a72696`` 建出，所以每张真表都有它。触发器会引用
    ``new.tags``，缺了它索引整条路都建不起来；但那是一个**从未存在过**的
    库形状，所以本文件不测它（AGENTS.md：别为不存在的状态写断言）。
  · **没有** ``videos_fts``——外部内容 FTS5 表是工单 #7 才加的。留着它的话
    一行老视频都不会被灌进索引，而界面上的表现是「升级完搜不到任何东西」。

## 判据读的是外部可观察的东西

库的列、索引的实际行数、社区搜索真的搜不搜得到。不测私有函数，不断言
迁移函数的调用顺序。
"""
import database

URL_A = "https://example.com/a"
URL_B = "https://example.com/b"

#: 逐字取自 `git show 4a72696:backend/database.py`（工单 #6，videos 表诞生）。
_LEGACY_VIDEOS_DDL = """
CREATE TABLE videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    summary_md TEXT DEFAULT '',
    mindmap_md TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    subtitle_text TEXT DEFAULT '',
    parsed_by INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
)
"""

#: 同一提交里的唯一索引。一并丢掉，否则老库比真的老库干净。
_LEGACY_VIDEOS_INDEX = "CREATE UNIQUE INDEX idx_videos_url ON videos(video_url)"


def _columns(db):
    with db.get_db() as c:
        return {r["name"] for r in c.execute("PRAGMA table_info(videos)")}


def _rows(db):
    with db.get_db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM videos ORDER BY id")]


def _has_table(db, name):
    with db.get_db() as c:
        return c.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)
        ).fetchone() is not None


def _search(db, q, **kw):
    """走公开出口检索，不直接查 FTS 表——索引坏掉时的症状正是搜不到。"""
    return database.search_community_videos(q=q, **kw)


def _seed_legacy_library(db):
    """在 legacy_db 指向的库里摆出工单 #6 那个版本的结构与两行存量数据。

    先 ``init_db()`` 拿一份**真实的**现行 DDL（users 等表要齐全，外键与
    播种都依赖它们），再把 ``videos`` 整张换成老样子——只换这一张，
    其余表保持现状：这正是「升级现场」的样子。
    """
    db.init_db()
    with db.get_db() as c:
        uid = c.execute(
            "INSERT INTO users (email, password_hash) VALUES ('old@x.com', 'h')"
        ).lastrowid

        # DROP TABLE videos 会连带删掉建在它上面的三个 FTS 触发器。
        c.execute("DROP TABLE videos")
        c.execute("DROP TABLE IF EXISTS videos_fts")
        c.execute(_LEGACY_VIDEOS_DDL)
        c.execute(_LEGACY_VIDEOS_INDEX)
        c.executemany(
            """INSERT INTO videos
               (video_url, status, summary_md, mindmap_md, tags,
                subtitle_text, parsed_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # 一条 ready 的真内容。
                # 标签刻意用 **5 个字**：FTS 走 trigram 分词器，低于
                # MIN_FTS_TERM_CHARS(3) 的词在索引里没有对应 trigram，
                # 查了必然是空。拿 2 字词当查询词会得到一条恒红的用例，
                # 而它与「索引有没有建成」毫无关系。
                (URL_A, "ready", "老库里的总结", "{}", '["计算机科普"]',
                 "字幕", uid, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
                # 一条 pending 占位：标签刻意与上面**不共用词**，
                # 这样「搜不到它」就不能靠「这个词只匹配得到那一条」蒙混过去。
                (URL_B, "pending", "", "{}", '["待解析视频标签"]',
                 "", None, "2026-01-02T00:00:00", "2026-01-02T00:00:00"),
            ],
        )


class TestLegacyShapeIsReallyLegacy:
    """前提自检。老库摆得不对，后面的断言全都失去意义。"""

    def test_it_really_lacks_the_two_card_columns(self, legacy_db):
        _seed_legacy_library(legacy_db)

        cols = _columns(legacy_db)
        assert "video_title" not in cols, "前提不成立：老库不该有 video_title"
        assert "cover_url" not in cols, "前提不成立：老库不该有 cover_url"

    def test_it_really_has_no_search_index(self, legacy_db):
        _seed_legacy_library(legacy_db)

        assert not _has_table(legacy_db, "videos_fts"), (
            "前提不成立：工单 #6 那个版本还没有 videos_fts"
        )

    def test_it_still_has_tags(self, legacy_db):
        # tags 与 videos 表同在 4a72696 建出。老库有它，触发器才建得起来。
        _seed_legacy_library(legacy_db)

        assert "tags" in _columns(legacy_db)


class TestUpgradeAddsWhatTheCodeReads:
    def test_init_db_adds_both_card_columns(self, legacy_db):
        _seed_legacy_library(legacy_db)

        legacy_db.init_db()

        cols = _columns(legacy_db)
        assert "video_title" in cols, "升级后没有 video_title，列表一 SELECT 就报错"
        assert "cover_url" in cols, "升级后没有 cover_url，同上"

    def test_new_columns_default_to_empty_string_not_null(self, legacy_db):
        """社区列表会把这两列渲染出来。补成 NULL 的话，模板里 `v.title || ''`
        之类的地方还能过，而 `v.video_title` 直接渲染出 "None"。"""
        _seed_legacy_library(legacy_db)

        legacy_db.init_db()

        for row in _rows(legacy_db):
            assert row["video_title"] == "", f"存量行的新列是 {row['video_title']!r}，不是空串"
            assert row["cover_url"] == "", f"存量行的新列是 {row['cover_url']!r}，不是空串"

    def test_existing_rows_survive_untouched(self, legacy_db):
        _seed_legacy_library(legacy_db)

        legacy_db.init_db()

        rows = _rows(legacy_db)
        assert len(rows) == 2, "升级把存量行弄没了"
        by_url = {r["video_url"]: r for r in rows}
        assert by_url[URL_A]["summary_md"] == "老库里的总结", "升级把内容抹了"
        assert by_url[URL_A]["tags"] == '["计算机科普"]', "升级把标签抹了"
        assert by_url[URL_A]["subtitle_text"] == "字幕", "升级把字幕抹了"
        assert by_url[URL_B]["status"] == "pending", "占位行的状态被改写了"

    def test_the_unique_index_is_still_unique(self, legacy_db):
        """唯一索引是老库里就有的。若迁移过程重建了它却不带 UNIQUE，
        「同一链接只解析一次」这条承诺在升级后就没了，且不报任何错。"""
        _seed_legacy_library(legacy_db)

        legacy_db.init_db()

        with legacy_db.get_db() as c:
            unique = any(
                r["name"] == "idx_videos_url" and r["unique"]
                for r in c.execute("PRAGMA index_list(videos)")
            )
        assert unique, "升级后 idx_videos_url 不再是唯一索引"


class TestUpgradeBuildsAWorkingSearchIndex:
    def test_the_index_table_exists_after_upgrade(self, legacy_db):
        _seed_legacy_library(legacy_db)

        legacy_db.init_db()

        assert _has_table(legacy_db, "videos_fts"), "升级后社区全文检索没有建起来"

    def test_preexisting_rows_are_actually_indexed(self, legacy_db):
        """本文件最该守住的一条。

        触发器只同步**它建立之后**发生的写入。老库里的存量行在触发器建立
        之前就已经在了——不显式 rebuild 的话，索引表建起来了、触发器也在了、
        界面也不报错，**唯独每一条老视频都搜不到**。那是最坏的一种静默：
        看起来全好了。
        """
        _seed_legacy_library(legacy_db)

        legacy_db.init_db()

        hit = _search(legacy_db, "计算机科普")
        assert [i["video_url"] for i in hit["items"]] == [URL_A], (
            "升级前就存在的行没被灌进检索索引：老视频全都搜不到"
        )

    def test_pending_rows_stay_out_of_search(self, legacy_db):
        """反向对照：上一条若只是「索引里全是行」，这条会红。

        同一个词在两个方向上各走一遍——先确认它现在搜不到（因为 pending），
        再确认它就绪之后立刻搜得到。于是「搜不到」与「索引坏了」分得开。

        关键词必须在 ``complete_video`` **之后**重新写进去：那次调用会用
        解析结果里的 tags 覆盖整列（``json.dumps(list(tags) if tags else [])``），
        占位期那个标签活不到就绪之后。拿它当跨两态的钥匙，会读到
        「标签被覆盖了」，而那正是它应有的行为。
        """
        _seed_legacy_library(legacy_db)
        legacy_db.init_db()

        assert _search(legacy_db, "待解析视频标签")["items"] == [], (
            "占位行漏进社区搜索了"
        )

        assert database.complete_video(
            URL_B, summary_md="总结", tags=["待解析视频标签"],
        ) == 1

        hit = _search(legacy_db, "待解析视频标签")
        assert [i["video_url"] for i in hit["items"]] == [URL_B], (
            "同一个词在行就绪后仍然搜不到：索引与触发器没接上"
        )

    def test_url_lookup_still_finds_the_legacy_row(self, legacy_db):
        """按链接精确定位走的是普通索引而不是 FTS，它独立地验一遍存量数据。"""
        _seed_legacy_library(legacy_db)

        legacy_db.init_db()

        hit = _search(legacy_db, URL_A)
        assert [i["video_url"] for i in hit["items"]] == [URL_A]

    def test_triggers_survive_and_keep_the_index_in_sync(self, legacy_db):
        """索引建成只是一半。触发器才是此后唯一的同步机制——少一个，
        任何一次 complete_video 之后那条视频就再也搜不到了。

        顺序不能反：``publish_video_card`` 的 ``WHERE status = 'ready'``
        会正确地拒绝一个还在占位中的行（它得先有内容才谈得上做卡片）。
        """
        _seed_legacy_library(legacy_db)
        legacy_db.init_db()

        assert database.complete_video(URL_B, summary_md="总结") == 1
        assert database.publish_video_card(URL_B, "新标题", "https://img/b.png") == 1

        hit = _search(legacy_db, "新标题")
        assert [i["video_url"] for i in hit["items"]] == [URL_B], (
            "触发器没跟上：刚补完卡片的那条视频搜不到"
        )


class TestUpgradeIsIdempotent:
    def test_running_init_db_twice_changes_nothing(self, legacy_db):
        """服务每次启动都跑 init_db。第二次撞上 duplicate column 或
        rebuild 重复插入，才是「本地好好的、部署后炸」的形态。"""
        _seed_legacy_library(legacy_db)
        legacy_db.init_db()

        legacy_db.init_db()
        legacy_db.init_db()

        rows = _rows(legacy_db)
        assert len(rows) == 2, "重复 init_db 把行写重了"
        assert [i["video_url"] for i in _search(legacy_db, "计算机科普")["items"]] == [URL_A]

    def test_a_current_shaped_library_is_also_untouched(self, legacy_db):
        """另一头：已经是现行结构的库（也就是所有开发机与新部署），
        升级路径同样不许动它。"""
        legacy_db.init_db()
        with legacy_db.get_db() as c:
            c.execute(
                "INSERT INTO videos (video_url, status, tags) VALUES (?, 'ready', '[]')",
                (URL_A,),
            )

        legacy_db.init_db()

        rows = _rows(legacy_db)
        assert len(rows) == 1
        assert rows[0]["video_url"] == URL_A
