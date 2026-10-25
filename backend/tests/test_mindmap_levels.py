"""思维导图的结构契约（工单 #106 / ADR 0027）。

## 判据读的是外部可观察的输出

只有 `clean_mindmap_markdown` 吐出来的 Markdown，以及从总结正文重建出来
的导图。落库那条链路在 `test_summarize_routes.py` 里走公开路径验，存量
重建那条在 `test_mindmap_migration.py` 里走老库升级路径验。

## 本文件自带一个结构化解析器

数节点不数行——行数会把「一条标题带三个叶子」和「三个标题」数成一样。
本文件的解析器**不调用**生产代码的解析器：拿生产实现证明生产实现，等于
没验。解析规则与 markmap 对齐：一级标题是第 1 层，列表叶子挂在它上面那个
标题之下（第 2 层）。
"""
import re

import pytest

from mindmap import (
    MAX_CHILDREN_PER_NODE,
    MAX_HEADING_LEVEL,
    MAX_TOTAL_NODES,
    clean_mindmap_markdown,
    is_degenerate,
    mindmap_depth,
    rebuild_mindmap_from_summary,
)

#: 产品拍板的三个上限（工单 #106）。断言里写**字面量**而不是 import 进来的
#: 常量：期望跟着实现一起动的话，「把上限调大」这种变异就杀不掉了。
MAX_LEVEL = 3
MAX_CHILDREN = 7
MAX_NODES = 30

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_ITEM_RE = re.compile(r"^[-*+•]\s+(.*)$")


def parse_tree(md):
    """把导图 Markdown 解析成一棵树；根节点是 dict 或 None。

    节点形如 {"kind": "heading"/"item", "level": int, "text": str,
    "children": [节点]}。
    """
    root = None
    stack = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            node = {
                "kind": "heading",
                "level": level,
                "text": heading.group(2),
                "children": [],
            }
            while stack and stack[-1]["level"] >= level:
                stack.pop()
            if stack:
                stack[-1]["children"].append(node)
            else:
                assert root is None, "导图出现了第二个根标题"
                root = node
            stack.append(node)
            continue
        item = _ITEM_RE.match(line)
        if item and stack:
            parent = stack[-1]
            parent["children"].append(
                {
                    "kind": "item",
                    "level": parent["level"] + 1,
                    "text": item.group(1),
                    "children": [],
                }
            )
    return root


def flatten(node):
    """先序展开整棵树。"""
    if node is None:
        return []
    out = [node]
    for child in node["children"]:
        out.extend(flatten(child))
    return out


def heading_levels(md):
    """所有标题的层级（列表叶子不占层级）。"""
    return [n["level"] for n in flatten(parse_tree(md)) if n["kind"] == "heading"]


def total_nodes(md):
    """节点总数（标题 + 列表叶子）。"""
    return len(flatten(parse_tree(md)))


def max_children(md):
    """单个父节点下最多的子节点数。"""
    tree = parse_tree(md)
    if tree is None:
        return 0
    return max(len(node["children"]) for node in flatten(tree))


def over_limit_input():
    """一份超限输入：9 个二级分支，每个下面 9 个三级节点。"""
    return "# 主题\n" + "".join(
        f"## 分支{i}\n" + "".join(f"### 细{i}_{j}\n" for j in range(9))
        for i in range(9)
    )


class TestCleanKeepsNodesAndLevels:
    """工单 #106 的第一个缺陷：提升一级标题时后代没有同步下移。"""

    def test_promoting_the_root_keeps_every_node_and_three_levels(self):
        md = "## 主题\n### 要点一\n#### 细节一\n### 要点二\n#### 细节二"
        out = clean_mindmap_markdown(md)
        tree = parse_tree(out)
        assert out.startswith("# 主题")
        # 节点一个不少：原来 5 个，清洗后还是 5 个
        assert total_nodes(out) == 5
        # 相对深度不变：模型写的是 ##/###/####，洗完是 #/##/###
        assert [n["level"] for n in flatten(tree)] == [1, 2, 3, 2, 3]
        assert max(heading_levels(out)) <= MAX_LEVEL

    def test_a_root_written_as_h2_lifts_its_children_one_level_down(self):
        out = clean_mindmap_markdown("## 主题\n### 要点一")
        assert out.startswith("# 主题")
        assert "## 要点一" in out

    def test_a_skip_in_levels_is_closed(self):
        out = clean_mindmap_markdown("# 主题\n### 子\n#### 孙")
        assert [n["level"] for n in flatten(parse_tree(out))] == [1, 2, 3]

    def test_exactly_one_root_survives(self):
        out = clean_mindmap_markdown("## 甲\n### 甲一\n## 乙\n### 乙一")
        assert out.count("\n# ") == 0
        assert len(heading_levels(out)) == 4


