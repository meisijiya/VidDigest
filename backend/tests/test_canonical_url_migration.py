"""`canonical_url` 的加列与回填（工单 #25 第一步 · 第 2 片）。

## 这一片做的是「加列 + 回填 + 写入侧填值」，**读侧一律不动**

所以除了「补上列了吗、回填了吗」，同样要紧的是**「读路径确实还没变」**——
否则这一片就顺手把第 4 片的活干了，而那三片各自独立才是切细的意义。
本文件最后一条判据专门钉这件事：不同形态的链接**仍然**互相查不到。

## 必须从真缺列的库起验

`db` 夹具每次 `init_db()` 造一个**全新**的库，于是「老库没有这一列、
靠迁移补上」那条路径一次都走不到。补列排错了顺序，全量绿，真库一升级就打不开
（本仓有过前车之鉴：`ON parse_history(is_favorite)` 的索引在 executescript 里、
补列迁移排在它后面，老库直接 abort）。所以升级测试全部用 `legacy_db`。

## 老库怎么造

不是手写一份「上一版的 DDL」——手写的会与真实 DDL 漂移。这里先
`init_db()` 拿到**现行** DDL，再把 `canonical_url` 那一行从里面摘掉，
DROP 重建。这样「老库」与「新库」的差别恰好只有这一列，
任何别的差异都会让测试在别处炸，而不是在这里静默通过。
"""
import pytest

import database
from url_canonical import canonical_video_url

#: 三张带 video_url 且要能跨形态对上号的表。
#:
#: 直接用生产那个常量，不在这里手抄第二份：加表的人只改一处才不会出现
#: 「一半真值」。⚠️ 但本文件守的是**正方向**（名单里的表**有**这一列）；
#: **反方向**（所有该有的表**都在**名单里）由 `test_canonical_url_coverage.py` 守。
#: 少了那边，删掉名单里一张表会让本文件安静地少覆盖一张而全绿。
CANONICAL_TABLES = database._CANONICAL_URL_TABLES

#: 同一个 B 站视频的三种形态，全部取自工单 #25 的实测矩阵。
BV = "https://www.bilibili.com/video/BV1aa411c7mD"
BILI_FORMS = [
    BV,
    "https://m.bilibili.com/video/BV1aa411c7mD",
    "https://www.bilibili.com/video/BV1aa411c7mD?spm_id_from=333.1007.tianma",
]
#: 短链：本工单明确不归一，回填后必须**原样**躺着。
SHORT_LINK = "https://b23.tv/AbCdEf"


# ── 造一个真缺列的老库 ────────────────────────────────────────

def _strip_column(ddl: str, column: str) -> str:
    lines = [ln for ln in ddl.split("\n") if column not in ln]
    out = "\n".join(lines)
    assert column not in out, f"没能从 DDL 里摘掉 {column} 列，造出来的还是新库"
    return out


def _seed_legacy_library():
    """造出「上一版」的结构：真实 DDL 减去 canonical_url，外加几行存量数据。

    返回造出来的 user_id。

    ⚠️ 这里**不能**回头 SELECT canonical_url 来自检：那正是老库缺的那一列。
    （第一版就是这么写的，于是 8 条升级判据全红，报错指向「迁移没生效」，
    真相是造库的 helper 自己查了它造不出来的列。）
    """
    database.init_db()  # 先要一份**真实**的现行 DDL，再从里面造老库
    with database.get_db() as c:
        uid = c.execute(
            "INSERT INTO users (email, password_hash) VALUES ('old@x.com', 'h')"
        ).lastrowid

        for table in CANONICAL_TABLES:
            row = c.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if row is None:
                pytest.skip(f"{table} 不在现行结构里，造不出老库")
            c.execute(f"DROP TABLE {table}")
            c.execute(_strip_column(row["sql"], "canonical_url"))

        # videos 只塞**一种**形态 + 一条短链：第 3 片之后 videos 上同 canonical 的
        # 行会被去重合并，形态收敛这件事改由 parse_history 那条断言守
        # （去重只作用于 videos，parse_history 是个人记录，本来就该多行）。
        c.executemany(
            "INSERT INTO videos (video_url, status) VALUES (?, 'ready')",
            [(BILI_FORMS[0],), (SHORT_LINK,)],
        )
        c.executemany(
            "INSERT INTO parse_history (user_id, video_url, video_title) VALUES (?, ?, '')",
            [(uid, u) for u in BILI_FORMS],
        )
        c.execute(
            "INSERT INTO chat_messages (user_id, video_url, role, content) "
            "VALUES (?, ?, 'user', '问过')",
            (uid, BILI_FORMS[1]),
        )
    return uid


