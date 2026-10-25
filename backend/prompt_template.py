"""从 ``prompts/*.md`` 读提示词模板并填占位符。

## 为什么提示词不进代码

改一句措辞要改 Python、改完要重启、评审时看不出改了什么。
放进 ``.md`` 之后：编辑那个文件即可，语法高亮正常，diff 只显示提示词的
变化，评审的人一眼能看出改了哪句话。

## 为什么用 ``string.Template`` 而不是 f-string / ``str.format``

模板里**必须**原样出现 JSON 的花括号（``{"mindmap": ...}``）。
``str.format`` 要求把它写成 ``{{`` / ``}}``，于是文件里出现的那串
双花括号**不是提示词的一部分**，删掉一对就静默改变输出。
``f-string`` 更糟：它根本不能放在文件里。

``string.Template`` 的占位符是 ``$name``，花括号是普通字符。
代价是内容里的字面量 ``$`` 要写成 ``$$``——这条已经写进模板文件的头部注释，
而且 ``substitute`` 遇到裸 ``$`` 会**报错**而不是静默吞掉。

## 头部注释不会进提示词

模板顶端的 ``<!-- ... -->`` 是写给改它的人看的，不是给模型的。
不剥离的话，模型会先读到一段讲「$sentinel 是什么」的话——
既烧 token，又可能让它把占位符名字当成要输出的内容。

## 模板清单只有一份

``prompts/templates.json`` 是「有哪些模板、哪个是默认、每个模板叫什么」
的**唯一**真值。前端下拉框、后端校验、落库默认值都从它派生。

清单里为什么不直接放提示词正文：正文在各自的 ``.md`` 里，清单只做索引。
放两份的话，改了 ``.md`` 却忘了改清单，两边就会各说各话。
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from string import Template

#: prompts/ 与本文件同级。不按 CWD 拼——生产环境 uvicorn 的工作目录
#: 未必是 backend/，按 CWD 找会得到一个"提示词文件不存在"而没人查得到原因。
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

#: 模板清单。与提示词文件同目录——它索引的就是这个目录里的文件。
MANIFEST_FILE = PROMPTS_DIR / "templates.json"

#: 块注释可跨行。写成非贪婪，否则第一个 --> 之后的内容全被吞掉。
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

#: 清单里每个模板必须有的字段。``file`` 是提示词文件名（不含扩展名）。
_TEMPLATE_FIELDS = ("id", "name", "description", "file")


class PromptError(RuntimeError):
    """模板缺失、占位符不认识、或引用了不存在的键。

    单独一个类型而不是复用 ValueError：调用方要靠它把
    「改提示词的人写错了」与「模型调用失败」分开处理——
    前者该让人立刻看到，后者该原样透传给调用方。
    """


def prompt_path(name: str) -> Path:
    """模板文件路径。``name`` 不含扩展名。

    刻意不接受调用方传进来的任意路径：那会让「提示词从哪来」变成一个
    由调用点决定的问题，而模板的内容会直接进模型。
    """
    if "/" in name or "\\" in name or name.startswith("."):
        raise PromptError(f"非法的提示词名：{name!r}")
    return PROMPTS_DIR / f"{name}.md"


@lru_cache(maxsize=None)
def load_template(name: str) -> Template:
    """读模板、剥注释、编译。**结果被缓存**——每次解析都读一次磁盘没有意义。"""
    path = prompt_path(name)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PromptError(
            f"提示词模板不存在：{path}。它必须是随代码一起提交的文件，"
            "不是运行期生成的东西——提示词是代码的一部分。"
        ) from None
    return Template(_COMMENT.sub("", raw).strip())


def render_prompt(name: str, values: dict) -> str:
    """渲染模板。

    占位符对不上时**抛异常**，不返回半成品：一份带着 ``$subtitle``
    字面量发给模型的提示词不会报错，它只会安静地少掉整段字幕，
    然后产出一份凭空编的总结。
    """
    template = load_template(name)
    try:
        return template.substitute(values)
    except KeyError as e:
        raise PromptError(
            f"提示词 {name!r} 里有未提供的占位符 {e}。调用方要补上，"
            "或把它从模板里删掉——留着会让整份提示词带着占位符发出去。"
        ) from None
    except ValueError as e:
        raise PromptError(
            f"提示词 {name!r} 里有非法的 $ 用法（{e}）。字面量 $ 请写成 $$。"
        ) from None


@lru_cache(maxsize=None)
def load_manifest() -> dict:
    """读模板清单（``prompts/templates.json``）。

    **结果被缓存**，与 :func:`load_template` 同一口径：清单改了要重启后端，
    否则进程里跑的还是旧清单。提示词正文也是这个规矩。

    清单坏了要**立刻炸**，不返回半成品：一份缺了默认模板的清单会让
    「不传模板」走到一个不存在的分支，而症状是模型收到了错的提示词——
    不报错，只是慢慢错。
    """
    try:
        raw = MANIFEST_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PromptError(
            f"模板清单不存在：{MANIFEST_FILE}。它必须与提示词文件一起提交。"
        ) from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PromptError(f"模板清单不是合法 JSON：{e}") from None

    templates = data.get("templates")
    if not isinstance(templates, list) or not templates:
        raise PromptError("模板清单里的 templates 必须是非空数组。")
    ids: set[str] = set()
    for item in templates:
        if not isinstance(item, dict):
            raise PromptError("模板清单里的每个模板必须是一个对象。")
        missing = [f for f in _TEMPLATE_FIELDS if not str(item.get(f, "")).strip()]
        if missing:
            raise PromptError(
                f"模板清单里的模板 {item.get('id')!r} 缺字段：{'、'.join(missing)}。"
            )
        if item["id"] in ids:
            raise PromptError(f"模板清单里有重复的模板 id：{item['id']!r}。")
        ids.add(item["id"])
    default = data.get("default")
    if default not in ids:
        raise PromptError(
            f"模板清单的 default={default!r} 不在 templates 里。"
        )
    return data


def list_templates() -> list[dict]:
    """对外的模板清单：**只给 id、展示名与一句说明**。

    刻意不给 ``file``：那是仓库内部的文件名，暴露出去就等于把
    「提示词从哪读」变成外部可依赖的约定。
    """
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "description": item["description"],
        }
        for item in load_manifest()["templates"]
    ]


def default_template_id() -> str:
    """默认模板 id。清单里的 ``default`` 说了算，代码里不再写第二份。"""
    return load_manifest()["default"]


def template_file(template_id: str) -> str:
    """模板 id → 提示词文件名（不含扩展名）。

    未知 id 抛 :class:`PromptError`：模板 id 会从请求体进来，
    拼不出来的 id 必须在这里被挡住，而不是渲染出一个空提示词。
    """
    for item in load_manifest()["templates"]:
        if item["id"] == template_id:
            return item["file"]
    raise PromptError(
        f"未知的总结模板：{template_id!r}。可用的是："
        f"{'、'.join(t['id'] for t in load_manifest()['templates'])}。"
    )


def is_known_template(template_id: str) -> bool:
    """模板 id 是否在清单里。给请求体校验用——未知 id 要报 422，不能静默兜底。"""
    try:
        template_file(template_id)
    except PromptError:
        return False
    return True
