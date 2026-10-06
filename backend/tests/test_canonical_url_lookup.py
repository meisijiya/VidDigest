"""查询侧切到 canonical 之后的行为（工单 #25 第一步 · 第 4 片）。

## 这一片第一次改变对外行为

前三片只加列、只回填、只去重——读出口一律按**原文**匹配。
这一片把**比较**全部换成 canonical，而**回显**仍然是 `video_url` 原文
（工单 #25 的第 2 问：回显哪个版本）。

## 判据按「谁在读」枚举，不按「数据在哪张表」枚举

新表带 canonical 不等于读出口用上了。这一片覆盖七类读出口：
`get_video_by_url`（社区复用判断的地基）、`probe_video`、`reserve_video`
的冲突与接管分支、`complete_video` / `release_video` / 覆盖写、
覆盖闸门的抢与放、社区搜索与历史页搜索的 URL 精确模式、以及三处 JOIN。

**漏掉任何一处的症状都是「静默」的**：那一路仍然按原文比，于是用户换个形态
打开同一个视频，又被当成没解析过。
"""
import pytest

import database
from url_canonical import canonical_video_url

BV = "https://www.bilibili.com/video/BV1aa411c7mD"
FORMS = [
    BV,
    "https://bilibili.com/video/BV1aa411c7mD",
    "https://m.bilibili.com/video/BV1aa411c7mD",
    "https://www.bilibili.com/video/BV1aa411c7mD?spm_id_from=333.1007.tianma",
    "https://www.bilibili.com/video/BV1aa411c7mD/",
    "https://www.bilibili.com/video/BV1aa411c7mD?",
]
OTHER = "https://www.bilibili.com/video/BV1zz411c7mX"
SHORT_A = "https://b23.tv/AbCdEf"
SHORT_B = "https://b23.tv/ZzYyXx"


# ── 存在性判断：地基 ─────────────────────────────────────────

class TestLookupAcrossForms:
    def test_every_form_finds_the_video_stored_under_a_different_form(self, db):
        """这是整件事的目的：解析过一次之后，换个形态打开**仍然**认得出。

        逐条断言只能证明「各自找得到」，所以后面那条 set 基数才是结论。
        """
        db.reserve_video(FORMS[1], 1)  # 存的是「无 www」那一种
        for form in FORMS:
            assert db.get_video_by_url(form) is not None, (
                f"{form} 认不出库里那一条 —— 用户换个形态打开就又被当成没解析过"
            )

    def test_six_forms_all_hit_the_same_row(self, db):
        """结论不是「每条各自对」，而是「它们指向同一行」。"""
        db.reserve_video(FORMS[1], 1)
        ids = {db.get_video_by_url(f)["id"] for f in FORMS}
        assert len(ids) == 1, f"六种形态落到了 {len(ids)} 行上：{ids}"

    def test_a_different_video_still_is_not_found(self, db):
        """反向那一半。少了它，「归一」也可能是「全都返回同一行」。

        而那种实现不报错，只是「点哪个视频都打开同一条」。
        """
        db.reserve_video(FORMS[0], 1)
        assert db.get_video_by_url(OTHER) is None

    def test_probe_video_reports_ready_for_every_form(self, db):
        """probe_video 是等待者轮询用的只读问询（工单 #19 第 1 项）。

        它与 get_video_by_url 走同一条路，但**独立判据**：等待者拿到
        「ready」就复用、拿到「claimable」就自己上。这里错了的后果是
        两个用户同时去调模型、同时扣额度。
        """
        assert db.reserve_video(FORMS[1], 1)[0] == "reserved"
        db.complete_video(FORMS[1], summary_md="总结")
        for form in FORMS:
            assert db.probe_video(form) == "ready", (
                f"{form} 上等不到 ready —— 等待者会当成没人占着而重复解析"
            )

    def test_reserving_a_second_form_does_not_create_a_second_row(self, db):
        """唯一索引是最后一道，但**能不能走到它**取决于读出口改没改。"""
        db.reserve_video(FORMS[0], 1)
        outcome, row = db.reserve_video(FORMS[2], 2)
        assert outcome in ("pending", "ready"), f"换了个形态就抢占成功了：{outcome}"
        with database.get_db() as c:
            assert c.execute("SELECT count(*) AS n FROM videos").fetchone()["n"] == 1


