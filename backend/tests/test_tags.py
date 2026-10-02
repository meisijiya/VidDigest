"""固定标签词表的校验边界（工单 #5）。

ADR `docs/adr/0005-fixed-tag-vocabulary.md` 说模型只能从词表里选。这份测试
守的是**服务端那一层**：prompt 里写「不要自创」不算数，`validate_tags`
必须把词表外的值挡在线下。

每个用例都对着一个具体的变异：删掉去重、删掉截断、删掉回落、把
「按词表声明顺序返回」改成「按模型给的顺序返回」——各自会有用例转红。
"""
import tags


class TestVocabularyItself:
    """词表本体。改词表是有意的动作，但要有意识地改。"""

    def test_has_no_duplicate(self):
        assert len(tags.TAG_VOCABULARY) == len(set(tags.TAG_VOCABULARY)), (
            f"词表里有重复项：{tags.TAG_VOCABULARY}"
        )

    def test_fallback_is_inside_the_vocabulary(self):
        """兜底标签必须在词表内且可达，否则它只是个摆设。"""
        assert tags.FALLBACK_TAG in tags.TAG_VOCABULARY

    def test_vocabulary_is_built_from_the_groups_in_order(self):
        """扁平后的顺序必须与分组声明一致——输出顺序就靠它。"""
        flat = tuple(t for _g, group in tags.VOCABULARY_GROUPS for t in group)
        assert tags.TAG_VOCABULARY == flat

    def test_prompt_text_lists_every_tag(self):
        """词表与 prompt 不同步时，模型只看得见其中一份。"""
        text = tags.vocabulary_prompt_text()
        missing = [t for t in tags.TAG_VOCABULARY if t not in text]
        assert not missing, f"这些标签没进 prompt：{missing}"
        for group, _members in tags.VOCABULARY_GROUPS:
            assert group in text, f"分组 {group} 没进 prompt"

    def test_cap_is_three(self):
        """一个视频最多 3 个：贴满是分类页碎片化的起点。"""
        assert tags.MAX_TAGS == 3


class TestOutOfVocabularyIsDropped:
    def test_invented_tags_are_not_accepted(self):
        accepted, rejected = tags.validate_tags(["编程", "AI 编程", "编程教学"])

        assert accepted == ["编程"], accepted
        assert "AI 编程" in rejected and "编程教学" in rejected, rejected

    def test_near_miss_of_a_real_tag_is_still_rejected(self):
        """词表里有「编程」不代表「编程教学」也在——近义词正是 ADR 要防的。"""
        accepted, rejected = tags.validate_tags(["Python 教程", "code review"])
        assert accepted == [tags.FALLBACK_TAG], accepted
        assert rejected == ["Python 教程", "code review"], rejected

    def test_rejected_keeps_model_originals_for_debugging(self):
        """rejected 是排查「模型为什么跑偏」的唯一线索，不能被洗掉。"""
        _accepted, rejected = tags.validate_tags(["", "  ", "读书"])
        assert rejected == ["", "  "], rejected

    def test_non_string_items_are_dropped(self):
        """模型输出是不可信输入：非字符串不该变成下游的 TypeError。"""
        accepted, rejected = tags.validate_tags(["读书", 42, None, {"a": 1}])
        assert accepted == ["读书"], accepted
        assert len(rejected) == 3, rejected


class TestAcceptedShape:
    def test_duplicates_collapse(self):
        accepted, _rejected = tags.validate_tags(["读书", "读书", "读书"])
        assert accepted == ["读书"], accepted

    def test_capped_at_three(self):
        five = ["读书", "健身", "旅行", "摄影", "影视"]
        accepted, _rejected = tags.validate_tags(five)
        assert len(accepted) == 3, accepted
        assert set(accepted) <= set(five)

    def test_cap_takes_the_earliest_in_vocabulary_order(self):
        """截断发生在排序之后：留哪三个由词表决定，不由模型决定。"""
        accepted, _rejected = tags.validate_tags(["影视", "健身", "摄影", "旅行", "读书"])
        # 声明顺序：读书 → 健身 → 旅行 → 摄影 → 影视
        assert accepted == ["读书", "健身", "旅行"], accepted

    def test_output_follows_vocabulary_order_not_model_order(self):
        """前端展示要稳定：顺序由词表说了算，模型给乱序也不抖。"""
        accepted, _rejected = tags.validate_tags(["读书", "人工智能", "编程"])
        assert accepted == ["编程", "人工智能", "读书"], accepted

    def test_surrounding_whitespace_is_tolerated(self):
        accepted, rejected = tags.validate_tags([" 编程 "])
        assert accepted == ["编程"], accepted
        assert rejected == [], rejected


class TestNeverEmpty:
    """数组永不为空：每个视频都得有标签可归。"""

    def test_empty_list_falls_back(self):
        accepted, rejected = tags.validate_tags([])
        assert accepted == [tags.FALLBACK_TAG], accepted
        assert rejected == [], rejected

    def test_all_out_of_vocabulary_falls_back(self):
        accepted, rejected = tags.validate_tags(["自创一", "自创二"])
        assert accepted == [tags.FALLBACK_TAG], accepted
        assert rejected == ["自创一", "自创二"], rejected

    def test_fallback_is_reachable_as_a_real_choice(self):
        """模型主动选「其他」与系统回落同值，但语义不同：不该被当成非法。"""
        accepted, rejected = tags.validate_tags([tags.FALLBACK_TAG])
        assert accepted == [tags.FALLBACK_TAG], accepted
        assert rejected == [], rejected

    def test_fallback_does_not_crowd_out_real_tags(self):
        """「其他」声明在最后，不该挤掉真标签。"""
        accepted, _rejected = tags.validate_tags([tags.FALLBACK_TAG, "摄影", "影视", "游戏"])
        assert accepted == ["摄影", "影视", "游戏"], accepted

    def test_missing_or_wrong_type_input_does_not_raise(self):
        """模型把 tags 写成字符串或干脆没写，都不该让整次解析炸掉。"""
        for raw in (None, "编程", 42, {"a": 1}):
            accepted, _rejected = tags.validate_tags(raw)
            assert accepted == [tags.FALLBACK_TAG], raw