def _columns(table: str) -> set[str]:
    with database.get_db() as c:
        return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def _rows(table: str, columns: str = "*") -> list[dict]:
    with database.get_db() as c:
        return [dict(r) for r in c.execute(f"SELECT {columns} FROM {table} ORDER BY id")]


def _indexes(table: str) -> dict[str, bool]:
    """{索引名: 是否唯一}。"""
    with database.get_db() as c:
        return {r["name"]: bool(r["unique"]) for r in c.execute(f"PRAGMA index_list({table})")}


# ── 老库升级 ─────────────────────────────────────────────────

class TestLegacyUpgrade:
    def test_legacy_library_really_lacks_the_column(self, legacy_db):
        """前提自检。不摆这一条，后面全绿也可能是因为「老库本来就是新库」。"""
        _seed_legacy_library()
        for table in CANONICAL_TABLES:
            assert "canonical_url" not in _columns(table), (
                f"{table} 上还有 canonical_url —— 这个老库是假的，"
                f"下面的升级判据全部无效"
            )

    def test_init_db_adds_the_column_to_all_three_tables(self, legacy_db):
        """三张表都补上。这一条与下面那条一起才说得清「补列」到底做了什么。"""
        _seed_legacy_library()
        database.init_db()
        for table in CANONICAL_TABLES:
            assert "canonical_url" in _columns(table), f"{table} 没补上 canonical_url"

    def test_init_db_does_not_explode_on_a_library_without_the_column(self, legacy_db):
        """索引排在补列之前的那条前车之鉴。

        老库里这一列压根不存在，所以**只要**有人把
        ``CREATE INDEX idx_videos_canonical`` 挪进 executescript（它跑在迁移之前），
        这一行就会抛 OperationalError —— 症状是「本地好好的，一升级就打不开」。
        所以本条不写断言：跑完不抛，就是它要守的东西。
        """
        _seed_legacy_library()
        database.init_db()  # 不抛即为通过

    def test_existing_rows_are_backfilled(self, legacy_db):
        """存量行按归一值回填，且 video_url 原文**一根汗毛都不动**。

        原文必须留着：它是回显的那一份（工单 #25 的第 2 问）。

        videos 上只塞了一种形态 + 一条短链——同 canonical 的多行会被第 3 片
        的去重合并掉，「三种形态收敛到同一个值」改由下面 parse_history 那条守。
        """
        _seed_legacy_library()
        database.init_db()

        got = _rows("videos")
        assert len(got) == 2, f"videos 应当剩下 B 站一条 + 短链一条，实得 {len(got)}"
        for row in got:
            assert row["canonical_url"] == canonical_video_url(row["video_url"]), row
            assert row["video_url"] in BILI_FORMS + [SHORT_LINK], "原文被改写了"

    def test_short_link_is_backfilled_verbatim(self, legacy_db):
        """短链原样回填。

        反向那一半：归一化解决不了短链（id 根本不在 URL 里），而「顺手猜一个
        规范值」会把不同的短链塌成同一个——于是库塌了且不报错。
        """
        _seed_legacy_library()
        database.init_db()
        row = next(r for r in _rows("videos") if r["video_url"] == SHORT_LINK)
        assert row["canonical_url"] == SHORT_LINK

    def test_parse_history_and_chat_messages_are_backfilled_too(self, legacy_db):
        """三张表都回填。漏掉哪张，哪张就会在第 4 片切 JOIN 时安静地对不上。

        「三种形态收敛到同一个规范值」也在这里守：videos 上同 canonical 的行
        会被第 3 片去重合并掉，而 parse_history 是**个人记录**，本来就该多行
        （用户两次粘不同形态就是两次记录，#6 的「只解析一次」说的是社区那一行）。
        """
        _seed_legacy_library()
        database.init_db()

        hist = _rows("parse_history")
        assert len(hist) == len(BILI_FORMS), f"个人记录不该被合并，实得 {len(hist)} 行"
        assert {r["canonical_url"] for r in hist} == {BV}, (
            f"同一个视频回填出了多个规范值："
            f"{ {r['canonical_url'] for r in hist} }"
        )
        assert all(r["canonical_url"] == canonical_video_url(r["video_url"]) for r in hist)

        chat = _rows("chat_messages")
        assert len(chat) == 1
        assert chat[0]["canonical_url"] == BV

    def test_running_init_db_twice_changes_nothing(self, legacy_db):
        """init_db 每次启动都跑。第二遍必须是无操作。"""
        _seed_legacy_library()
        database.init_db()
        first = _rows("videos")

        database.init_db()

        assert _rows("videos") == first, "重复跑 init_db 又动了数据"

    def test_the_index_exists_so_the_main_lookup_need_not_scan(self, legacy_db):
        """社区视频表**刻意不做条数裁剪**，所以主查询路径必须有索引。

        「唯一」不在这里断言：那是第 3 片（去重）落地之后的事，
        由 tests/test_canonical_url_dedupe.py 守着。本片只保证「不扫全表」。
        """
        _seed_legacy_library()
        database.init_db()
        assert "idx_videos_canonical" in _indexes("videos"), "索引没建：主查询路径要全表扫"