# ── 短链不受影响 ─────────────────────────────────────────────

class TestShortLinksUnaffected:
    def test_two_short_links_are_still_two_videos(self, db):
        """归一解决不了短链（id 不在 URL 里），所以它们维持原样。

        而「维持原样」不是碰巧：canonical 就是它自己，
        于是精确比较对短链仍然有效，且不会把不同短链并成一条。
        """
        db.reserve_video(SHORT_A, 1)
        db.reserve_video(SHORT_B, 2)
        assert db.get_video_by_url(SHORT_A)["id"] != db.get_video_by_url(SHORT_B)["id"]

    def test_a_short_link_still_finds_its_own_row(self, db):
        db.reserve_video(SHORT_A, 1)
        assert db.get_video_by_url(SHORT_A) is not None


# ── 写路径跟着改，否则会出现「改了一半」 ───────────────────────

class TestWritePathsFollowTheLookup:
    def test_complete_video_writes_through_a_different_form(self, db):
        """complete_video 用的是另一个形态，但它**必须**写进同一行。

        漏改的症状：reserve 说「reserved」了，complete 却匹配 0 行，
        那一行永远停在 pending，后来的每个用户都只能干等到超时。
        """
        db.reserve_video(FORMS[1], 1)
        assert db.complete_video(FORMS[4], summary_md="总结") == 1, (
            "换个形态写不进去 —— 那一行会永远停在 pending"
        )
        assert db.get_video_by_url(FORMS[0])["status"] == database.VIDEO_STATUS_READY

    def test_release_video_works_through_a_different_form(self, db):
        db.reserve_video(FORMS[1], 1)
        assert db.release_video(FORMS[3], 1) == 1, (
            "换形态就还不了位 —— 占位会永远停着，那个链接再也解析不了"
        )

    def test_publish_video_card_writes_through_a_different_form(self, db):
        """卡片标题与封面是「先到先得」，用别的形态回填也要能写进去。"""
        db.reserve_video(FORMS[1], 1)
        db.complete_video(FORMS[1], summary_md="总结")
        assert db.publish_video_card(FORMS[2], "标题", "https://img/a.jpg") == 1, (
            "换形态就回填不进卡片 —— 社区卡片会一直显示「未命名视频」"
        )

    def test_the_regenerate_gate_is_acquired_through_a_different_form(self, db):
        """覆盖闸门（工单 #20）是防「两人同时覆盖同一份总结」的那道锁。

        漏改的后果不是慢，是**静默的双花**：两个人都拿到闸、都调模型、都扣额度。
        """
        db.reserve_video(FORMS[1], 1)
        db.complete_video(FORMS[1], summary_md="总结")
        assert db.acquire_regenerate_gate(FORMS[3], 1) is True
        assert db.acquire_regenerate_gate(FORMS[3], 2) is False, (
            "换了个用户/形态就抢到了第二把闸 —— 闸门形同虚设"
        )
        db.release_regenerate_gate(FORMS[4], 1)


# ── 回显仍然是原文 ───────────────────────────────────────────

