"""存量导图的写迁移（工单 #106 / #111 / ADR 0027、0029）。

## 为什么这条路径必须单独走一遍

`db` 夹具每次都造一张**全新**的表，存量行从来没在迁移面前出现过。而导图
退化恰恰发生在**写库那一刻**：老库里的导图是一条光杆（模型给的导图清洗
后只剩一级），而清洗只作用于**新**的总结。所以「新总结能出多层导图」
不解决存量——存量只能靠一条写迁移收拾。

## 迁移收拾两类行

* **退化的行**（只有一级标题）：按同一行的总结正文重建。
* **层级够但超上限的行**（同一父节点下超过 7 个子节点，或全图超过 30 个
  节点）：按当前规则截回上限之内——**不拿总结正文替换**，只丢超出的部分。

## 判据读的是库里的真实内容

迁移跑完之后那几行 `mindmap_md` 到底变成了什么。不断言迁移函数的调用
顺序，也不断言私有函数的返回值。

## 老库长什么样

`videos` 表用 `test_videos_legacy_upgrade.py` 里那份逐字抄来的老 DDL
（工单 #6 那个版本）：没有 `video_title` / `cover_url`，没有 `videos_fts`。
`parse_history` 用现行 DDL（它从没改过形状），只塞一行存量数据。
"""
import database
from test_mindmap_levels import heading_levels, max_children, total_nodes
from test_videos_legacy_upgrade import _LEGACY_VIDEOS_DDL, _LEGACY_VIDEOS_INDEX

URL_DEGENERATE = "https://example.com/degenerate"
URL_NO_SUMMARY = "https://example.com/no-summary"
URL_ALREADY_GOOD = "https://example.com/already-good"
URL_OVER_LIMIT = "https://example.com/over-limit"

#: 退化后的导图：只剩一根。
DEGENERATE_MD = "# 视频思维导图"

#: 已经有层级的导图：不在重建范围内。
GOOD_MD = "# 视频主题\n## 分支\n### 细"

#: 层级够、但同一父节点下挂了 9 个子节点（上限是 7）：超限的行。
OVER_LIMIT_MD = "# 视频主题\n" + "".join(f"## 分支{i}\n" for i in range(9))

#: 重建依据：同一行的总结正文里现成的标题结构。
SUMMARY_MD = (
    "# 视频主题\n"
    "## 视频概述\n"
    "- 本期实测三款扫地机器人\n"
    "## 吸力表现\n"
    "### 甲机地毯残留最少\n"
    "## 结论\n"
    "- 效果优先选甲机\n"
)


def _seed_legacy_library(db):
    """在 legacy_db 指向的库里摆出「真老库」：老 DDL + 三行存量数据。"""
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
               (video_url, status, summary_md, mindmap_md, tags, subtitle_text,
                parsed_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # 退化的导图 + 有正文：这条该被重建
                (URL_DEGENERATE, "ready", SUMMARY_MD, DEGENERATE_MD, '["数码测评"]',
                 "字幕", uid, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
                # 退化的导图 + 没有正文：没有重建依据，保持原样
                (URL_NO_SUMMARY, "ready", "", DEGENERATE_MD, '["数码测评"]',
                 "字幕", uid, "2026-01-02T00:00:00", "2026-01-02T00:00:00"),
                # 已经有层级：不在重建范围内，保持原样
                (URL_ALREADY_GOOD, "ready", SUMMARY_MD, GOOD_MD, '["数码测评"]',
                 "字幕", uid, "2026-01-03T00:00:00", "2026-01-03T00:00:00"),
                # 层级够但超上限（9 个二级分支，上限是 7）：该被截回上限之内
                (URL_OVER_LIMIT, "ready", SUMMARY_MD, OVER_LIMIT_MD, '["数码测评"]',
                 "字幕", uid, "2026-01-04T00:00:00", "2026-01-04T00:00:00"),
            ],
        )
        # 个人解析历史里同样躺着一份退化的导图
        c.execute(
            "INSERT INTO parse_history (user_id, video_url, summary_md, mindmap_md) "
            "VALUES (?, ?, ?, ?)",
            (uid, URL_DEGENERATE, SUMMARY_MD, DEGENERATE_MD),
        )