class TestLimitsAreEnforced:
    """工单 #106 的第二个缺陷：上限没人执行。"""

    def test_over_limit_input_is_truncated_within_every_limit(self):
        md = over_limit_input()
        # 先确认这份输入确实超限（否则下面的断言是空的）
        assert total_nodes(md) > MAX_NODES
        out = clean_mindmap_markdown(md)
        assert total_nodes(out) <= MAX_NODES
        assert max(heading_levels(out)) <= MAX_LEVEL
        assert max_children(out) <= MAX_CHILDREN

    def test_a_wide_branch_is_cut_to_the_children_limit(self):
        md = "# 主题\n" + "".join(f"## 分支{i}\n" for i in range(9))
        out = clean_mindmap_markdown(md)
        assert total_nodes(md) > MAX_CHILDREN
        assert max_children(out) <= MAX_CHILDREN
        # 总节点上限没到，所以被切掉的只是那一支的宽度
        assert total_nodes(out) == 1 + MAX_CHILDREN

    def test_a_deep_branch_is_cut_to_the_level_limit(self):
        md = "# 主题\n## 分支\n### 细\n#### 太深\n- 叶子"
        out = clean_mindmap_markdown(md)
        assert max(heading_levels(out)) <= MAX_LEVEL
        assert "太深" not in out

    def test_input_inside_the_limits_is_not_truncated(self):
        md = "# 主题\n## 分支\n### 细\n- 叶子"
        out = clean_mindmap_markdown(md)
        assert total_nodes(out) == total_nodes(md)


class TestTheLimitsAreTheOnesWeDecided:
    """上限是产品决定，不是实现细节：常量与拍板值必须一致。"""

    def test_the_module_limits_match_the_decided_ones(self):
        assert MAX_HEADING_LEVEL == 3
        assert MAX_CHILDREN_PER_NODE == 7
        assert MAX_TOTAL_NODES == 30


class TestDegenerateDetection:
    def test_a_single_level_map_is_degenerate(self):
        assert is_degenerate("# 视频思维导图") is True

    def test_a_two_level_map_is_not_degenerate(self):
        assert is_degenerate("# 视频思维导图\n## 分支") is False

    def test_depth_counts_headings_only(self):
        assert mindmap_depth("# 主题\n## 分支\n### 细") == 3
        assert mindmap_depth("没有标题") == 0


class TestRebuildFromSummary:
    """兜底重建的来源：总结正文里现成的标题结构。"""

    SUMMARY = (
        "# 视频主题\n"
        "## 视频概述\n"
        "- 本期实测三款扫地机器人\n"
        "## 吸力表现\n"
        "### 甲机地毯残留最少\n"
        "## 结论\n"
        "- 效果优先选甲机\n"
    )

    def test_the_rebuilt_map_has_more_than_one_level(self):
        out = rebuild_mindmap_from_summary(self.SUMMARY)
        assert out.startswith("# 视频主题")
        assert max(heading_levels(out)) >= 2
        assert total_nodes(out) >= 3

    def test_the_rebuilt_map_keeps_the_sections_of_the_summary(self):
        out = rebuild_mindmap_from_summary(self.SUMMARY)
        assert "## 视频概述" in out
        assert "## 吸力表现" in out
        assert "### 甲机地毯残留最少" in out

    def test_a_flat_summary_still_rebuilds_into_two_levels(self):
        out = rebuild_mindmap_from_summary("# 只有根\n- 甲\n- 乙")
        assert max(heading_levels(out)) >= 2

    def test_a_summary_without_any_structure_rebuilds_nothing(self):
        # 没有标题也没有要点：没有结构来源，宁可什么都不重建
        assert rebuild_mindmap_from_summary("") == ""
        assert rebuild_mindmap_from_summary("一整段没有结构的散文") == ""


@pytest.mark.parametrize(
    "md",
    [
        "",
        "没有标题也没有列表",
        "# 主题",
        "## 主题\n### 要点",
    ],
)
def test_cleaning_never_raises(md):
    assert isinstance(clean_mindmap_markdown(md), str)