class TestEchoIsStillTheOriginal:
    def test_the_stored_video_url_is_untouched_by_lookups(self, db):
        """查的时候用 canonical，**存**的时候不能顺手把原文改成规范值。

        工单 #25 第 2 问：回显哪个版本。这里钉的是「存进去什么样就回显什么样」。
        """
        db.reserve_video(FORMS[2], 1)
        row = db.get_video_by_url(FORMS[0])
        assert row["video_url"] == FORMS[2], "原文被改写成规范值了"
        assert row["canonical_url"] == BV

    def test_community_card_echoes_the_original(self, db):
        db.reserve_video(FORMS[3], 1)
        db.complete_video(FORMS[3], summary_md="总结")
        page = database.search_community_videos(FORMS[3])
        assert page["items"][0]["video_url"] == FORMS[3], (
            "社区卡片回显的不是用户当初分享的那个地址"
        )


# ── 搜索与 JOIN ──────────────────────────────────────────────

class TestSearchAndJoin:
    def test_community_url_search_finds_the_row_from_another_form(self, db):
        db.reserve_video(FORMS[1], 1)
        db.complete_video(FORMS[1], summary_md="总结")
        assert database.search_community_videos(FORMS[3])["total"] == 1

    def test_history_row_joins_community_row_across_forms(self, db, make_user):
        """这一条最容易被漏掉，而漏掉的症状极不像 bug。

        历史页的列表是 ``parse_history h LEFT JOIN videos v``，按原文 JOIN 的话，
        用户粘 ``m.bilibili.com/...``、社区里存的是 ``www.bilibili.com/...``，
        于是**标签列变空**、社区侧内容一点都接不上，列表看着只是「标签没了」。

        判据只用 JOIN 派生的那一列（``tags``）：``has_ai_result`` 不行——
        它由 ``_HISTORY_HAS_AI_ALIASED_H`` 从 **parse_history 自己的**
        summary_md / chat_history 算出来，跟这条 JOIN 无关，用它断言会恒真。
        """
        uid = make_user()
        db.reserve_video(FORMS[1], 1)
        db.complete_video(FORMS[1], summary_md="总结", tags=["科普"])
        db.upsert_parse_history(uid, FORMS[3], video_title="甲")

        page = database.list_parse_histories(uid)
        assert len(page["items"]) == 1
        row = page["items"][0]
        assert row["tags"] == ["科普"], (
            "JOIN 没接上：社区那一行的标签没过来（而用户看到的只是「标签没了」）"
        )

    def test_history_url_search_finds_a_row_stored_in_another_form(self, db, make_user):
        uid = make_user()
        db.upsert_parse_history(uid, FORMS[1], video_title="甲")
        assert database.list_parse_histories(uid, q=FORMS[4])["total"] == 1

    def test_chat_history_is_found_across_forms(self, db, make_user):
        """追问记录按原文比对的话，换个形态存的一问一答就对不上。

        症状是「历史里明明问过，AI 却完全不记得」——而历史列表看起来一切正常。
        """
        uid = make_user()
        db.append_chat_turn(uid, FORMS[1], "问", "答")
        db.upsert_parse_history(uid, FORMS[3], video_title="甲")
        assert database.get_chat_session(uid, FORMS[5]), "换个形态就找不到问答记录了"

    def test_the_legacy_chat_column_is_found_across_forms(self, db, make_user):
        """老数据的回退读口也要按 canonical 找。

        这一条与上面那条是不同的坏法：上面那条走的是新表（chat_messages），
        只要新表命中就轮不到旧列，所以把 ``get_chat_session`` 里那句退回
        按原文比较，它照样绿。必须专门造一个「新表为空、只有旧列」的库。
        """
        uid = make_user()
        db.upsert_parse_history(uid, FORMS[3], video_title="甲")
        with database.get_db() as c:
            c.execute(
                "UPDATE parse_history SET chat_history = ? WHERE user_id = ?",
                ('[{"question": "问过", "answer": "答过"}]', uid),
            )

        turns = database.get_chat_session(uid, FORMS[5])
        assert turns, "换个形态就读不到旧列里的问答记录了 —— 老用户的话全没了"
        assert turns[0]["question"] == "问过"

    def test_has_ai_badge_counts_chat_across_forms(self, db, make_user):
        """AI 徽标的 EXISTS 子查询同样要跨形态。

        漏改的症状特别不像 bug：列表里那条记录安静地没有 AI 徽标，
        而用户记得自己问过——于是他以为那次解析没成功，又重新解析了一遍。
        """
        uid = make_user()
        db.append_chat_turn(uid, FORMS[1], "问", "答")
        db.upsert_parse_history(uid, FORMS[3], video_title="甲")

        page = database.list_parse_histories(uid)
        assert page["items"][0]["has_ai_result"], (
            "换个形态之后 AI 徽标消失了 —— 用户会以为那次解析没成功，又重跑一遍"
        )


