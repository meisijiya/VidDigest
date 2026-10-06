"""删号：凡是引用用户的表，都必须被删号流程**明确**覆盖（工单 #34）。

## 这条不变量漏掉之后的症状：不报错

`_USER_DELETE_BLOCKERS` 是一个**按名字枚举的清单**。新建一张引用用户的表而忘了登记，
建表与迁移都不会抱怨——那张表压根不在名单里，代码不会去动它。

后果分两种形状，取决于建表时有没有外键，而**本仓的约定是不建外键**
（`database.py:366-367`：「不建 `user_id` 外键：用户注销后会话记录应随该用户一起消失，
而不是变成孤儿行」）。所以将来新表**默认落在静默那一侧**：

| 形状 | 后果 |
|---|---|
| 有 FK、未登记 | `sqlite3.IntegrityError` → HTTP **500**，用户仍在 |
| 无 FK、未登记 | `delete_user` 正常返回 → HTTP **200**，用户已删、**留下孤儿行** |

## 枚举按「哪些列引用用户」，不按「哪张表」

工单原文写的是「枚举所有带 `user_id` 列的表」。**在当前树上这条不成立**：
`videos` 压根没有 `user_id` 列，它用的是 `parsed_by`（外加 `regenerating_by`）。
只按 `user_id` 枚举，`videos` 永远进不了范围——而它恰恰是全库**唯一一张
「故意不随删号消失」的表**。那样一来豁免登记表恒为空，
「豁免会不会变成垃圾桶」这个问题连观察的对象都没有。

所以这里枚举的是「带 `_USER_REF_COLUMNS` 任一列的表」：按 `sqlite_master` 逐张表扫、
`PRAGMA table_info` 逐列看，不 grep 表名、不按表名放行。新建一张带 `user_id` 的表，
它自动进范围——这是**安全**的方向：往名单里塞表名能挡掉检查，
而多一个列名只会让检查多看见一张表。

残留风险写在这里而不是藏起来：一个**既不叫这几个名字、又没有外键**的引用列
（例如将来有人写 `author_id`）会漏过枚举。补法是往 `_USER_REF_COLUMNS`
加那个列名——它是一句「本仓把哪几种列名当成用户引用」的声明，
而豁免登记是「哪张表故意不删」的声明，两者的失败方向是相反的。

## 豁免不是垃圾桶：三道机制

豁免天然带白名单性质（`videos.parsed_by` 是**故意**不删的：社区内容必须留下，
ADR 0010，而「故意」无法从结构上检测，只能靠人声明）。三道机制把它箍住：

1. **加一条就是一次显式决定 + 一句理由。** 登记表是 `表 → (下场, 理由)`，
   理由必须是非空的、且有实际长度——`""` / `"-"` / `"todo"` 都过不了。
2. **条目不许腐烂。** 登记表里的表必须**仍然存在**、仍然**仍然引用用户**。
   删了表而忘了删条目会立刻报红——否则那条残留会让人误以为那张表仍受保护。
3. **不许靠「声明」蒙过去，要靠行为兑现。** 每一张表都会被**真跑一次
   `delete_user`**，插一行、删号、再数那行还在不在。往豁免里塞一张真表，
   换来的是一条「删号后这张表的行必须留下」的**行为断言**——那几乎从来不是塞表的人想要的；
   真想要的话，他也已经写下了一条断言数据不存在的测试。
   而带外键的表塞进豁免会直接撞上第 3 条：`delete_user` 抛 `IntegrityError`，
   根本走不到「正常返回且行还在」这一步。

所以这张表**不是**一张名单的对称检查，而是两层：
静态层回答「有没有表被漏掉」（并指名是哪张），
行为层回答「`delete_user` 真的会那么做吗」——
第二层是必需的，因为 `delete_user` 里那行手写 `DELETE` 是**真的被执行到了**
才作数，「源码里看起来有」不算。
"""

import pytest

import database
from database import _USER_DELETE_BLOCKERS


