"""思维导图的结构规则（工单 #106 / ADR 0027）。

这个模块只做纯文本处理：不读库、不调模型。因此 `summarizer` 与 `database`
都能**顶层** import 它而不会绕成环——与 `model_catalog` 那个刻意延迟的
import 不同（同样的话见 database.py 顶部）。

## 结构契约

一份导图是一棵以**唯一**一级标题为根的树：

* **深度 ≤ 3 级**：`#` → `##` → `###`。更深的标题连同它的子树被截断。
* **每支 ≤ 7 个子节点**：同一个父节点下的同层子节点（标题 + 列表叶子）
  最多 7 个。
* **全图 ≤ 30 个节点**（标题 + 列表叶子）。

「每层 ≤ 7」按**分支扇出**解释，不是「整层合计 ≤ 7」：后者会让 3 层最多
只有 1+7+7=15 个节点，总节点上限 30 永远碰不到——那是一条死约束。扇出
解释下三条上限都非平凡，也都能被变异杀死。

## 为什么「提升根标题」必须连带下移后代

模型经常把根写成 `## 主题`、子节点写成 `### 要点`：

    ## 主题
    ### 要点一

只把根提升成 `# 主题` 而不管后代，后代就成了 `###`——比根深两级，在
3 级上限下会被整棵截掉，节点凭空消失（5 节点 3 级只剩 3 节点）。
本模块按**树深度**重算级别（子节点恒为父节点 +1），整棵子树的相对深度
因此保持不变：`##`/`###` 变成 `#`/`##`，节点一个不少。

## 截断行为（超限时的确定行为）

按文档顺序深度优先保留：某个节点若会突破「深度 ≤3 / 每支 ≤7 / 全图 ≤30」
中的任何一条，它**连同自己的子树**一起被丢掉；它后面的兄弟节点仍然可以
被保留（例如某个二级分支被丢掉后，同级的另一个分支不受影响）。
"""
import re

#: 标题层级上限：`#` → `##` → `###`。
MAX_HEADING_LEVEL = 3

#: 同一个父节点下的同层子节点上限（标题 + 列表叶子）。
MAX_CHILDREN_PER_NODE = 7

#: 全图节点总数上限（标题 + 列表叶子）。
MAX_TOTAL_NODES = 30

#: 模型没给标题时用的兜底根标题。
DEFAULT_ROOT_TITLE = "视频思维导图"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^[-*+•]\s+(.*)$")
_FIRST_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_LEAD_JUNK_RE = re.compile(r"^[-*•#\d.、\s]+")


class _Node:
    """导图树的一个节点：标题（kind="heading"）或列表叶子（kind="item"）。"""

    __slots__ = ("kind", "level", "text", "children")

    def __init__(self, kind, text, level=0):
        self.kind = kind
        self.level = level
        self.text = text
        self.children = []


def mindmap_depth(md: str) -> int:
    """导图的最大标题层级（没有标题时为 0）。

    「层级」只数标题：`#` 是一级，`###` 是三级。列表叶子是挂在标题下面的
    叶子，不占层级——它渲染时的深度仍落在 markmap 支持的范围内。
    """
    depth = 0
    for line in (md or "").splitlines():
        heading = _HEADING_RE.match(line.strip())
        if heading:
            depth = max(depth, len(heading.group(1)))
    return depth


def is_degenerate(md: str) -> bool:
    """导图是不是「退化」了：没有标题，或者只有一级（没有分支）。

    退化的导图在 markmap 上就是一条光杆，读者看不到任何结构。
    """
    return mindmap_depth(md) < 2


def clean_mindmap_markdown(md: str) -> str:
    """把模型给的思维导图洗成一份**结构合规**的 Markdown。

    洗完的导图一定满足：唯一一级根标题、深度 ≤3、每支 ≤7 子节点、
    全图 ≤30 节点；超出的部分按文档顺序截断（见模块 docstring）。
    """
    if not md or not md.strip():
        return md or ""
    text = md.strip()
    # 1. 去除代码块围栏
    text = re.sub(r"^```(?:markdown|md)?\s*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n?```\s*$", "", text)
    # 2. 截掉第一个标题之前的前导说明文字
    first = _FIRST_HEADING_RE.search(text)
    if not first:
        # 3. 兜底：没有任何 Markdown 标题时，把非空文本行转为根节点下的列表
        return _fallback_from_plain_lines(text)
    blocks = _parse_blocks(text[first.start():])
    forest = _build_forest(blocks)
    if not forest:
        return _fallback_from_plain_lines(text)
    root = forest[0]
    # 4. 唯一的根：其余顶层块挂到根下面（否则会渲染出第二个一级标题）
    for extra in forest[1:]:
        root.children.append(extra)
    out = []
    _render_node(root, 1, out, {"total": 0})
    return "\n".join(out)


