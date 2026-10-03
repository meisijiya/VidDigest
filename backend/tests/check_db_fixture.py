"""门禁：任何会触库的测试都必须取 db 夹具。

为什么需要它：`conftest.py` 声明了「绝不碰 backend/data/app.db」这条不变量，
但 Python 不会强制它兑现。一条漏取夹具的测试会静默连上开发机上的真实库——
只跑 PRAGMA 时看不出来，一旦它 INSERT 就会污染真实数据。
（实测发生过：4 条测试在打开真实的 app.db，产生 -wal/-shm 边文件。）

这不是一次性核验脚本，是常驻门禁：接进 CI 或本地全量测试。
用法：
    python tests/check_db_fixture.py        # 单独跑
    pytest tests -q                         # 全量时会自动带上 test_db_fixture_guard
"""
import ast
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

#: 会真正触库的函数。只调它们的测试才需要 db 夹具——
#: 纯桩计数测试（StubSummarizer 等）不碰数据库，不该被误报。
DB_FUNCS = {
    "get_db", "init_db", "open_connections", "open_connection_threads",
    "forget_all_connections", "get_connection_generation",
    "create_user", "get_user_by_id", "get_user_by_email", "create_order",
    # 额度：拆分后只剩这一族。工单 #4 之前的 check_summary_quota /
    # consume_summary_quota 已删除——名字留着不起作用，缺了现存的才致命：
    # 漏一个就等于这道门禁对那条路径失明。
    "consume_quota", "check_quota_kind", "refund_quota", "check_quota",
    "quota_limit", "is_vip_active",
    # 社区视频表（工单 #6）：这些函数会触库，测试直接调它们时必须取 db 夹具
    "reserve_video", "complete_video", "release_video",
    "regenerate_video", "get_video_by_url",
    # _paginate 是社区列表与搜索共用的分页信封助手，它自己 `with get_db()`。
    # 上面那两个补进来的名字由 test_db_fixture_gate.py 从源码闭包算出来，
    # 不是手查的 —— 那道元测试存在的意义就是别再靠手查。
    # 社区浏览与检索（工单 #7）：漏一个就等于这道门禁对那条路径失明
    "list_community_videos", "get_community_video",
    "publish_video_card", "_paginate",
    "search_community_videos",
    # 追问会话（工单 #8）：append_chat_history 已退役，追问记录改存 chat_messages
    "append_chat_turn", "get_recent_chat_messages", "get_chat_session",
    "upsert_parse_history", "get_parse_histories", "get_parse_history_detail",
    "delete_parse_history", "get_user_orders",
    "update_order_stripe_session", "complete_order",
    # 管理后台地基（工单 #11）：seed_admin_emails_from_env 会写 users 表。
    # 漏掉它，这道门禁就对「播种测试会不会连上真实 app.db」失明。
    "seed_admin_emails_from_env",
    # 管理后台（工单 #12）：四个读出口 + 唯一的写出口。
    # 漏掉任何一个，这道门禁就对「那条路径会不会连上真实 app.db」失明
    # ——而 POST quota 那个是会**写** users 表的。
    "list_admin_users", "admin_user_detail", "set_user_quota_override",
    "list_admin_community",
    # 社区审核的两个**写**出口。漏掉任何一个，这道门禁就对「测试直接调它」
    # 失明 —— 而它们写的是真实库里的社区内容：update_video_tags 改标签，
    # delete_video_record 删行。后者尤其不能漏：一条漏取夹具的删除测试
    # 连上的会是开发机上有数据的 app.db。
    #
    # 词表读出口不在这里：它不触库（数据全在 tags 模块里），硬塞进名单只会
    # 让这份名单掺进不该有的东西，第二个守门人就懒得看了。
    "update_video_tags", "delete_video_record",
    # 模型清单（工单 #13）：DDL、播种、三个查询口。
    "init_model_catalog", "list_model_providers", "get_model_provider",
    "platform_default_model",
    # 模型清单的**写**路径（ADR 0010「模型清单可改」）。它是写操作，
    # 漏掉它，这道门禁就对「测试直接调它」失明 —— 而那条测试会连上
    # 真实 app.db 并改 model_providers。
    "update_model_provider",
    # 解析历史 · 搜索与筛选（上限 1000 + 收藏）：五个都是新出口。
    # set_parse_history_favorite 与 clear_parse_history 是**写**出口，
    # 漏掉它们，这条门禁就对「测试直接调它写开发机上的真实 app.db」失明。
    "list_parse_histories", "list_parse_history_facets",
    "set_parse_history_favorite", "get_parse_history_favorite",
    "clear_parse_history",
    # 社区标签清单（给「保持标签总量显示」当选项来源）。它是**只读**出口，
    # 但漏掉它这条门禁就对「测试不取夹具、直接读开发机真实 app.db」失明 ——
    # 失明的后果不是写坏数据，是那条测试的断言根本量不到它想量的东西。
    "list_community_tags",
    # 账号生命周期（ADR 0012）：三个都是**写** users 表的，
    # 漏掉任何一个，这道门禁就对「测试直接调它」失明 ——
    # 而 delete_user 还会连带删 chat_messages。
    "create_admin_user", "set_user_admin", "delete_user",
}

#: 取了这些参数就算拿到了 db 夹具（client_app 内部依赖 db）
#:
#: legacy_db 是 db 的「只隔离、不建表」版本：老库升级测试必须先摆一张缺列的
#: 旧表出来，而 db 夹具会先 init_db() 出一个全新库，那条路径在它下面走不到。
#: 两者对数据库的隔离强度相同（都换 DB_PATH、都清所有线程连接），所以
#: 豁免的是「自己管库结构」这一个理由，不是「碰库可以不隔离」。
DB_FIXTURE_ARGS = {"db", "client_app", "legacy_db"}


def find_offenders(path: Path) -> list:
    """返回「会触库却没取 db 夹具」的测试名清单。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue

        args = {a.arg for a in node.args.args}
        if args & DB_FIXTURE_ARGS:
            continue

        calls = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name in DB_FUNCS:
                    calls.add(name)

        if calls:
            offenders.append((node.name, sorted(calls)))

    return offenders


def scan() -> list:
    all_offenders = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        for name, calls in find_offenders(path):
            all_offenders.append((path.name, name, calls))
    return all_offenders


def main() -> int:
    offenders = scan()
    if offenders:
        print("门禁失败：以下测试会触库但未取 db 夹具，可能连到真实生产库：")
        for filename, name, calls in offenders:
            print(f"  {filename}::{name}  触库调用={calls}")
        print("\n修法：给测试加上 db 夹具参数，例如 def test_x(self, db):")
        return 1
    print(f"门禁通过：所有会触库的测试都取了 db 夹具（扫描 {len(list(TESTS_DIR.glob('test_*.py')))} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
