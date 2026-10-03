"""固定标签词表与校验。

ADR `docs/adr/0005-fixed-tag-vocabulary.md`：词表由人工维护，模型只能从中选择。
新增标签改 `VOCABULARY_GROUPS`——这是有意的代价，换取标签可枚举、分类语义稳定。

模型只被告知「从下列词表里选」是不够的：它仍会自创「AI 编程」这类近义词。
所以校验必须落在**服务端**（`validate_tags`），词表外的值一律丢弃，绝不原样下发。
"""

#: 兜底标签。必须留在词表内且可达：模型选不出合适的就该选它，而不是空数组。
FALLBACK_TAG = "其他"

#: 一个视频最多 3 个。贴满是分类页碎片化的起点，也让人看不出主领域。
MAX_TAGS = 3

#: 词表本体，按主题分组。**声明顺序即输出顺序**——前端展示稳定、不抖。
#:
#: 覆盖的依据是**社区里真实出现的题材**，不是想象的品类清单。词表只按
#: 分组给出、不加解释，所以「一个明确属于某类的话题却没有对应的类目」
#: 是可枚举的失败模式：模型只能落到「其他」，而这个词表外的归类能力
#: 拿不到、用户也筛不出来。新增类目前先确认它真的落在这份表里。
VOCABULARY_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("技术开发", (
        "编程", "人工智能", "架构设计", "数据库", "运维部署",
        "前端开发", "后端开发", "云原生", "网络安全", "开源项目",
    )),
    ("产品与职场", (
        "产品设计", "用户增长", "创业", "职业发展", "团队管理", "职场维权",
    )),
    # 这一组是补上的：劳动仲裁、财经解读、普法视频原先无处可去，
    # 哪怕同组内还有「职业发展」也因为对不上而整条落进「其他」。
    ("财经与时事", (
        "财经投资", "时事评论", "法律科普", "消费维权",
    )),
    ("学习成长", (
        "英语学习", "知识管理", "读书", "自我提升",
    )),
    ("生活兴趣", (
        "健身", "美食", "旅行", "摄影", "影视", "游戏", "汽车", "宠物",
    )),
    # 同一条视频可能既是游戏又是促销，横测既属数码也属消费决策。
    # 分开给是为了让这两类各自可达，而不是硬塞进「生活兴趣」里当子项。
    ("消费决策", (
        "数码测评", "电商优惠", "性价比攻略",
    )),
    ("兜底", (FALLBACK_TAG,)),
)

#: 扁平后的词表，顺序与上面的分组声明一致。
TAG_VOCABULARY: tuple[str, ...] = tuple(
    tag for _group, tags in VOCABULARY_GROUPS for tag in tags
)

_ALLOWED = frozenset(TAG_VOCABULARY)


def vocabulary_prompt_text() -> str:
    """拼进 prompt 的词表文本（按分组列出）。

    分组保留下来是因为它顺带说明了标签的适用语境，能少一些误选。
    """
    return "\n".join(
        f"{group}：{'、'.join(tags)}" for group, tags in VOCABULARY_GROUPS
    )


def validate_tags(raw) -> tuple[list, list]:
    """把模型给的标签收敛成「词表内、至多 3 个、非空」。

    返回 ``(accepted, rejected)``：

    - ``accepted`` 按**词表声明顺序**返回（不是模型给的顺序），已去重、
      截断到 :data:`MAX_TAGS`；一条都没留下时回落到 ``["其他"]``。
    - ``rejected`` 是被丢掉的词表外原值（含非字符串），用于排查模型为什么跑偏。
      去重与截断不是「违规」，不计入这里。

    prompt 里写了「不要自创」不等于模型不会自创，这一层是服务端兜底：
    下游拿到的永远是词表内的值。
    """
    if not isinstance(raw, (list, tuple)):
        raw = ()

    picked: set[str] = set()
    rejected: list = []
    for item in raw:
        if isinstance(item, str) and item.strip() in _ALLOWED:
            picked.add(item.strip())
        else:
            rejected.append(item)

    accepted = [tag for tag in TAG_VOCABULARY if tag in picked][:MAX_TAGS]
    return (accepted or [FALLBACK_TAG]), rejected
