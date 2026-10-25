#!/usr/bin/env python3
"""真实视频的 AI 总结端到端质量探针（手动跑，**不进 init.sh**）。

## 这个脚本回答什么

「AI 总结到底做得怎么样」不是一个能靠单测回答的问题：单测只能证明
**管道没坏**，证明不了**产出够不够完整、思维导图有没有层级**。这个脚本
用一条真实视频（默认是一条公开的科普视频）跑完整条链路，然后把可核对的
数字打出来：

- 字幕拿到多少字（多少段、什么语言）
- 总结正文多少字、几个二级标题（``##``）
- 思维导图几级、几个节点
- 标签是什么

这些数字是**对照量**：换模型、换提示词、换模板之后重跑一次，拿两次数字
比一比，才知道改动是变好了还是变差了。

**但一次不算数**：总结调用是带温度的采样（平台路径 temperature=0.4），同一
条素材跑两次，字数、标题数、导图节点数都会飘（本仓库实测同一条素材两
次分别是 1771 字/7 个 H2/10 节点 与 1694 字/5 个 H2/13 节点）。
要下结论请**至少跑三次取中位数**，或者把温度调到 0 再比。

## 为什么不进 init.sh

1. 它要真调模型，**每次都花钱**（一次几分钟的科普视频约几厘到几分钱）。
2. 它要联网，CI 里跑会让门禁变成「看第三方服务脸色」。
3. 它的产出是**质量**，不是**对错**：模型换个版本数字就会变，拿它当门禁
   只会天天误报。

所以它归在 ``scripts/ops/`` 下，**由人按需手动跑**。门禁里只留
``scripts/ops/check_xff_bypass.py`` 那种「判定式」检查——那类有确定的
对错，可以进 CI。

## 怎么跑

在**装好依赖且配好 AI key** 的后端环境里：

    # 容器里（compose 起的栈）
    docker compose exec backend python /app/scripts/ops/probe_ai_e2e.py

    # 换一条视频（必须是能取到字幕的公开视频）
    docker compose exec backend python /app/scripts/ops/probe_ai_e2e.py \\
        https://www.bilibili.com/video/BV1cJ411W75a

    # 只看字幕拿不拿得到（不调模型、不花钱）
    docker compose exec backend python /app/scripts/ops/probe_ai_e2e.py --no-ai

    # 换提示词模板（对照四份模板的产出形态）
    docker compose exec backend python /app/scripts/ops/probe_ai_e2e.py \\
        --template brief

## 输出

每行一个 ``键=值``，便于脚本或人眼比对。退出码：0 = 全流程跑通；
非 0 = 某一环断了（取不到字幕、模型报错等），细节在异常信息里。

## 硬边界

- **不打印任何 key**：只打印模型名与 base_url。
- 脚本**不改任何数据**：不写库、不落文件。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

def _backend_dir() -> Path:
    """找到 ``backend/`` 目录。

    脚本在仓库里是 ``<repo>/scripts/ops/probe_ai_e2e.py``，但运维常把它
    ``docker cp`` 到容器的 ``/tmp`` 里跑——那时「往上两级」就不存在了。
    所以按候选顺序找**第一个真的装着 summarizer.py 的目录**，找不到才退回
    仓库布局那一条。
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "backend" if len(here.parents) > 2 else None,
        here.parents[1] / "backend" if len(here.parents) > 1 else None,
        Path("/app/backend"),
        Path.cwd() / "backend",
    ]
    for candidate in candidates:
        if candidate and (candidate / "summarizer.py").exists():
            return candidate
    # 一个都没命中：给一个**存在**的目录（脚本被放在 /tmp 之类的地方时
    # parents 不足两级，直接索引会抛 IndexError）。
    for parent in here.parents:
        return parent / "backend"
    return Path("backend")


# 允许直接 `python scripts/ops/probe_ai_e2e.py` 运行：把 backend/ 放进 import 路径
sys.path.insert(0, str(_backend_dir()))

#: 默认素材：一条公开的科普视频，主题是「一个问题分几层解释」——
#: 这种题材最能看出总结的完整度与思维导图的层级。
DEFAULT_URL = "https://www.bilibili.com/video/BV1cJ411W75a"


def _count_headings(markdown: str) -> int:
    """二级标题（``## ``）的个数——总结「分了几块」的可核对量。"""
    return sum(1 for line in markdown.splitlines() if line.startswith("## "))


