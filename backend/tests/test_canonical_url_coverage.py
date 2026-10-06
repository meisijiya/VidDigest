"""凡是带 `video_url` 的表，都必须有 `canonical_url`（工单 #31）。

## 这条不变量漏掉之后的症状：不报错

新建一张带 `video_url` 的表，忘了加进 `_CANONICAL_URL_TABLES`——
建表与迁移都不会抱怨，因为那张表压根不在名单里，代码不会去动它。

于是那张表上永远没有 `canonical_url`，任何按 canonical 的查询
（`WHERE canonical_url = ?`、`LEFT JOIN … ON v.canonical_url = h.canonical_url`）
对它一律匹配不到行。症状是**「换个形态打开同一个视频，服务端说没解析过」**，
而请求 200、日志无异常、门禁全绿。

## 豁免按「是不是虚拟表」判定，不按表名

`videos_fts` 有 `video_url`（关键词检索要匹配它）却没有 `canonical_url`。
它**不需要**：按 URL 精确查找走 `WHERE canonical_url = ?`，根本不经过 FTS。

但这条豁免如果写成表名白名单，就会变成垃圾桶——将来谁加一张真表进去，
判据被放松了而没有任何东西会红。所以豁免按**结构**判定：
`sqlite_master.sql` 里带 `VIRTUAL TABLE` 的才是虚拟表。
配套断言 `test_the_fts_exemption_is_structural_not_a_name_pattern` 专门守这一点。

## 与 `test_canonical_url_migration.py` 的分工

那边守的是「名单里的表**有**这一列」。这边守的是它的**反面**：
「所有该有的表**都在**名单里」。两边只差几个字，守的却是完全不同的东西——
只写一边就有一半真值。
"""

import database
from database import _CANONICAL_URL_TABLES


# ── 判定用的几个小工具 ──────────────────────────────────────

def _ddl(conn, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return (row["sql"] or "") if row else ""


def _is_virtual_table(conn, table: str) -> bool:
    """结构判定：虚拟表（FTS 索引）由 SQLite 自己标出来，不靠表名。"""
    return "VIRTUAL TABLE" in _ddl(conn, table).upper()


def _columns(conn, table: str) -> set:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _tables_with(conn, column: str) -> list:
    """库里带某一列的**所有**表。

    按 `sqlite_master` 逐张表扫，不 grep 表名、不靠印象——
    新建的表这一轮就在名单里，不必记得改别处的清单。
    """
    names = [
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return [n for n in names if column in _columns(conn, n)]


def _missing_canonical(conn) -> list:
    """带 `video_url` 却没有 `canonical_url`、且**不是虚拟表**的表。"""
    return [
        name for name in _tables_with(conn, "video_url")
        if "canonical_url" not in _columns(conn, name)
        and not _is_virtual_table(conn, name)
    ]


# ── 覆盖判据 ──────────────────────────────────────────────

class TestEveryAddressableTableIsCovered:
    def test_no_table_with_video_url_is_left_without_the_column(self, db):
        with database.get_db() as conn:
            missing = _missing_canonical(conn)
        assert missing == [], (
            "这些表带 video_url 却不在 _CANONICAL_URL_TABLES 里，"
            "于是它们永远没有 canonical_url，按 canonical 的查询对它们一律匹配不到行：\n"
            + "\n".join(f"  · {t}" for t in missing)
            + "\n症状是「换个形态打开同一个视频，说没解析过」，而不报错。"
        )

    def test_the_manifest_names_exactly_the_addressable_tables(self, db):
        """反向：名单不该多，也不该少。

        「多」是加了一张不该管的表；「少」正是上面那条抓的。
        两条合起来，名单与结构就锁死了。
        """
        with database.get_db() as conn:
            expected = {
                n for n in _tables_with(conn, "video_url")
                if not _is_virtual_table(conn, n)
            }
        assert set(_CANONICAL_URL_TABLES) == expected, (
            f"名单是 {sorted(_CANONICAL_URL_TABLES)}，"
            f"库里该有的却是 {sorted(expected)}"
        )


# ── 判据本身有牙齿吗 ──────────────────────────────────────

class TestTheCheckItselfHasTeeth:
    def test_it_reports_a_table_that_lacks_the_column(self, db):
        """阳性对照：造一张只有 video_url 的普通表，判据必须报出来。

        没有这一条，上面那条可能是一条**恒真**的断言——
        「库里现在恰好没有漏的表」和「判据根本不会发现漏的表」长得一模一样。
        """
        with database.get_db() as conn:
            conn.execute(
                "CREATE TABLE scratch_probe (id INTEGER PRIMARY KEY, video_url TEXT)"
            )
            try:
                missing = _missing_canonical(conn)
                assert "scratch_probe" in missing, (
                    f"判据没报出缺列的表（它报的是 {missing}）——"
                    f"这条断言恒真，覆盖判据也就没有牙齿"
                )
            finally:
                conn.execute("DROP TABLE scratch_probe")

    def test_the_fts_exemption_is_structural_not_a_name_pattern(self, db):
        """豁免按「是不是虚拟表」判定，所以名字带 `_fts` 的**普通表**照样被报出来。

        豁免写成表名白名单的话，这一条会红——而那正是它该拦的写法。
        """
        with database.get_db() as conn:
            conn.execute(
                "CREATE TABLE evil_fts (id INTEGER PRIMARY KEY, video_url TEXT)"
            )
            try:
                missing = _missing_canonical(conn)
                assert "evil_fts" in missing, (
                    "一张名字里带 _fts 的普通表被豁免了——"
                    "豁免已经退化成表名匹配，将来谁往白名单里塞一张真表都不会有东西变红"
                )
            finally:
                conn.execute("DROP TABLE evil_fts")

    def test_the_real_fts_table_really_is_virtual(self, db):
        """前提自检：豁免**当前**是有理由的。

        哪天 `videos_fts` 变成普通表了，上面那组豁免就失去依据，
        此刻这条会红，提醒人重新看一眼那条理由还成不成立。
        """
        with database.get_db() as conn:
            assert "videos_fts" in _tables_with(conn, "video_url"), (
                "videos_fts 上没有 video_url 了——"
                "本文件关于「FTS 表为什么可以豁免」的理由要重新审视"
            )
            assert _is_virtual_table(conn, "videos_fts"), (
                "videos_fts 现在不是虚拟表了，豁免它不再有结构依据"
            )
            assert "videos_fts" not in _missing_canonical(conn)