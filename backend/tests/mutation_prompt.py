"""变异脚本：证明「导图结构契约」那组用例真在验（可重跑）。

改提示词很容易，改完门禁照样绿——因为没有任何一条测试会因为
「提示词又变回只讲格式」而转红。这份脚本逐个破坏 → 跑指定测试 →
必须转红。绿灯不算证据。

沿用 mutation_check.py 的两处纪律：
  1. 变异前先确认目标能收集到测试（pytest 的 exit 5 是「一条没选中」，
     不是「测试失败」；用退出码判存活会把空变异伪装成护栏有效）；
  2. 替换时归一化行尾符——summarizer.py 是 CRLF，模式串按 \\n 写，
     不归一就一个字符都匹配不上，「没命中」长得和「守不住」一模一样。

工单 #106 之后再多守两条：
  3. **KILLED 必须伴随 AssertionError**。语法错 / KeyError / TypeError
     让用例变红的方式，和「断言抓到了缺陷」不是一回事——那说明变异体
     自己坏了，不是护栏在生效。
  4. 每个变异跑完立刻**字节级**还原，并做一次还原自检（比对原始字节），
     绝不留下变异体。

工单 #105 再加一条同口径的：**模板选择被忽略**（永远用默认模板）要能变红。
接口、提示词渲染、落库三处都各守一次——只守一处，另外两处坏了照样绿。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from venv_python import venv_python

TESTS = Path(__file__).resolve().parent
BACKEND = TESTS.parent
PY = venv_python()
PROMPT_FILE = BACKEND / "prompts" / "summarize_full.md"
MINDMAP_FILE = BACKEND / "mindmap.py"
DATABASE_FILE = BACKEND / "database.py"
PROVIDER_FILE = BACKEND / "provider_config.py"
SUMMARIZER_FILE = BACKEND / "summarizer.py"
CATALOG_FILE = BACKEND / "model_catalog.py"
#: 工单 #105：模板选择的入口（请求体进 api_summarize 的那一步）。
API_SUMMARIZE_FILE = BACKEND / "api_summarize.py"

#: 提示词契约那组用例（工单 #106 口径 3）。
PROMPT_TARGET = ["tests/test_summarize_routes.py", "-k", "TestSummaryPromptContentDemand"]

#: 模板清单与模板选择那组用例（工单 #105 口径 1、2）。
TEMPLATE_TARGET = ["tests/test_summary_templates.py"]

#: 清洗与上限那组用例（工单 #106 口径 1、2）。
LEVELS_TARGET = ["tests/test_mindmap_levels.py"]

#: 存量重建迁移那组用例（工单 #106 口径 5）。
MIGRATION_TARGET = ["tests/test_mindmap_migration.py"]

#: 兜底重建落库那组用例（工单 #106 口径 4）。
STORAGE_TARGET = [
    "tests/test_summarize_routes.py",
    "tests/test_mindmap_migration.py",
]

#: provider 抽象与平台口径那组用例（工单 #107 口径 1、2、4）。
PROVIDER_TARGET = [
    "tests/test_provider_config.py",
    "tests/test_platform_provider.py",
]

#: 模型清单与存量迁移那组用例（工单 #107 口径 3）。
CATALOG_TARGET = ["tests/test_model_catalog.py"]

_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error)")


def read_source(path: Path) -> bytes:
    return path.read_bytes()


def write_source(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def mutate_bytes(data: bytes, pairs) -> bytes:
    """在保持行尾符的前提下做替换，模式串里一律用 \\n 书写。"""
    crlf = b"\r\n" in data
    text = data.decode("utf-8")
    if crlf:
        text = text.replace("\r\n", "\n")

    for pattern, replacement in pairs:
        if pattern not in text:
            return data  # 未命中：原样返回，让调用方识别
        text = text.replace(pattern, replacement)

    if crlf:
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8")


# 越障与脱困 / 尘盒与维护 两节，各 3 条要点
_BLOCK_YUEZHANG = (
    "- 门槛测试设了 1.5cm 和 2cm 两档：只有甲机能过 2cm，乙机和丙机都卡在 1.5cm\n"
    "- 卡住之后三款行为差别很大：甲机会减速换个角度再试，其余两款直接停在原地\n"
    "- 地毯边缘的脱困测试里乙机成功率最低，重复三次里有一次彻底没出来\n"
)
_BLOCK_CHENHE = (
    "- 甲机尘盒可水洗，清理时不必反复倒灰，这是它日常体验加分最多的一点\n"
    "- 乙机的尘盒滤网网孔偏密，两周后堵得比预想快，每次清理都得拿刷子挑\n"
    "- 丙机尘盒是密封设计，不挑灰但不能水洗，长期发霉的风险要自己承担\n"
)

#: 提示词里那段写明结构上限的话（工单 #106 口径 3）。
_LIMITS_BLOCK = (
    "导图的结构上限（超出的部分会被截断，所以要自己控制）：\n"
    "- 层级最多 3 级：`#` → `##` → `###`，不要出现 `####`；\n"
    "- 同一个父节点下最多 7 个子节点；\n"
    "- 至少 2 个二级分支，每个分支下再挂 1-2 个三级要点——只有一层等于没给。\n"
)

MUTATIONS = [
    (
        "P1 篇幅下限被改成「尽量详实」（= 没有下限）",
        PROMPT_FILE,
        PROMPT_TARGET,
        [(r"7. **篇幅**：正文不少于 1500 字（不含标题行）。",
          "7. **篇幅**：正文尽量详实。")],
    ),
    (
        "P2 示例退回占位骨架「（总结正文……）」",
        PROMPT_FILE,
        PROMPT_TARGET,
        [("- 三款定价依次为 2199、1299、899 元，差价主要来自导航方案和尘盒结构",
          "（总结正文……）")],
    ),
    (
        "P3 示例要点被削到 9 条（演示不出密度）",
        PROMPT_FILE,
        PROMPT_TARGET,
        [(_BLOCK_YUEZHANG, ""), (_BLOCK_CHENHE, "")],
    ),
    (
        "P4 导图字数限制去掉作用域隔离（会泛化成全文基调）",
        PROMPT_FILE,
        PROMPT_TARGET,
        [("（下面的长度与节点数限制**只适用于思维导图**，不要套用到第一部分的总结正文。\n"
          "导图刻意精简、总结正文刻意详实，这是两件不同的事。）",
          "（导图按下面的要求输出。）")],
    ),
    (
        "P5 标签数量限制去掉作用域隔离",
        PROMPT_FILE,
        PROMPT_TARGET,
        [("（同样只约束标签本身，不影响前两部分。）", "")],
    ),
    (
        "P6 「覆盖优先 / 不要编造」被软化",
        PROMPT_FILE,
        PROMPT_TARGET,
        [("**覆盖优先**", "**自由取舍**"), ("不要编造", "可以补充")],
    ),
    (
        "P7 正反例对照被删（只说「要有内容」不够）",
        PROMPT_FILE,
        PROMPT_TARGET,
        [("   - 反例：`成本分析：不同模型的 API 费用对比`（只说了这节要讲什么，没有内容）\n"
          "   - 反例：`做法介绍：介绍这道菜的基本做法`（同上）\n"
          "   - 正例：`做法介绍：先用盐水浸泡 20 分钟去腥，两面煎至金黄后转小火慢炖 40 分钟`\n"
          "   - 正例：`模型表现：横测里它在代码任务通过率最高，但长上下文出现了明显退化`\n",
          "")],
    ),
    (
        # 真变异：不是删标题（那只是排版，需求还在），是把整块内容规则挪到
        # 第二部分之后 —— 作用域错位，模型会把「覆盖优先 / 1500 字」读成
        # 导图或标签的要求。
        "P8 内容要求被挪到第二部分之后（作用域错位）",
        PROMPT_FILE,
        PROMPT_TARGET,
        [
            (
                "二、内容（比上面四条格式更重要，务必遵守）\n"
                "5. 这是**摘要**，不是目录。每一条要点都必须写出实质内容——具体的事实、数据、\n"
                "   结论、观点，或它和其它要点的因果关系。**严禁**只把小标题换个说法：\n"
                "   - 反例：`成本分析：不同模型的 API 费用对比`（只说了这节要讲什么，没有内容）\n"
                "   - 反例：`做法介绍：介绍这道菜的基本做法`（同上）\n"
                "   - 正例：`做法介绍：先用盐水浸泡 20 分钟去腥，两面煎至金黄后转小火慢炖 40 分钟`\n"
                "   - 正例：`模型表现：横测里它在代码任务通过率最高，但长上下文出现了明显退化`\n"
                "6. **覆盖优先**：字幕里出现过的每一个主要话题都要讲到，不要因为「看起来不重要」\n"
                "   就略过；拿不准要不要展开的次要话题，也用一句话把它带过。\n"
                "7. **篇幅**：正文不少于 1500 字（不含标题行）。宁可把一个话题的背景和因果讲透，\n"
                "   也不要列一堆小标题就收尾；同时不要用重复的话凑字数。\n"
                "8. 字幕里没有的信息不要编造。听不清、字幕本身就没展开的部分，直接略过。\n\n"
                "【第二部分：思维导图】",
                "【第二部分：思维导图】",
            ),
            (
                "【第三部分：标签】",
                "二、内容（比上面四条格式更重要，务必遵守）\n"
                "5. 这是**摘要**，不是目录。每一条要点都必须写出实质内容。\n"
                "6. **覆盖优先**：字幕里出现过的每一个主要话题都要讲到。\n"
                "7. **篇幅**：正文不少于 1500 字（不含标题行）。\n"
                "8. 字幕里没有的信息不要编造。\n\n"
                "【第三部分：标签】",
            ),
        ],
    ),
    # ── 工单 #106 的结构契约 ──────────────────────────────────────────
    (
        # 口径 1：提升根标题时后代必须同步下移。不下移的话，`###` 会变成
        # 比根深两级，在 3 级上限下整棵被截掉——5 节点 3 级只剩 3 节点。
        "M1 提升根标题时后代不再同步下移（子节点保留模型给的原级别）",
        MINDMAP_FILE,
        LEVELS_TARGET,
        [("    return parent_level + 1\n", "    return child.level\n")],
    ),
    (
        # 口径 2：三个上限得真的被执行。调大它们，超限输入就不再被截断。
        "M2 三个结构上限被调大（超限输入不再被截断）",
        MINDMAP_FILE,
        LEVELS_TARGET,
        [
            ("MAX_HEADING_LEVEL = 3", "MAX_HEADING_LEVEL = 9"),
            ("MAX_CHILDREN_PER_NODE = 7", "MAX_CHILDREN_PER_NODE = 99"),
            ("MAX_TOTAL_NODES = 30", "MAX_TOTAL_NODES = 999"),
        ],
    ),
    (
        # 口径 3：提示词里必须写明结构上限，否则模型不知道能有多深。
        "M3 提示词里的导图结构上限段被删掉",
        PROMPT_FILE,
        PROMPT_TARGET,
        [(_LIMITS_BLOCK, "")],
    ),
    (
        # 口径 3 的另一半：few-shot 样例必须是真的多层。退回单层单分支，
        # 模型被教出来的导图就又只有一层。
        "M4 提示词里的导图样例退回单层单分支",
        PROMPT_FILE,
        PROMPT_TARGET,
        [('{"mindmap": "# 视频主题\\n## 主要结论\\n### 结论要点\\n## 关键数据\\n'
          '### 对比数据", "tags": ["编程", "人工智能"]}',
          '{"mindmap": "# 视频思维导图\\n## 章节\\n### 要点", "tags": ["编程", "人工智能"]}')],
    ),
    (
        # 口径 4：退化检测被短路，兜底重建就再也不会触发。
        "M5 退化检测被短路（退化导图不再触发兜底重建）",
        MINDMAP_FILE,
        STORAGE_TARGET,
        [("    return mindmap_depth(md) < 2\n", "    return False\n")],
    ),
    (
        # 口径 5：存量导图的写迁移空转，老库里的光杆永远重建不了。
        "M6 存量导图重建迁移空转（退化行不被重建）",
        DATABASE_FILE,
        MIGRATION_TARGET,
        [('            if not is_degenerate(row["mindmap_md"]):\n                continue\n',
          "            continue\n")],
    ),
    # ── 工单 #107 的 provider 口径 ───────────────────────────────
    (
        # 口径 1：DeepSeek 兜底没删干净。只配了 DEEPSEEK_API_KEY 也能构造出
        # 客户端——「配了就能用」的错觉回来了，而它打的仍是百炼端点，必然 404。
        "V1 DeepSeek 兜底分支被恢复（只配 DeepSeek key 也算配置好了）",
        SUMMARIZER_FILE,
        PROVIDER_TARGET,
        [('        bailian_key = os.getenv("ALIYUN_BAILIAN_API_KEY", "").strip()\n'
          "        if not bailian_key:\n"
          "            # ── 没配置平台 key（**没有兜底链**，工单 #107）──\n",
          '        bailian_key = os.getenv("ALIYUN_BAILIAN_API_KEY", "").strip()\n'
          "        if not bailian_key:\n"
          '            bailian_key = os.getenv("DEEPSEEK_API_KEY", "").strip()\n'
          "        if not bailian_key:\n"
          "            # ── 没配置平台 key（**没有兜底链**，工单 #107）──\n")],
    ),
    (
        # 口径 2：协议路由对调。配成 anthropic 的 provider 拿到 OpenAI 兼容
        # 客户端（反之亦然）——真实环境里表现成一个看不懂的 404/400。
        "V2 provider 协议路由对调（anthropic 配置走了 OpenAI 兼容客户端）",
        PROVIDER_FILE,
        PROVIDER_TARGET,
        [("    protocol = normalize_protocol(settings.protocol)\n"
          "    if protocol == PROTOCOL_OPENAI:\n"
          "        return _build_openai_client(settings, openai_factory)\n"
          "    return _build_anthropic_client(settings)\n",
          "    protocol = normalize_protocol(settings.protocol)\n"
          "    if protocol == PROTOCOL_OPENAI:\n"
          "        return _build_anthropic_client(settings)\n"
          "    return _build_openai_client(settings, openai_factory)\n")],
    ),
    (
        # 口径 3：清单表里的平台默认模型没改成新模型。新库立刻就对不上。
        "V3 平台默认模型退回旧值 qwen-plus（清单表没跟上）",
        CATALOG_FILE,
        CATALOG_TARGET,
        [('PLATFORM_DEFAULT_MODEL = "qwen3.7-flash"', 'PLATFORM_DEFAULT_MODEL = "qwen-plus"')],
    ),
    (
        # 口径 3 的另一半：存量库靠迁移收敛，迁移空转就等于没改。
        # 改的是**参数**而不是 SQL 字符串：占位符个数与参数个数必须对得上，
        # 否则整条 SQL 直接报错——那是「变异体自己坏了」，不是「护栏在生效」。
        "V4 存量库的默认模型迁移空转（老库永远停在旧模型）",
        CATALOG_FILE,
        CATALOG_TARGET,
        [("        LEGACY_PLATFORM_DEFAULT_MODELS,\n",
          '        ("__never__",),\n')],
    ),
    (
        # 反向对照：provider 抽象里**与契约无关**的旋钮（请求超时）改了，
        # 不该让任何用例转红。守卫越权比变异存活更严重。
        "V5 反向对照：请求超时从 120s 改成 180s（不该让任何用例转红）",
        PROVIDER_FILE,
        PROVIDER_TARGET,
        [("DEFAULT_TIMEOUT = 120.0", "DEFAULT_TIMEOUT = 180.0")],
        "GREEN",
    ),
    # ── 工单 #105 的模板选择 ──────────────────────────────────────────
    (
        # 口径 1：请求里选的模板被忽略（永远用 full）。这一处是选择的
        # **入口**：请求体里的 summary_template 到这里被丢掉，模型拿到
        # 的永远是默认那份，落库的也是默认那份——症状是「下拉框选了没用」。
        "T1 请求里选的模板被忽略（永远用默认模板）",
        API_SUMMARIZE_FILE,
        TEMPLATE_TARGET,
        [("template_id = req.summary_template or default_template_id()",
          "template_id = default_template_id()")],
    ),
    (
        # 口径 1 的另一半：入口收下了选择，但渲染提示词时又扔掉。这一处
        # 更阴——落库的 template 列写着 brief，发给模型的却是 full 的
        # 提示词，界面上完全看不出错。
        "T2 渲染提示词时又把模板选择扔掉（id 映射被短路）",
        SUMMARIZER_FILE,
        TEMPLATE_TARGET,
        [("template_id = template or default_template_id()",
          "template_id = default_template_id()")],
    ),
]


def run(args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(
        [str(PY), "-m", "pytest", *args, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=BACKEND, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    return r.returncode, r.stdout


def parse_counts(out):
    lines = out.strip().splitlines()
    summary = lines[-1] if lines else out
    passed = sum(int(m.group(1)) for m in _COUNT_RE.finditer(summary) if m.group(2) == "passed")
    failed = sum(int(m.group(1)) for m in _COUNT_RE.finditer(summary) if m.group(2) in ("failed", "error"))
    return passed, failed, summary.strip()


def failed_names(out):
    return re.findall(r"(?:FAILED\s+\S+::\S+|_+ Test\S+)", out)


def main():
    # 1. 每个目标都要能收集到测试，且它自己是绿的
    for label, target in (
        ("提示词契约", PROMPT_TARGET),
        ("清洗与上限", LEVELS_TARGET),
        ("存量重建", MIGRATION_TARGET),
        ("provider 协议与平台口径", PROVIDER_TARGET),
        ("模型清单与迁移", CATALOG_TARGET),
        ("模板选择", TEMPLATE_TARGET),
    ):
        code, out = run(list(target))
        p, f, summary = parse_counts(out)
        if code != 0:
            print(f"FAIL: 目标「{label}」基线不绿，无法评估变异")
            print(out[-2000:])
            return 9
        if p + f == 0:
            print(f"FAIL: 目标「{label}」{target} 收集到 0 个测试，变异评估无意义")
            print(out[-1200:])
            return 9
        print(f"基线 {label}: {summary}")
    print()

    killed, survived, not_run, broken, restore_failed = 0, [], [], [], []
    green_ok, over_killed = 0, []

    for entry in MUTATIONS:
        # 第 5 个元素是**期望**：默认 "KILLED"（必须转红）；
        # "GREEN" 是反向对照（必须**不**转红——守卫越权比变异存活更严重）。
        name, path, target, pairs = entry[:4]
        expect = entry[4] if len(entry) > 4 else "KILLED"
        original = read_source(path)
        mutated = mutate_bytes(original, pairs)
        if mutated == original:
            print(f"  [未命中] {name} —— 模式没匹配上，先更新脚本，别跳过")
            not_run.append(name)
            continue
        write_source(path, mutated)
        try:
            code, out = run(list(target))
            _, failed, summary = parse_counts(out)
            if failed == 0:
                if expect == "GREEN":
                    green_ok += 1
                    print(f"GREEN   {name}（反向对照：不该转红，确实没红）")
                else:
                    survived.append(f"{name} —— 存活（{summary}，这组用例没在验它）")
            elif "AssertionError" not in out:
                # 变异体自己坏了（语法错、KeyError……），不是护栏在生效
                broken.append(f"{name} —— 用例红了但没有 AssertionError：{summary}")
            else:
                if expect == "GREEN":
                    over_killed.append(
                        f"{name} —— 反向对照转红了（守卫越权）：{summary}"
                    )
                else:
                    killed += 1
                    names = failed_names(out)
                    print(f"KILLED  {name}")
                    print(f"        转红于: {', '.join(n.strip('_ ') for n in names[:4])}")
        finally:
            write_source(path, original)
            if read_source(path) != original:
                print(f"  [还原失败] {name} —— 文件没能字节级还原")
                restore_failed.append(name)

    expect_killed = sum(
        1 for e in MUTATIONS
        if (e[4] if len(e) > 4 else "KILLED") == "KILLED"
    )
    expect_green = len(MUTATIONS) - expect_killed
    print("=" * 72)
    print(f"变异结果：{killed}/{expect_killed} 杀，"
          f"反向对照 {green_ok}/{expect_green} 条保持绿，"
          f"{len(survived)} 存活，{len(not_run)} 未命中，{len(broken)} 变异体自坏")
    for s in survived:
        print("  存活: " + s)
    for s in not_run:
        print("  未命中: " + s)
    for s in broken:
        print("  变异体自坏: " + s)
    for s in over_killed:
        print("  反向对照转红: " + s)
    for s in restore_failed:
        print("  还原失败: " + s)
    return 1 if (survived or not_run or broken or restore_failed or over_killed) else 0


if __name__ == "__main__":
    sys.exit(main())
