"""老库升级路径：索引的列集合变了怎么办（ADR 0030）。

**为什么这条路径值得单独一个文件**：列集合不是能 `ALTER` 出来的东西。
FTS5 的外部内容表不支持加列，要加列只能 DROP 重建；而实测 `DROP TABLE`
**不会**带走建在它上面的三个触发器，留着就会在重建时报
`trigger videos_fts_ai already exists`。这条链路上任何一步漏掉，
症状都是「升级之后搜索整体失灵」或者「新加的列永远是空的」。

所以这里摆一份**真的老库**：索引只有 ADR 0030 之前的三列
（标题、标签、链接），触发器也是旧的三列版本，然后让现行代码去升级它。

断言的层次（从前提到结果）：
1. 前提自检——升级前它**确实**是老样子（老库没摆对，后面的断言全无意义）；
2. 升级后索引的列集合是新的五列，且三个触发器都被换成了五列版本；
3. 升级前搜不到的正文词，升级后搜得到（存量数据被 rebuild 灌进了索引）；
4. 升级前就搜得到的词，升级后**仍然**搜得到（升级不能弄坏原有能力）；
5. 重复升级不改变结果（幂等）。
"""
import pytest

import database


#: ADR 0030 之前的索引列集合：三列（标题、标签、链接）。
_OLD_INDEX_COLUMNS = ("video_title", "tags", "video_url")

#: ADR 0030 之前的索引表定义。逐字照抄旧版本，只是把列数缩回三列。
_OLD_INDEX_DDL = """
CREATE VIRTUAL TABLE videos_fts USING fts5(
    video_title, tags, video_url,
    content='videos', content_rowid='id', tokenize='trigram'
)
"""

#: ADR 0030 之前的三个触发器。逐字照抄旧版本（少两列）。
_OLD_TRIGGERS = """
CREATE TRIGGER videos_fts_ai AFTER INSERT ON videos BEGIN
    INSERT INTO videos_fts(rowid, video_title, tags, video_url)
    VALUES (new.id, new.video_title, new.tags, new.video_url);
END;
CREATE TRIGGER videos_fts_ad AFTER DELETE ON videos BEGIN
    INSERT INTO videos_fts(videos_fts, rowid, video_title, tags, video_url)
    VALUES ('delete', old.id, old.video_title, old.tags, old.video_url);
END;
CREATE TRIGGER videos_fts_au AFTER UPDATE ON videos BEGIN
    INSERT INTO videos_fts(videos_fts, rowid, video_title, tags, video_url)
    VALUES ('delete', old.id, old.video_title, old.tags, old.video_url);
    INSERT INTO videos_fts(rowid, video_title, tags, video_url)
    VALUES (new.id, new.video_title, new.tags, new.video_url);
END;
"""

#: 只出现在总结正文里的词。标题、标签、链接里都没有它。
_BODY_WORD = "量子纠缠"

#: 只出现在字幕里的词。
_SUBTITLE_WORD = "稀释制冷机"

#: 标题里的词——升级前就能搜到的那一类。
_TITLE_WORD = "碳纤维"


@pytest.fixture()
def old_index_db(db):
    """把现行库改造成「索引只有三列」的老库。

    ``db`` 夹具已经跑过一次现行 ``init_db()``，所以现行的五列索引与五列
    触发器都在。这里把它们拆掉，换成旧版本，再放一条内容齐全的 ready 行。
    """
    with database.get_db() as conn:
        conn.executescript(
            "DROP TRIGGER IF EXISTS videos_fts_ai;"
            "DROP TRIGGER IF EXISTS videos_fts_au;"
            "DROP TRIGGER IF EXISTS videos_fts_ad;"
        )
        conn.execute("DROP TABLE IF EXISTS videos_fts")
        conn.executescript(_OLD_INDEX_DDL)
        conn.executescript(_OLD_TRIGGERS)
        conn.execute(
            """INSERT INTO videos
               (video_url, status, video_title, tags, summary_md,
                mindmap_md, subtitle_text, parsed_by)
               VALUES (?, 'ready', ?, ?, ?, '{}', ?, NULL)""",
            (
                "https://example.com/v/old-index",
                f"标题里只有{_TITLE_WORD}这三个字",
                '["老标签"]',
                f"总结正文里写的是完全不同的东西：{_BODY_WORD}",
                f"字幕里提到了{_SUBTITLE_WORD}",
            ),
        )
        conn.execute("INSERT INTO videos_fts(videos_fts) VALUES('rebuild')")
    return db


