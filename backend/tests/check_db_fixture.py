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
    # 社区视频表（工单 #6）：这四个函数会触库，测试直接调它们时必须取 db 夹具
    "reserve_video", "complete_video", "release_video", "get_video_by_url",
    # 社区浏览与检索（工单 #7）：漏一个就等于这道门禁对那条路径失明
    "list_community_videos", "get_community_video",
    "get_community_video_by_url", "publish_video_card",
    "search_community_videos",
    # 追问会话（工单 #8）：append_chat_history 已退役，追问记录改存 chat_messages
    "append_chat_turn", "get_recent_chat_messages", "get_chat_session",
    "upsert_parse_history", "get_parse_histories", "get_parse_history_detail",
    "delete_parse_history", "get_user_orders",
    "update_order_stripe_session", "complete_order",
}

#: 取了这些参数就算拿到了 db 夹具（client_app 内部依赖 db）
DB_FIXTURE_ARGS = {"db", "client_app"}


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