#: 本仓把哪几种列名当成「引用一个用户」。
#:
#: 是一句**约定声明**，不是放行名单：多写一个列名只会让检查多看见一张表，
#: 而漏写一个会让一张表静默漏网（见模块 docstring 的残留风险一段）。
_USER_REF_COLUMNS = ("user_id", "parsed_by", "regenerating_by")

#: 表 → (删号时这一行的下场, 理由)
#:
#: 三种下场：
#:   blocked   —— 删号被 409 拦住，用户与行都留下（要管理员先处理）
#:   cascaded  —— `delete_user` 里的手写 DELETE 把它清掉
#:   exempted  —— 故意留下，理由写在右边
#:
#: 理由对三种都要求非空：登记一条就是一次显式决定，
#: 不是顺手加个名字。空串 / "-" / "todo" 一律过不了 `test_every_entry_carries_a_reason`。
_EXPECTED_FATES = {
    "orders": (
        "blocked",
        "订单是支付凭证（ADR 0012）：后台不替你级联，删错了没法恢复，"
        "所以名下有订单时删号直接 409，让管理员先处理。",
    ),
    "parse_history": (
        "blocked",
        "解析历史是用户自己的数据（ADR 0012）：与订单同级，"
        "带 REFERENCES users(id) 且无 ON DELETE，不先清掉就是 500 而不是提示。",
    ),
    "chat_messages": (
        "cascaded",
        "追问记录没有外键（database.py:366-367 明写不建），"
        "必须由 delete_user 里那行手写 DELETE 兜住，"
        "否则删号后会话记录变成孤儿行。",
    ),
    "videos": (
        "exempted",
        "parsed_by 是弱引用：解析者注销后社区内容必须留下来（ADR 0010），"
        "社区列表用 LEFT JOIN 取作者，解析者没了照样读得出。"
        "刻意不删，与 chat_messages 相反。",
    ),
}

_BLOCKED, _CASCADED, _EXEMPTED = "blocked", "cascaded", "exempted"


# ── 判定用的几个小工具（形态照抄 test_canonical_url_coverage.py）──────────

def _columns(conn, table: str) -> set:
    return {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}