# ── 写入侧：新行自己填（不给「列存在但恒为空」留空间）────────────

class TestWritePaths:
    def test_reserve_video_fills_canonical_url(self, db):
        db.reserve_video(BILI_FORMS[1], 1)
        row = db.get_video_by_url(BILI_FORMS[1])
        assert row["canonical_url"] == BV

    def test_upsert_parse_history_fills_canonical_url(self, db, make_user):
        uid = make_user()
        db.upsert_parse_history(uid, BILI_FORMS[2], video_title="甲")
        rows = _rows("parse_history", "canonical_url, video_url")
        assert len(rows) == 1
        assert rows[0]["canonical_url"] == BV
        assert rows[0]["video_url"] == BILI_FORMS[2], "原文被改写了"

    def test_upsert_parse_history_fills_it_on_the_conflict_path_too(self, db, make_user):
        """冲突路径（同一个 user 同一个 url 再存一次）也得填。

        ON CONFLICT DO UPDATE 不动 canonical_url，所以值来自**第一次**插入。
        第一次是空的话…… 不可能：第一次也是这条 INSERT。钉住它是为了
        将来有人把 canonical_url 挪进 DO UPDATE SET 时不会漏掉另一条路。
        """
        uid = make_user()
        db.upsert_parse_history(uid, BILI_FORMS[1], video_title="甲")
        db.upsert_parse_history(uid, BILI_FORMS[1], summary_md="总结")
        rows = _rows("parse_history", "canonical_url")
        assert len(rows) == 1
        assert rows[0]["canonical_url"] == BV

    def test_append_chat_turn_fills_canonical_url(self, db, make_user):
        uid = make_user()
        db.append_chat_turn(uid, BILI_FORMS[1], "问", "答")
        rows = _rows("chat_messages", "canonical_url")
        assert len(rows) == 2, "一问一行，两行"
        assert {r["canonical_url"] for r in rows} == {BV}

    def test_no_row_is_left_with_an_empty_canonical_url(self, db, make_user):
        """全局兜底：三张表里不该有任何一行是空的。

        「列存在、有索引、查得到行，只是值全是空的」是这一片最坏的结果——
        它看起来完全正常。所以这里按表逐个查，而不是抽查一行。
        """
        uid = make_user()
        db.reserve_video(BILI_FORMS[0], uid)
        db.upsert_parse_history(uid, BILI_FORMS[1], video_title="甲")
        db.append_chat_turn(uid, BILI_FORMS[2], "问", "答")

        for table in CANONICAL_TABLES:
            with database.get_db() as c:
                blank = c.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE COALESCE(canonical_url, '') = ''"
                ).fetchone()["n"]
            assert blank == 0, f"{table} 里有 {blank} 行 canonical_url 是空的"


# ── 第 2 片刻意**不做**读侧切换 ─────────────────────────────────
#
# 这一片只加列 + 回填 + 写入侧填值，读出口仍旧按原文匹配。
# 切读侧是第 4 片的事，判据在 tests/test_canonical_url_lookup.py。
# 本片唯一与之相关的守卫在下面 `test_no_row_is_left_with_an_empty_canonical_url`：
# 列存在却没有值，是这一片最坏的结果。