# ── 工单 #25 自己写的三条验收判据 ────────────────────────────────
#
# 票面写的是「方案确定后，以下任一条能转红才算落地」。这里逐条对应，
# 让「落地了」这句话能对回票面，而不是只对回我这四片的提交说明。
#
# 第 1 条**刻意不满足**，而且是有意的——见下面的注释。

SIGNED = "https://www.bilibili.com/video/BV1aa411c7mD?sig=SECRETTOKEN&exp=1789"
UNSIGNED = "https://www.bilibili.com/video/BV1aa411c7mD"


class TestTicketAcceptanceCriteria:
    def test_criterion_2_signed_and_unsigned_are_the_same_row(self, db):
        """判据 2：同一个视频用「带签名」和「不带签名」两种 URL 解析，社区里只有一行。

        落地前：两行（精确比较劈开了）。
        """
        db.reserve_video(SIGNED, 1)
        outcome, _row = db.reserve_video(UNSIGNED, 2)

        with database.get_db() as c:
            n = c.execute("SELECT count(*) AS n FROM videos").fetchone()["n"]
        assert n == 1, f"社区里同一个视频有 {n} 行"
        assert outcome in ("pending", "ready"), f"第二个人抢占成功了：{outcome}"
        assert db.get_video_by_url(UNSIGNED) is not None, "不带签名那一份找不到"
        assert db.get_video_by_url(SIGNED) is not None, "带签名那一份找不到"

    def test_criterion_3_by_url_still_locates_from_the_users_own_text(self, db):
        """判据 3：拿用户手贴的**原始**分享链接，仍能精确定位到那一行。

        票面说这一条「最容易被规范化顺手弄坏」——它把原文换成规范值去查，
        用户粘分享链接就命中不了。而它恰恰是分享场景的主要入口。
        """
        db.reserve_video(SIGNED, 1)
        db.complete_video(SIGNED, summary_md="总结")
        # 用户后来粘的是**另一种**形态（不带签名）
        assert db.get_video_by_url(UNSIGNED)["status"] == database.VIDEO_STATUS_READY

    def test_criterion_1_is_deliberately_not_met(self, db):
        """判据 1（签名不再出现在访客响应里）**刻意不满足**，且是有意的。

        票面把它拆成了「回显哪个版本」那个独立问题，而本轮拍板的是
        「**比较**用 canonical，**回显**仍然是原文」——回显用户当初分享的
        那个地址，对临时分享链接来说可能正是持有人在意的那个。

        所以这一条现在断言的是**现状 + 理由**，而不是把结论藏起来：
        签名确实还在回显里。要闭掉它需要单独决定「回显哪个版本」，
        那是工单 #25 的第 2 问，本轮明确不在范围内。
        """
        db.reserve_video(SIGNED, 1)
        db.complete_video(SIGNED, summary_md="总结")
        row = db.get_video_by_url(SIGNED)
        assert "SECRETTOKEN" in row["video_url"], (
            "回显里没有签名了 —— 如果这是有意改的，请同步更新本条与工单 #25 的第 2 问；"
            "如果不是，那说明有人顺手把原文改了，必须还原"
        )
        assert row["canonical_url"] == UNSIGNED, "但比较用的那一份确实是不带签名的"