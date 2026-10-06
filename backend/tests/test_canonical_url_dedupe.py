"""`videos` 按 canonical 去重 + 唯一索引（工单 #25 第一步 · 第 3 片）。

## 这一步跑在 init_db 里，也就是**每次部署都跑一次**

出错就是直接毁社区内容——所以判据的排优先级是：
「不丢东西」> 「去干净」> 「建索引」。

## 两个决定（本轮拍板）

1. 合并时 ``parsed_by`` **取最新那条**——与内容取最新那条一致，「后来者接管」。
   它是**产品语义**不是工程细节：它决定谁能重新解析这一条
   （`can_regenerate` 与 `complete_video` 的 ``WHERE parsed_by IS ?`` 都落在它上面）。
2. 某一组里有一行正被覆盖（``regenerating_by`` 非空）→ **整组跳过、不建唯一索引**。
   宁可让索引暂时保持普通索引（`PRAGMA index_list` 里 unique=0，**可观察**），
   下次启动再试；绝不为了建一条索引去强抢别人正在进行的覆盖。
"""
import pytest

import database
from url_canonical import canonical_video_url

BV = "https://www.bilibili.com/video/BV1aa411c7mD"
BILI_FORMS = [
    BV,
    "https://m.bilibili.com/video/BV1aa411c7mD",
    "https://www.bilibili.com/video/BV1aa411c7mD?spm_id_from=333.1007.tianma",
]
SHORT_LINK = "https://b23.tv/AbCdEf"
OTHER = "https://www.bilibili.com/video/BV1zz411c7mX"


# ── 造库 ─────────────────────────────────────────────────────

