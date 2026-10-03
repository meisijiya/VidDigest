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
"""

import re
from functools import lru_cache
from pathlib import Path
from string import Template

#: prompts/ 与本文件同级。不按 CWD 拼——生产环境 uvicorn 的工作目录
#: 未必是 backend/，按 CWD 找会得到一个"提示词文件不存在"而没人查得到原因。
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

#: 块注释可跨行。写成非贪婪，否则第一个 --> 之后的内容全被吞掉。
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


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