def rebuild_mindmap_from_summary(summary_md: str) -> str:
    """从总结正文重建导图（模型给的导图为空或只有一层时的兜底）。

    总结正文里已经有现成的结构：`# 主题` / `## 章节` / `### 小节`。
    这里只取**标题结构**——总结里的要点是完整句子，搬进导图会变成几十字
    的长节点（导图的节点文字要短）。

    总结里没有二级标题（扁平结构）时，退而用三级标题或要点当二级分支：
    否则重建出来仍然只有一层，等于没重建。整段正文既没有标题也没有要点
    时返回空串——没有结构来源时，宁可什么都不重建，也不拿散文编一棵
    导图出来。
    """
    if not summary_md or not summary_md.strip():
        return ""
    blocks = _parse_blocks(summary_md)
    headings = [b for b in blocks if b.kind == "heading"]
    if not headings:
        # 连标题都没有：要点当分支，保证重建后至少两层
        branches = [b for b in blocks if b.kind == "item"]
        if not branches:
            return ""
        root = _Node("heading", DEFAULT_ROOT_TITLE, 1)
        for block in branches:
            root.children.append(_Node("heading", block.text, 2))
        return _render_tree(root)

    first = headings[0]
    root = _Node("heading", first.text, 1)
    sections = []
    current = None
    for block in blocks:
        if block is first:
            continue
        if block.kind == "heading":
            if block.level <= 2:
                current = _Node("heading", block.text, 2)
                sections.append(current)
            elif current is not None:
                current.children.append(_Node("heading", block.text, 3))
        # 列表叶子不搬进导图：总结的要点是整句，节点会太长
    if not sections:
        # 没有二级标题：三级标题或要点当分支，否则重建出来还是一层
        for block in blocks:
            if block is first:
                continue
            if block.kind in ("heading", "item"):
                sections.append(_Node("heading", block.text, 2))
    for section in sections:
        root.children.append(section)
    return _render_tree(root)


def _parse_blocks(text: str):
    """把一段 Markdown 拆成「标题 / 列表叶子 / 散行」块。

    三种块分别是 kind="heading"、kind="item"、kind="text"。缩进不影响归属：
    导图契约里叶子只是挂在标题下面的叶子。散行（既不是标题也不是列表项）
    在清洗时当叶子保留，在重建时**不**当结构用——那是散文，不是节点。
    """
    blocks = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append(_Node("heading", heading.group(2).strip(), len(heading.group(1))))
            continue
        item = _LIST_RE.match(line)
        if item:
            blocks.append(_Node("item", item.group(1).strip()))
        else:
            blocks.append(_Node("text", line))
    return blocks


def _build_forest(blocks):
    """按标题级别把块串成森林（顶层可能有多个根）。"""
    roots = []
    stack = []
    for block in blocks:
        if block.kind != "heading":
            if stack:
                stack[-1][1].children.append(block)
            continue
        while stack and stack[-1][0] >= block.level:
            stack.pop()
        if stack:
            stack[-1][1].children.append(block)
        else:
            roots.append(block)
        stack.append((block.level, block))
    return roots


def _child_level(parent_level: int, child: _Node) -> int:
    """子节点的级别恒为父级别 +1。

    这一行是「提升根标题时后代同步下移」的落点：模型把根写成 `##`、
    子节点写成 `###` 时，按树深度重算会把后代整体上移一级（`###` → `##`），
    结构与节点数都不变。反过来（保留原级别）就会跨级，节点在 3 级上限
    下被截掉。
    """
    return parent_level + 1


def _render_node(node: _Node, level: int, out, state) -> None:
    """渲染一个节点并执行三层上限（深度 / 每支子节点数 / 全图节点数）。"""
    if state["total"] >= MAX_TOTAL_NODES:
        return
    if node.kind == "heading":
        if level > MAX_HEADING_LEVEL:
            return
        out.append("#" * level + " " + node.text)
    else:
        out.append("- " + node.text)
    state["total"] += 1
    used = 0
    for child in node.children:
        if used >= MAX_CHILDREN_PER_NODE:
            break
        before = len(out)
        _render_node(child, _child_level(level, child), out, state)
        if len(out) > before:
            used += 1


def _render_tree(root: _Node) -> str:
    out = []
    _render_node(root, 1, out, {"total": 0})
    return "\n".join(out)


def _fallback_from_plain_lines(text: str) -> str:
    """完全没有标题的输入：兜底成一个根 + 若干叶子。"""
    items = []
    for line in text.splitlines():
        clean = _LEAD_JUNK_RE.sub("", line.strip()).strip()
        if clean:
            items.append(clean)
    if not items:
        return "# %s\n- 暂无内容" % DEFAULT_ROOT_TITLE
    root = _Node("heading", DEFAULT_ROOT_TITLE, 1)
    for item in items:
        root.children.append(_Node("item", item))
    return _render_tree(root)