def _mindmap_shape(mindmap_markdown: str) -> tuple[int, int]:
    """思维导图的（最深层级, 节点总数）。

    层级按标题井号个数算；列表项（``- ``）**不算节点**——它们是节点的
    内容，不是分支。工单 #106 的上限是「≤3 级 / 每支 ≤7 / 总计 ≤30」，
    这里只报数，不判合格与否（合格与否由用例判）。
    """
    depth = 0
    nodes = 0
    for line in mindmap_markdown.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            depth = max(depth, len(stripped) - len(stripped.lstrip("#")))
            nodes += 1
    return depth, nodes


def _base_url_of(summarizer) -> str:
    """端点地址。

    客户端有两层：OpenAI 兼容协议下 ``client`` 是包装器
    （``client.raw`` 是**属性**，取出来就是被包的 SDK 客户端），Anthropic
    协议下 ``client`` 自己带 ``base_url``。哪条路都取不到就回落到环境
    变量——**只打印地址，不打印 key**。
    """
    client = getattr(summarizer, "client", None)
    inner = getattr(client, "raw", None)
    if inner is None:
        inner = client
    for candidate in (client, inner):
        base_url = getattr(candidate, "base_url", None)
        if base_url:
            return str(base_url)
    return os.getenv("ALIYUN_BAILIAN_BASE_URL", "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实视频的 AI 总结端到端质量探针")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL, help="视频 URL")
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="只探字幕（不调模型、不花钱）",
    )
    parser.add_argument(
        "--template",
        default=None,
        help="用哪个提示词模板（full / brief / tutorial / analysis）。缺省=平台默认",
    )
    args = parser.parse_args(argv)

    # 与应用本体同一条加载链：后端读配置走的是 python-dotenv（backend/main.py
    # 里 load_dotenv()）。这里自己调一次，才能在「宿主机直接跑」时也拿到 key；
    # 已经存在的环境变量不会被它覆盖（容器里 compose 注入的就是）。
    # 路径要写死：load_dotenv() 不带参数时是**从调用方文件所在目录往上找**
    # .env，而这个脚本在 scripts/ops/ 下，往上找不到 backend/.env。
    # 真值文件在 **backend/ 里面**（backend/.env），所以是 backend 目录
    # 自己的 .env，不是它的上级目录——曾经写错成上一级，宿主机直接跑时
    # 就读不到 key，表现为「未检测到 AI 服务 API Key」。
    try:
        from dotenv import load_dotenv

        env_path = _backend_dir() / ".env"
        load_dotenv(env_path if env_path.exists() else None)
    except Exception:  # pragma: no cover - 没装 dotenv 时按环境变量走
        pass

    from summarizer import SubtitleExtractor, VideoSummarizer

    extractor = SubtitleExtractor()
    info = extractor.extract(args.url)
    segments = info.get("segments") or []
    language = info.get("language") or "zh"
    subtitle = extractor._join_segments(segments, language)
    print("URL=%s" % args.url)
    print("SUBTITLE_SEGMENTS=%d" % len(segments))
    print("SUBTITLE_CHARS=%d" % len(subtitle))
    print("SUBTITLE_LANGUAGE=%s" % language)
    if not subtitle.strip():
        print("SUBTITLE_MISSING=1")
        print("PROBE_VERDICT=FAIL")
        return 1
    if args.no_ai:
        print("PROBE_VERDICT=PASS")
        return 0

    summarizer = VideoSummarizer()
    print("PROVIDER=%s" % summarizer.provider)
    print("MODEL=%s" % summarizer.model)
    print("BASE_URL=%s" % _base_url_of(summarizer))
    print("TEMPLATE=%s" % (args.template or "<平台默认>"))

    summary_parts: list[str] = []
    mindmap_markdown = ""
    tags: object = ""
    for kind, payload in summarizer.summarize_full_stream(subtitle, template=args.template):
        if kind == "summary":
            summary_parts.append(payload)
        elif kind == "mindmap":
            mindmap_markdown = payload
        else:
            tags = payload

    summary = "".join(summary_parts)
    depth, nodes = _mindmap_shape(mindmap_markdown)
    print("SUMMARY_CHARS=%d" % len(summary))
    print("SUMMARY_H2=%d" % _count_headings(summary))
    print("MINDMAP_DEPTH=%d" % depth)
    print("MINDMAP_NODES=%d" % nodes)
    print("TAGS=%s" % (tags,))
    print("PROBE_VERDICT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