def _index_columns(db):
    with db.get_db() as conn:
        return tuple(
            r["name"] for r in conn.execute("PRAGMA table_info(videos_fts)")
        )


def _trigger_sql(db):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master"
            " WHERE type = 'trigger' AND name LIKE 'videos_fts_%'"
        ).fetchall()
    return {r["name"]: r["sql"] for r in rows}


def _search(db, q):
    """走公开出口检索——索引坏掉时的症状正是搜不到。"""
    return database.search_community_videos(q=q)


class TestTheOldLibraryIsReallyOld:
    """前提自检。老库没摆对，后面的断言全都失去意义。"""

    def test_the_index_has_only_the_three_old_columns(self, old_index_db):
        cols = _index_columns(old_index_db)
        assert cols == _OLD_INDEX_COLUMNS, (
            f"老库的索引列是 {cols}，前提不成立：这不是一份三列的老索引"
        )

    def test_the_triggers_are_the_three_column_ones(self, old_index_db):
        triggers = _trigger_sql(old_index_db)
        assert set(triggers) == {"videos_fts_ai", "videos_fts_au", "videos_fts_ad"}
        for name, sql in triggers.items():
            assert "summary_md" not in sql, (
                f"触发器 {name} 里已经有 summary_md：老库摆得不够老"
            )

    def test_the_body_word_is_not_searchable_yet(self, old_index_db):
        """老库里正文词搜不到——这是这次升级要修的那个缺陷本身。"""
        assert _search(old_index_db, _BODY_WORD)["total"] == 0, (
            "正文词在老库里就搜得到：老库摆得不够老"
        )


class TestUpgradeRebuildsTheIndex:
    def test_init_db_replaces_the_index_with_the_five_column_one(self, old_index_db):
        database.init_db()
        cols = _index_columns(old_index_db)
        assert cols == database._VIDEO_SEARCH_COLUMNS, (
            f"升级后索引列是 {cols}，应为 {database._VIDEO_SEARCH_COLUMNS}"
        )

    def test_init_db_replaces_the_triggers_with_the_five_column_ones(self, old_index_db):
        database.init_db()
        triggers = _trigger_sql(old_index_db)
        assert set(triggers) == {"videos_fts_ai", "videos_fts_au", "videos_fts_ad"}, (
            f"升级后触发器是 {sorted(triggers)}，三个同步触发器必须都还在"
        )
        for name, sql in triggers.items():
            for col in database._VIDEO_SEARCH_COLUMNS:
                assert col in sql, (
                    f"触发器 {name} 的 SQL 里没有 {col}：这一列不会被同步进索引"
                )


class TestUpgradeKeepsTheLibrarySearchable:
    def test_the_body_word_becomes_searchable_after_the_upgrade(self, old_index_db):
        """存量行升级后立刻可搜——rebuild 会把内容表全量灌进索引。"""
        database.init_db()
        assert _search(old_index_db, _BODY_WORD)["total"] == 1, (
            "升级后正文词仍然搜不到：存量数据没被灌进新索引"
        )

    def test_the_subtitle_word_becomes_searchable_after_the_upgrade(self, old_index_db):
        database.init_db()
        assert _search(old_index_db, _SUBTITLE_WORD)["total"] == 1, (
            "升级后字幕里的词仍然搜不到：存量数据没被灌进新索引"
        )

    def test_the_title_word_still_works_after_the_upgrade(self, old_index_db):
        """升级不能弄坏**原来就管用**的能力。

        这条是升级路径的反向对照：只加列、不减能力。
        如果重建时把索引换成了一张空的或列不齐的表，这条立刻红。
        """
        database.init_db()
        assert _search(old_index_db, _TITLE_WORD)["total"] == 1, (
            "升级后标题词搜不到了：升级弄坏了原本就管用的能力"
        )


class TestUpgradeIsIdempotent:
    def test_running_init_db_twice_changes_nothing(self, old_index_db):
        database.init_db()
        first = _index_columns(old_index_db)
        database.init_db()
        assert _index_columns(old_index_db) == first
        assert _search(old_index_db, _BODY_WORD)["total"] == 1
        assert len(_trigger_sql(old_index_db)) == 3