def _seed_duplicates(rows):
    """在一个**还没有 canonical 唯一索引**的库里摆出重复行。

    为什么不能直接用 `db` 夹具插：它每次 `init_db()`，而 init_db 现在会把
    唯一索引建出来——于是第二行当场被 IntegrityError 拦下，
    **重复行根本造不出来**，而去重路径一次都走不到。

    而重复行只可能出现在「还没有唯一索引」的库里，也就是老库。
    所以这里用 `legacy_db`：先 init_db 拿到真实结构，把那条索引摘掉
    （降级成第 2 片留下的普通索引），再插重复行，最后重新 init_db 触发去重。

    这条路径与真实升级完全一致，不是为了测试而造的特例。
    """
    database.init_db()  # 真实现行结构
    with database.get_db() as c:
        c.execute("DROP INDEX IF EXISTS idx_videos_canonical")  # 降级成普通索引
        for row in rows:
            c.execute(
                """INSERT INTO videos
                   (video_url, canonical_url, status, parsed_by, video_title,
                    cover_url, summary_md, mindmap_md, subtitle_text, tags,
                    regenerating_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["video_url"], canonical_video_url(row["video_url"]),
                 row.get("status", "ready"), row.get("parsed_by"),
                 row.get("video_title", ""), row.get("cover_url", ""),
                 row.get("summary_md", ""), row.get("mindmap_md", ""),
                 row.get("subtitle_text", ""), row.get("tags", "[]"),
                 row.get("regenerating_by"),
                 row.get("created_at", "2026-01-01T00:00:00"),
                 row.get("updated_at", "2026-01-01T00:00:00")),
            )


def _rows(table="videos", columns="*"):
    with database.get_db() as c:
        return [dict(r) for r in c.execute(f"SELECT {columns} FROM {table} ORDER BY id")]


def _index_is_unique(table="videos", name="idx_videos_canonical"):
    with database.get_db() as c:
        return any(
            r["name"] == name and r["unique"]
            for r in c.execute(f"PRAGMA index_list({table})")
        )


# ── 合并规则 ─────────────────────────────────────────────────

class TestMerge:
    def test_same_video_in_two_forms_becomes_one_row(self, legacy_db):
        _seed_duplicates([{"video_url": u} for u in BILI_FORMS[:2]])
        assert len(_rows()) == 2, "前提不成立：还没插两行"

        database.init_db()

        rows = _rows()
        assert len(rows) == 1, f"同一个视频还剩 {len(rows)} 行"
        assert rows[0]["canonical_url"] == BV

    def test_different_videos_are_never_merged(self, legacy_db):
        """反向那一半：只写上面那条的话，「去重」也可能是「把全表并成一行」。

        而那种实现**不报错**，只是社区慢慢塌成一条。
        """
        _seed_duplicates([{"video_url": u} for u in [BILI_FORMS[0], OTHER, SHORT_LINK]])
        database.init_db()
        assert len(_rows()) == 3

    def test_content_is_merged_not_dropped(self, legacy_db):
        """两条重复行往往是**互补**的：只留最新那条会把另一半悄悄抹掉。

        这里让较早那条带标题与封面，较晚那条带总结——
        合��之后两半都必须在。
        """
        _seed_duplicates([
            {
                "video_url": BILI_FORMS[0],
                "video_title": "早那条的标题",
                "cover_url": "https://img/a.jpg",
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            },
            {
                "video_url": BILI_FORMS[1],
                "summary_md": "晚那条的总结",
                "created_at": "2026-02-01T00:00:00",
                "updated_at": "2026-02-01T00:00:00",
            },
        ])
        database.init_db()

        row = _rows()[0]
        assert row["video_title"] == "早那条的标题", "较早那条的标题被抹掉了"
        assert row["cover_url"] == "https://img/a.jpg", "较早那条的封面被抹掉了"
        assert row["summary_md"] == "晚那条的总结", "较晚那条的总结被抹掉了"

    def test_created_at_keeps_the_earliest_one(self, legacy_db):
        """它是「社区里第一次出现这个视频」的时间，取最新会把它伪装成新视频。"""
        _seed_duplicates([
            {"video_url": BILI_FORMS[0], "created_at": "2026-01-01T00:00:00",
             "updated_at": "2026-01-01T00:00:00"},
            {"video_url": BILI_FORMS[1], "created_at": "2026-02-01T00:00:00",
             "updated_at": "2026-02-01T00:00:00"},
        ])
        database.init_db()
        assert _rows()[0]["created_at"] == "2026-01-01T00:00:00"

    def test_survivor_is_the_latest_row(self, legacy_db):
        """幸存者 = 更新那一条；`video_url` 与 `updated_at` 都取它的。"""
        _seed_duplicates([
            {"video_url": BILI_FORMS[0], "updated_at": "2026-01-01T00:00:00"},
            {"video_url": BILI_FORMS[1], "updated_at": "2026-02-01T00:00:00"},
        ])
        database.init_db()
        assert _rows()[0]["video_url"] == BILI_FORMS[1]
        assert _rows()[0]["updated_at"] == "2026-02-01T00:00:00"

    def test_parsed_by_follows_the_latest_row(self, legacy_db):
        """拍板结论：取最新那条的。它决定谁能重新解析这一条。"""
        _seed_duplicates([
            {"video_url": BILI_FORMS[0], "parsed_by": 1, "updated_at": "2026-01-01T00:00:00"},
            {"video_url": BILI_FORMS[1], "parsed_by": 2, "updated_at": "2026-02-01T00:00:00"},
        ])
        database.init_db()
        assert _rows()[0]["parsed_by"] == 2, "归属没跟着幸存者走 —— 重新解析的权限判错了"

    def test_pending_never_overwrites_ready(self, legacy_db):
        """只有一条是 pending 而另一条已 ready 时，合成 pending 会让社区内容
        重新变成「还在解析」——而那份内容明明存在。"""
        _seed_duplicates([
            {"video_url": BILI_FORMS[0], "status": "ready", "summary_md": "总结",
             "updated_at": "2026-01-01T00:00:00"},
            {"video_url": BILI_FORMS[1], "status": "pending",
             "updated_at": "2026-02-01T00:00:00"},
        ])
        database.init_db()

        row = _rows()[0]
        assert row["status"] == "ready", "社区内容被降级成「还在解析」"
        assert row["summary_md"] == "总结", "内容跟着状态一起丢了"

    def test_short_links_are_never_merged(self, legacy_db):
        """两条不同短链的 canonical 就是它们自己，不该被归一函数牵走。"""
        _seed_duplicates([{"video_url": "https://b23.tv/AbCdEf"},
                 {"video_url": "https://b23.tv/ZzYyXx"}])
        database.init_db()
        assert len(_rows()) == 2, "两个不同的短链被合成了一条"


# ── 正在被覆盖的一组：跳过，且不建唯一索引 ───────────────────────

class TestLockedGroupIsSkipped:
    def test_locked_row_is_left_alone(self, legacy_db):
        _seed_duplicates([
            {"video_url": BILI_FORMS[0], "summary_md": "早那条",
             "updated_at": "2026-01-01T00:00:00"},
            {"video_url": BILI_FORMS[1], "summary_md": "晚那条",
             "regenerating_by": 9, "updated_at": "2026-02-01T00:00:00"},
        ])
        database.init_db()

        rows = _rows()
        assert len(rows) == 2, "正在被覆盖的行被合并了 —— 那次覆盖会静默失败"
        assert any(r["regenerating_by"] == 9 for r in rows), "锁被清掉了"
        assert {r["summary_md"] for r in rows} == {"早那条", "晚那条"}, "内容被抹了"

    def test_no_unique_index_when_a_group_was_skipped(self, legacy_db):
        """去重不彻底时不建唯一索引 —— 那条索引保持**普通**索引，
        而这是可观察的（`PRAGMA index_list` 里 unique=0），下次启动会再试。"""
        _seed_duplicates([
            {"video_url": BILI_FORMS[0], "updated_at": "2026-01-01T00:00:00"},
            {"video_url": BILI_FORMS[1], "regenerating_by": 9,
             "updated_at": "2026-02-01T00:00:00"},
        ])
        database.init_db()

        assert _index_is_unique() is False, (
            "有一组被跳过了却建了唯一索引 —— 它要么建不出来，"
            "要么这两行已经被合过（那锁去哪了？）"
        )

    def test_a_clean_database_still_gets_the_unique_index(self, legacy_db):
        """反向那一半：上面两条靠的是「跳过」，这一条证明跳过不是常态。

        没有锁的那一组必须真的被合掉，而且唯一索引要真的建出来。
        """
        _seed_duplicates([{"video_url": u} for u in BILI_FORMS[:2]])
        database.init_db()

        assert len(_rows()) == 1, "没有锁却没合并"
        assert _index_is_unique() is True, "去干净了却没建唯一索引"


# ── 唯一索引本身 ─────────────────────────────────────────────

class TestUniqueIndex:
    def test_the_index_is_really_unique_not_merely_present(self, db):
        """「建了索引」与「索引是唯一的」是两件事。

        这条与 `_migrate_parse_history_unique_url` 那次同款：老库那条同名
        **普通**索引不先删，`CREATE UNIQUE INDEX IF NOT EXISTS` 会是空操作，
        于是代码与注释都以为唯一性成立，而数据库上根本没有。
        """
        database.init_db()
        assert _index_is_unique() is True

    def test_inserting_a_second_form_of_the_same_video_is_rejected(self, db):
        """唯一索引真正在拦东西——这是 reserve_video 并发安全的地基。"""
        with database.get_db() as c:
            c.execute(
                "INSERT INTO videos (video_url, canonical_url, status) "
                "VALUES (?, ?, 'ready')", (BILI_FORMS[0], BV)
            )
        with pytest.raises(Exception) as exc:
            with database.get_db() as c:
                c.execute(
                    "INSERT INTO videos (video_url, canonical_url, status) "
                    "VALUES (?, ?, 'ready')", (BILI_FORMS[1], BV)
                )
        assert "UNIQUE" in str(exc.value).upper(), f"不是唯一约束拦下的：{exc.value}"

    def test_empty_canonical_rows_are_not_constrained(self, db):
        """canonical 为空的行不受唯一约束。

        canonical 为空只可能是 video_url 本身为空，而那种行本来就没有
        「同一链接」的语义——让它参与唯一约束只会让建索引失败。
        """
        database.init_db()
        # ⚠️ video_url 必须**不同**：`videos.video_url` 自己就带唯一索引
        # （idx_videos_url），两行空 URL 会被它先拦下，压根到不了这条
        # partial 索引。要测的是「canonical 还没回填」那种行，不是「空 URL」。
        with database.get_db() as c:
            c.execute("INSERT INTO videos (video_url, canonical_url, status) "
                      "VALUES (?, '', 'ready')", ("https://a/x",))
            c.execute("INSERT INTO videos (video_url, canonical_url, status) "
                      "VALUES (?, '', 'ready')", ("https://b/y",))
        assert len(_rows()) == 2, "canonical 为空的两行被唯一索引拦下了"

    def test_running_init_db_twice_is_a_no_op(self, legacy_db):
        """init_db 每次启动都跑。第二遍必须既不重复合并、也不动数据。"""
        _seed_duplicates([{"video_url": u} for u in BILI_FORMS[:2]])
        database.init_db()
        first = _rows()

        database.init_db()

        assert _rows() == first, "重复跑 init_db 又动了数据"
        assert _index_is_unique() is True, "重复跑把唯一索引弄丢了"