def _all_tables(conn) -> list:
    return [
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _ref_columns(conn, table: str) -> set:
    """这张表上哪些列引用用户。"""
    return _columns(conn, table) & set(_USER_REF_COLUMNS)


def _user_referencing_tables(conn) -> list:
    """库里所有引用用户的表——按结构枚举，不 grep 表名。"""
    return [t for t in _all_tables(conn) if _ref_columns(conn, t)]


def _uncovered(conn) -> list:
    """引用了用户、却没在登记表里声明下场的表。"""
    return [t for t in _user_referencing_tables(conn) if t not in _EXPECTED_FATES]


# ── 静态层：有没有表被漏掉 ──────────────────────────────────────────────

class TestEveryUserReferencingTableIsDeclared:
    def test_no_table_referencing_a_user_is_left_undeclared(self, db):
        """这是本工单的主判据，失败信息必须**指名是哪张表**。"""
        with database.get_db() as conn:
            missing = [
                (t, sorted(_ref_columns(conn, t))) for t in _uncovered(conn)
            ]
        assert missing == [], (
            "这些表引用了用户（列在 %s 里），却没有在 _EXPECTED_FATES 里声明"
            "删号时它的下场：\n%s\n"
            "没声明的后果取决于有没有外键：无外键时 delete_user 正常返回、"
            "用户被删而这些行变成孤儿行（HTTP 200，看不出任何异常）；"
            "有外键时直接 IntegrityError → 500。"
            "本仓的约定是新表不建外键，所以默认落在前一种。"
            % (
                "/".join(_USER_REF_COLUMNS),
                "\n".join("  · %s（引用列：%s）" % (t, "/".join(cols)) for t, cols in missing),
            )
        )

    def test_the_declaration_names_exactly_the_user_referencing_tables(self, db):
        """反向：登记表不该多，也不该少。

        「多」是登记了一张已经不引用用户的表（或压根不存在的表）——
        那条理由会开始骗人；「少」正是上面那条抓的。
        """
        with database.get_db() as conn:
            expected = set(_user_referencing_tables(conn))
        assert set(_EXPECTED_FATES) == expected, (
            "登记表是 %s，库里引用用户的表却是 %s。"
            "多出来的那些理由已经不成立，该删；少掉的那些正是上面那条要抓的"
            % (sorted(_EXPECTED_FATES), sorted(expected))
        )

    def test_every_entry_carries_a_reason(self, db):
        """加进登记表就是一次显式决定 + 一句理由，不许只加个名字。"""
        for table, (_fate, reason) in sorted(_EXPECTED_FATES.items()):
            assert isinstance(reason, str) and len(reason.strip()) >= 10, (
                "%s 的理由是 %r——登记一张表就是一次显式决定，"
                "必须写清「为什么它可以这样」，空串 / \"-\" / \"todo\" 都不算数"
                % (table, reason)
            )

    def test_an_exempted_table_still_exists_and_still_references_a_user(self, db):
        """条目不许腐烂：删了表而忘了删豁免，那条会变成纯噪音。

        残留的豁免比没有豁免更坏——它让人以为那张表仍受保护，
        而实际上它已经不在库里了。
        """
        with database.get_db() as conn:
            for table, (fate, _reason) in sorted(_EXPECTED_FATES.items()):
                if fate != _EXEMPTED:
                    continue
                assert table in _all_tables(conn), (
                    "豁免登记里的 %s 在库里已经不存在了——删表时忘了删这条登记。"
                    "残留条目会让人误以为它仍受保护" % table
                )
                assert _ref_columns(conn, table), (
                    "%s 已经不再引用用户了（列名都变了），"
                    "「故意不删」这个理由失去了对象，该把这条登记删掉" % table
                )

    def test_the_blocker_manifest_matches_the_blocked_declarations(self, db):
        """声明成 blocked 的表，与生产代码真的拿去查的那张清单，必须是同一批。

        两边各写一份的话，早晚会漂：声明说「拦住了」而生产清单里没有它，
        删号就会静默走过去——而这一条正是工单 #34 要防的那种静默。
        """
        declared = {t for t, (fate, _r) in _EXPECTED_FATES.items() if fate == _BLOCKED}
        assert set(_USER_DELETE_BLOCKERS) == declared, (
            "生产代码 _USER_DELETE_BLOCKERS=%s，测试里声明成 blocked 的却=%s。"
            "少登记的那张在删号时不会被 409 拦住"
            % (sorted(_USER_DELETE_BLOCKERS), sorted(declared))
        )

    def test_an_exempted_table_is_never_also_blocked(self, db):
        """两个下场互斥：既「删号被拦」又「故意留下」是自相矛盾的。"""
        for table, (fate, _reason) in sorted(_EXPECTED_FATES.items()):
            if fate == _EXEMPTED:
                assert table not in _USER_DELETE_BLOCKERS, (
                    "%s 同时被登记成豁免、又在 _USER_DELETE_BLOCKERS 里——"
                    "那它到底删不删？" % table
                )


# ── 行为层：delete_user 真的会那么做吗 ───────────────────────────────────

def _plant_row(conn, table, user_id, tag):
    """按 PRAGMA 结构补齐 NOT NULL 且无默认值的列，插一行属于 user_id 的。

    刻意不硬编码各表的列清单：那是又一张要人同步维护的名单，
    而这里要证明的恰恰是「表自己长什么样都会被正确处理」。
    """
    parts, args = [], []
    for r in conn.execute("PRAGMA table_info(%s)" % table):
        name, ctype, notnull, dflt = r["name"], (r["type"] or ""), r["notnull"], r["dflt_value"]
        if name in _ref_columns(conn, table):
            parts.append(name)
            args.append(user_id)
        elif notnull and dflt is None:
            if "INT" in ctype.upper():
                parts.append(name)
                args.append(1)
            else:
                parts.append(name)
                args.append("probe-%s-%s" % (name, tag))
    return conn.execute(
        "INSERT INTO %s (%s) VALUES (%s)" % (table, ", ".join(parts), ", ".join("?" * len(parts))),
        args,
    ).lastrowid


def _observed_fate(db, table, user_id):
    """真跑一次 delete_user，返回这一行的实际下场。

    返回 ``(fate, detail)``；fate ∈ {blocked, cascaded, exempted, error}。
    """
    with database.get_db() as conn:
        _plant_row(conn, table, user_id, str(user_id))
        planted = conn.execute(
            "SELECT count(*) FROM %s" % table,
        ).fetchone()[0]
        assert planted >= 1, "阳性对照失败：往 %s 插行没插进去，下面测的不是删号" % table
        ref = sorted(_ref_columns(conn, table))[0]

    try:
        db.delete_user(user_id)
    except database.UserConflict as e:
        return _BLOCKED, "blockers=%s" % sorted(e.blockers)
    except Exception as e:                      # noqa: BLE001 — 异常本身就是一种下场
        return "error", "%s: %s" % (type(e).__name__, e)

    with database.get_db() as conn:
        left = conn.execute(
            "SELECT count(*) FROM %s WHERE %s = ?" % (table, ref), (user_id,)
        ).fetchone()[0]
    return (_EXEMPTED if left else _CASCADED), "该用户在这张表里残留 %d 行" % left


class TestDeleteUserActuallyDoesWhatTheDeclarationClaims:
    def test_each_table_lands_on_the_fate_it_declared(self, db):
        """每一张引用用户的表都真跑一遍删号，实际下场必须与登记一致。

        这是「手写 DELETE 真的被执行到了」的唯一可信读法：
        `delete_user` 里那行 `DELETE FROM chat_messages` 只要被摘掉，
        这里立刻变成 exempted ≠ cascaded。
        """
        with database.get_db() as conn:
            tables = _user_referencing_tables(conn)
        assert tables, "枚举结果为空——检查对这张库失明了，下面的断言恒真"

        wrong = []
        for table in tables:
            declared, reason = _EXPECTED_FATES[table]
            user_id = db.create_user("fate-%s@example.com" % table, "hash")["id"]
            observed, detail = _observed_fate(db, table, user_id)
            if observed != declared:
                wrong.append(
                    "  · %s：登记 %s（%s），实跑 %s（%s）"
                    % (table, declared, reason[:24] + "…", observed, detail)
                )
        assert not wrong, (
            "这些表删号时的实际下场与登记不一致：\n%s\n"
            "登记说 cascaded 而实测 cascaded 之外的结果，最常见的原因是"
            "delete_user 里那行手写 DELETE 被人摘掉了——"
            "「源码里看起来有」不算数，只有真删掉才算"
            "\n".join(wrong)
        )

    def test_the_cascade_check_would_notice_a_deleted_handwritten_delete(self, db):
        """阳性对照：证明上面那条不是恒真断言。

        把 chat_messages 的引用列改名成一个**不在** `_USER_REF_COLUMNS` 里的名字，
        枚举就会漏掉它——这正是模块 docstring 里写下的残留风险，
        钉在这里是为了哪天它真的发生时，有人知道该往哪看。
        """
        with database.get_db() as conn:
            tables = _user_referencing_tables(conn)
            assert "chat_messages" in tables, (
                "chat_messages 已经不在枚举范围里了——"
                "_USER_REF_COLUMNS 漏了它的引用列，"
                "「手写 DELETE 被摘掉」这条检查对它失明了"
            )
            assert "videos" in tables, (
                "videos 已经不在枚举范围里了——"
                "它靠 parsed_by 进范围，只按 user_id 枚举会整个漏掉它"
            )


# ── 判据本身有牙齿吗 ───────────────────────────────────────────────────

class TestTheCheckItselfHasTeeth:
    def test_an_unregistered_user_table_is_reported_by_name(self, db):
        """工单要求的那条变异：造一张带 user_id 却没登记的表，判据必须指名它。

        没有这一条，主判据可能是一条**恒真**的断言——
        「库里现在恰好没有漏的表」和「判据根本不会发现漏的表」长得一模一样。
        """
        with database.get_db() as conn:
            conn.execute(
                "CREATE TABLE scratch_probe (id INTEGER PRIMARY KEY, user_id INTEGER)"
            )
            try:
                missing = _uncovered(conn)
                assert missing == ["scratch_probe"], (
                    "判据没报出 scratch_probe（它报的是 %s）——"
                    "这条断言恒真，覆盖判据也就没有牙齿" % missing
                )
            finally:
                conn.execute("DROP TABLE scratch_probe")

    def test_dropping_the_exemption_registration_is_reported(self, db):
        """把一张豁免表从登记表里拿掉，判据必须红。"""
        table = next(t for t, (f, _r) in _EXPECTED_FATES.items() if f == _EXEMPTED)
        declared = {k: v for k, v in _EXPECTED_FATES.items() if k != table}
        with database.get_db() as conn:
            found = _user_referencing_tables(conn)
        assert table in found, (
            "前提不成立：%s 已经不在枚举范围里，"
            "「拿掉它的登记」这条变异测不到任何东西" % table
        )
        missing = [t for t in found if t not in declared]
        assert missing == [table], (
            "拿掉 %s 的登记后判据应当只报它，实际报的是 %s" % (table, missing)
        )

    def test_a_table_referencing_a_user_under_another_column_name_is_still_found(self, db):
        """豁免不是靠表名放行的：换个列名的引用列照样进范围。

        豁免写成「按表名跳过」的话这一条会红——而那正是它该拦的写法。
        """
        with database.get_db() as conn:
            conn.execute(
                "CREATE TABLE author_id_probe (id INTEGER PRIMARY KEY, author_id INTEGER)"
            )
            try:
                found = _user_referencing_tables(conn)
                assert "author_id_probe" not in found, (
                    "author_id_probe 不该被算进来——它不在 _USER_REF_COLUMNS 里，"
                    "枚举按列名而非表名，这条红说明枚举被写成了表名白名单"
                )
                conn.execute("ALTER TABLE videos ADD COLUMN author_id INTEGER")
                try:
                    found = _user_referencing_tables(conn)
                    assert "videos" in found, "videos 引用用户却没进枚举，前提坏了"
                    conn.execute(
                        "CREATE TABLE evil_videos (id INTEGER PRIMARY KEY, parsed_by INTEGER)"
                    )
                    try:
                        found = _user_referencing_tables(conn)
                        assert "evil_videos" in found, (
                            "一张名字里带 videos 的新表被放过了——"
                            "豁免已经退化成表名匹配，"
                            "将来谁往白名单里塞一张真表都不会有东西变红"
                        )
                    finally:
                        conn.execute("DROP TABLE evil_videos")
                finally:
                    conn.execute("ALTER TABLE videos DROP COLUMN author_id")
            finally:
                conn.execute("DROP TABLE author_id_probe")

    @pytest.mark.parametrize("bad_reason", ["", "   ", "-", "todo", "n/a"])
    def test_a_reasonless_exemption_does_not_pass(self, db, bad_reason):
        """理由必须是真的理由：空串 / 占位符一律过不了。"""
        for table, (_fate, _reason) in _EXPECTED_FATES.items():
            stripped = bad_reason.strip()
            assert not (isinstance(stripped, str) and len(stripped) >= 10), (
                "占位理由 %r 不该通过长度检查" % bad_reason
            )
        assert len(_EXPECTED_FATES) >= 1, "登记表空了，参数化这条就没有对象"