def _video_maps(db):
    with db.get_db() as c:
        return {
            r["video_url"]: r["mindmap_md"]
            for r in c.execute("SELECT video_url, mindmap_md FROM videos")
        }


def _history_maps(db):
    with db.get_db() as c:
        return [
            r["mindmap_md"]
            for r in c.execute("SELECT mindmap_md FROM parse_history ORDER BY id")
        ]


def test_the_stored_degenerate_map_is_rebuilt_on_upgrade(legacy_db):
    _seed_legacy_library(legacy_db)
    legacy_db.init_db()  # 老库启动：迁移在存量行面前跑一遍
    rebuilt = _video_maps(legacy_db)[URL_DEGENERATE]
    assert rebuilt != DEGENERATE_MD
    assert rebuilt.startswith("# 视频主题")
    assert "## 视频概述" in rebuilt
    assert max(heading_levels(rebuilt)) >= 2


def test_the_stored_degenerate_map_in_parse_history_is_rebuilt_too(legacy_db):
    _seed_legacy_library(legacy_db)
    legacy_db.init_db()
    rebuilt = _history_maps(legacy_db)[0]
    assert rebuilt != DEGENERATE_MD
    assert max(heading_levels(rebuilt)) >= 2


def test_a_row_without_a_summary_keeps_its_map(legacy_db):
    _seed_legacy_library(legacy_db)
    legacy_db.init_db()
    # 没有总结正文就没有重建依据：宁可留一根光杆，也不编一棵导图出来
    assert _video_maps(legacy_db)[URL_NO_SUMMARY] == DEGENERATE_MD


def test_a_row_that_already_has_levels_is_left_alone(legacy_db):
    _seed_legacy_library(legacy_db)
    legacy_db.init_db()
    assert _video_maps(legacy_db)[URL_ALREADY_GOOD] == GOOD_MD


def test_a_row_over_the_limits_is_truncated_on_upgrade(legacy_db):
    _seed_legacy_library(legacy_db)
    legacy_db.init_db()
    stored = _video_maps(legacy_db)[URL_OVER_LIMIT]
    # 超限的行被收拾回上限之内
    assert max_children(stored) <= 7
    assert total_nodes(stored) <= 30
    # 收拾掉的是「超出的那部分」，不是换成总结正文重建出来的另一棵
    assert "## 分支0" in stored
    assert "## 分支7" not in stored
    assert "## 视频概述" not in stored


def test_the_migration_is_idempotent(legacy_db):
    _seed_legacy_library(legacy_db)
    legacy_db.init_db()
    first = _video_maps(legacy_db)
    legacy_db.init_db()
    legacy_db.init_db()
    assert _video_maps(legacy_db) == first


def test_the_migration_keeps_the_row_count(legacy_db):
    _seed_legacy_library(legacy_db)
    legacy_db.init_db()
    with legacy_db.get_db() as c:
        assert c.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 4
        assert c.execute("SELECT COUNT(*) FROM parse_history").fetchone()[0] == 1


def test_a_fresh_library_rebuilds_a_degenerate_row_without_adding_rows(legacy_db):
    """全新库没有存量行要升级：迁移不凭空造行，带正文的退化行按它自己的正文重建。"""
    legacy_db.init_db()
    with legacy_db.get_db() as c:
        c.execute(
            "INSERT INTO videos (video_url, status, summary_md, mindmap_md) "
            "VALUES (?, ?, ?, ?)",
            (URL_DEGENERATE, "ready", SUMMARY_MD, DEGENERATE_MD),
        )
    legacy_db.init_db()
    # 这一行有正文，所以它**应该**被重建——重建依据是它自己的总结正文
    assert max(heading_levels(_video_maps(legacy_db)[URL_DEGENERATE])) >= 2
    with legacy_db.get_db() as c:
        assert c.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1
