import os
import json
import sqlite3
import threading
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "app.db")

# 线程本地连接缓存（uvicorn 线程池复用线程，连接随之复用）
_thread_local = threading.local()

# 全部已建立连接（含线程池工作线程那份），供测试夹具彻底清理。
# threading.local() 的属性只有持有它的线程能删，主测试线程拿不到工作线程那份，
# 因此必须在这里留一份清单。
#
# 强引用是必须的（Connection 不支持弱引用，实测 weakref.WeakSet 直接抛
# TypeError: cannot create weak reference to 'sqlite3.Connection' object），
# 代价是「线程死了但清单还留着它的连接」会造成 fd 滞留。因此每次登记前
# 先剔除已死线程的条目——threading.enumerate() 只列活线程。
_open_conns = []
#: 每份连接由哪个线程创建（与 _open_conns 同序）。
#: 测试要证明「工作线程那份确实被登记了」，光数数量不够——
#: 同一份连接被登记两次也能让计数达标。
_open_conns_threads = []
_open_conns_lock = threading.Lock()

# 连接代际号。清理连接时递增，所有线程下次取连接时发现代际变了就重建。
#
# 为什么不能靠「试一次 execute 看连接是否已关闭」：sqlite3 禁止跨线程使用
# 连接，主线程对一份**仍打开**的连接执行 execute 会抛
#   ProgrammingError: SQLite objects created in a thread can only be used in
#   that same thread
# 而对一份**已关闭**的连接抛的是
#   ProgrammingError: Cannot operate on a closed database
# 两者异常类型相同，只有消息文本能区分——也就是说，在主线程用
# `pytest.raises(sqlite3.ProgrammingError)` 断言「连接已关闭」恒真，
# 测不出任何东西（这正是本工单早期版本里一条假通过的断言）。
# 代际号不依赖连接对象的内部状态，跨线程可靠。
_conn_generation = 0


def get_connection_generation() -> int:
    return _conn_generation

FREE_DAILY_SUMMARY_LIMIT = 3
MAX_PARSE_HISTORY_PER_USER = 30

# 两个额度上限都从环境变量读取，改配置不必发版。
# 各自独立取值：解析产出内容、追问消耗对话，用量节奏本就不同。
def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        # 配错时退回默认值，而不是让整个应用起不来——
        # 一个环境变量写错不该让整个站点不可用。
        return default


DAILY_PARSE_LIMIT = _env_int("VIDDIGEST_DAILY_PARSE_LIMIT", 3)
DAILY_CHAT_LIMIT = _env_int("VIDDIGEST_DAILY_CHAT_LIMIT", 10)

#: 额度种类 → (计数字段, 日期字段, 上限常量名)
#: 两个计数器在同一张 users 表上，但各占自己的列与日期字段。
_QUOTA_KINDS = {
    "parse": ("daily_parse_count", "last_parse_date", "DAILY_PARSE_LIMIT"),
    "chat": ("daily_chat_count", "last_chat_date", "DAILY_CHAT_LIMIT"),
}


def get_db_path():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return DB_PATH


def _prune_dead_threads_locked() -> None:
    """剔除已死线程的连接条目（调用方须已持锁）。

    线程死掉后，它的 threading.local 一并销毁，连接本可被 GC 回收；
    但 _open_conns 强引用着它，不剔除就会一直占着 OS 文件句柄。
    实测：200 个短命线程，不剔除时注册表滞留 201 条、句柄净增 401。

    sqlite3.Connection 不支持弱引用（weakref.WeakSet 抛 TypeError），
    只能靠「线程是否还活着」判断，threading.enumerate() 正好只列活线程。

    快路径比的是**身份**不是数量：曾用 `len(alive) == len(登记数)` 直接跳过，
    但死线程数与新活线程数相等时那两条判据会同时成立，死条目被漏掉
    （实测残留 2 条，每条 WAL 连接占 2~3 个句柄）。
    """
    alive = {t.ident for t in threading.enumerate()}
    if _open_conns_threads and alive.issuperset(_open_conns_threads):
        return  # 每条登记都属于活线程，无需逐一比对

    keep = [i for i, owner in enumerate(_open_conns_threads) if owner in alive]
    if len(keep) == len(_open_conns_threads):
        return

    _open_conns[:] = [_open_conns[i] for i in keep]
    _open_conns_threads[:] = [_open_conns_threads[i] for i in keep]


def open_connections():
    """当前进程内所有未关闭的数据库连接（跨线程）。

    仅供测试夹具使用：它需要清掉线程池工作线程持有的连接，
    而那些连接存在各自的 threading.local() 里，外部无法直接触达。
    """
    with _open_conns_lock:
        _prune_dead_threads_locked()
        return list(_open_conns)


def open_connection_threads():
    """与 open_connections() 同序的「创建线程 ident」列表。

    单独给出是因为「工作线程那份被登记了」这件事，光数数量证明不了：
    同一份连接被登记两次也能让计数达标。

    这里也调剔除——否则「与 open_connections() 同序」不总成立：
    先读 threads 再读 conns 时，中间若有线程死亡，两者长度就会不等。
    """
    with _open_conns_lock:
        _prune_dead_threads_locked()
        return list(_open_conns_threads)


def forget_all_connections():
    """关闭并清空全部已建立连接，同时递增代际号。

    sqlite3 的连接归创建它的线程所有，**主线程 close 不了工作线程那份**：
    会抛 ProgrammingError: objects created in a thread can only be used in
    that same thread。threading 又没有「按线程 ident 投递任务」的 API，
    因此工作线程的连接只能等它自己在下次 get_db() 时因代际号变化而丢弃，
    届时引用计数归零、连接被 GC 回收（sqlite3.Connection.__del__ 会 close）。

    所以这里对工作线程的连接不做 close，只清登记；对主线程自己的那份
    直接 close——那条是真能关掉的。跨线程 close 抛错被静默吞掉，
    等于一个都没关，假装关了才是更糟的错误。

    递增代际号解决的是「别再用旧连接」，与「立刻关掉旧连接」是两件事：
    前者是正确性保证（由测试守住），后者只是资源回收的尽力而为。
    """
    global _conn_generation
    current = threading.get_ident()
    with _open_conns_lock:
        _conn_generation += 1
        _prune_dead_threads_locked()
        owned_by_current = [
            conn for conn, owner in zip(_open_conns, _open_conns_threads)
            if owner == current
        ]
        _open_conns.clear()
        _open_conns_threads.clear()

    for conn in owned_by_current:
        try:
            conn.close()
        except sqlite3.Error:
            pass


@contextmanager
def get_db():
    """线程本地连接复用：WAL/外键等 PRAGMA 仅在新连接时设置一次，
    避免每次操作重建连接的开销（实测单次查询 155ms → 亚毫秒级）。"""
    with _open_conns_lock:
        generation = _conn_generation

    conn = getattr(_thread_local, "conn", None)
    if conn is not None and getattr(_thread_local, "generation", None) != generation:
        # 连接属于上一代（已被清理或换过库），本线程还拿着它。
        # 必须重建，否则工作线程会写进上一个测试的库。
        conn = None
        _thread_local.conn = None

    if conn is None:
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _thread_local.conn = conn
        _thread_local.generation = generation
        with _open_conns_lock:
            _prune_dead_threads_locked()
            _open_conns.append(conn)
            _open_conns_threads.append(threading.get_ident())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """初始化数据库表结构"""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_vip INTEGER DEFAULT 0,
                vip_expire_at TEXT,
                daily_summary_count INTEGER DEFAULT 0,
                last_summary_date TEXT,
                daily_parse_count INTEGER DEFAULT 0,
                last_parse_date TEXT,
                daily_chat_count INTEGER DEFAULT 0,
                last_chat_date TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT DEFAULT 'cny',
                status TEXT DEFAULT 'pending',
                plan_type TEXT DEFAULT 'monthly',
                stripe_session_id TEXT UNIQUE,
                stripe_payment_intent_id TEXT,
                paid_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);

            CREATE TABLE IF NOT EXISTS parse_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                video_url TEXT NOT NULL,
                video_title TEXT DEFAULT '',
                video_data TEXT DEFAULT '',
                summary_md TEXT DEFAULT '',
                mindmap_md TEXT DEFAULT '',
                subtitle_data TEXT DEFAULT '',
                chat_history TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_history_user ON parse_history(user_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_history_user_url ON parse_history(user_id, video_url);
        """)

        _migrate_quota_columns(conn)


def _migrate_quota_columns(conn) -> None:
    """给已存在的 users 表补上额度拆分需要的新列（expand 阶段）。

    为什么必须单独一步：`CREATE TABLE IF NOT EXISTS` 对已存在的表是空操作，
    老库里不会有新列，代码一读就报 no such column。

    逐列判断再 ALTER：SQLite 没有 `ADD COLUMN IF NOT EXISTS`，
    重复执行会抛 duplicate column name，所以先查列是否存在。
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    for column, ddl in (
        ("daily_parse_count", "ALTER TABLE users ADD COLUMN daily_parse_count INTEGER DEFAULT 0"),
        ("daily_chat_count", "ALTER TABLE users ADD COLUMN daily_chat_count INTEGER DEFAULT 0"),
        ("last_parse_date", "ALTER TABLE users ADD COLUMN last_parse_date TEXT"),
        ("last_chat_date", "ALTER TABLE users ADD COLUMN last_chat_date TEXT"),
    ):
        if column not in existing:
            conn.execute(ddl)


# ── 用户操作 ──────────────────────────────────────────────

def get_user_by_email(email: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_user(email: str, password_hash: str) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, password_hash),
        )
        return {"id": cursor.lastrowid, "email": email}


# ── AI 总结次数限制 ───────────────────────────────────────

def is_vip_active(user) -> bool:
    """VIP 权益判定——本项目唯一一份实现。

    naive datetime 兜底在此收口：所有 vip_expire_at 的比较都必须走这里，
    否则同一个"还是不是 VIP"的问题会在多处得到不同答案。
    """
    if not user["is_vip"] or not user["vip_expire_at"]:
        return False
    try:
        expire = datetime.fromisoformat(user["vip_expire_at"])
    except (ValueError, TypeError):
        return False
    # 兜底：SQLite 里存的可能是 naive datetime，统一按 UTC 处理
    if expire.tzinfo is None:
        expire = expire.replace(tzinfo=timezone.utc)
    return expire > datetime.now(timezone.utc)


# ── 额度拆分：解析额度 / 对话额度（工单 #4）────────────────
#
# 拆分前那套「单一共用额度」的实现（check_summary_quota /
# consume_summary_quota）已随路由层切换一并删除：全仓库再无调用者，
# 留着只会让人以为还有第二条计费路径。这里是唯一的计费实现。


def _quota_spec(kind: str) -> tuple[str, str, str]:
    """取出某类额度对应的 (计数字段, 日期字段, 上限常量名)。

    拼错的 kind 立刻抛错：静默 fallback 会让「扣了对话额度却记到解析头上」
    这类错误一路走到用户面前才发现。
    """
    spec = _QUOTA_KINDS.get(kind)
    if spec is None:
        raise ValueError(
            f"未知的额度类型 {kind!r}；可用：{sorted(_QUOTA_KINDS)}"
        )
    return spec


def quota_limit(kind: str) -> int:
    """按模块当前值取上限——而不是导入时冻结的常量。

    刻意不写成 `from DAILY_PARSE_LIMIT`：测试会 monkeypatch 模块属性，
    导入时冻结会让「改配置后行为随之改变」这条 AC 无法验证。

    公开是因为路由层要报同样的数字。import 时冻结的副本会和这里的
    remaining 打架，同一份 payload 报出 `remaining=0, limit=3`（真实上限 1）。
    """
    return globals()[_quota_spec(kind)[2]]


def check_quota_kind(user_id: int, kind: str) -> tuple[bool, int]:
    """判定单类额度。只读，不写库。返回 (allowed, remaining)，-1 表示无限。"""
    count_col, date_col, _ = _quota_spec(kind)
    limit = quota_limit(kind)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return False, 0
        if is_vip_active(user):
            return True, -1
        # 日期不是今天，说明今天还没用过，额度是满的
        if user[date_col] != today:
            return True, limit

        current = user[count_col] or 0
        if current >= limit:
            return False, 0
        return True, limit - current


def check_quota(user_id: int) -> dict:
    """一次判定全部额度。键固定为 parse / chat。"""
    return {kind: check_quota_kind(user_id, kind) for kind in _QUOTA_KINDS}


def consume_quota(user_id: int, kind: str) -> int:
    """扣减一次额度，返回扣减后的 remaining。调用前须已通过 check_quota_kind。"""
    count_col, date_col, _ = _quota_spec(kind)
    limit = quota_limit(kind)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return 0
        if is_vip_active(user):
            return -1

        if user[date_col] != today:
            conn.execute(
                f"UPDATE users SET {count_col} = 1, {date_col} = ? WHERE id = ?",
                (today, user_id),
            )
            return limit - 1

        current = user[count_col] or 0
        conn.execute(
            f"UPDATE users SET {count_col} = {count_col} + 1 WHERE id = ?",
            (user_id,),
        )
        return limit - current - 1


def refund_quota(user_id: int, kind: str) -> int:
    """把一次扣减还回去，返回回滚后的 remaining。

    用途：模型调用抛异常时不让用户白扣。扣减与调用之间没有事务，
    所以回滚本身是幂等且原子的（单条 UPDATE，不做读-改-写）：
    绝不会把计数压到负数，也不会跨天给今天白送额度，
    更不会把并发的另一次扣减覆盖掉。
    """
    count_col, date_col, _ = _quota_spec(kind)
    limit = quota_limit(kind)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return 0
        if is_vip_active(user):
            return -1
        # 跨天了：扣的是昨天的，回滚会让今天的额度凭空多出来，不做。
        if user[date_col] != today:
            return limit

        # 单条 UPDATE 里完成减一。读-改-写两步会和并发的 consume_quota
        # 交叉：中间那次扣减会被本处的绝对值覆盖掉，等于白送额度。
        conn.execute(
            f"UPDATE users SET {count_col} = MAX(COALESCE({count_col}, 0) - 1, 0) "
            f"WHERE id = ?",
            (user_id,),
        )
        row = conn.execute(
            f"SELECT {count_col} FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return limit - (row[count_col] or 0)


# ── 订单操作 ──────────────────────────────────────────────

def create_order(user_id: int, order_no: str, amount: int, currency: str = "cny", plan_type: str = "monthly"):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO orders (order_no, user_id, amount, currency, plan_type)
               VALUES (?, ?, ?, ?, ?)""",
            (order_no, user_id, amount, currency, plan_type),
        )


def update_order_stripe_session(order_no: str, session_id: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE orders SET stripe_session_id = ? WHERE order_no = ?",
            (session_id, order_no),
        )


def complete_order(session_id: str, payment_intent_id: str) -> dict | None:
    """支付完成时更新订单状态、激活 VIP。幂等性：只有 pending 状态的订单才会被处理。"""
    from dateutil.relativedelta import relativedelta

    with get_db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE stripe_session_id = ? AND status = 'pending'",
            (session_id,),
        ).fetchone()

        if not order:
            return None

        now = datetime.now(timezone.utc).isoformat()
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?", (order["user_id"],)
        ).fetchone()

        current_expire = None
        if user["vip_expire_at"]:
            try:
                current_expire = datetime.fromisoformat(user["vip_expire_at"])
                # 兜底 naive datetime（与 check_and_increment_summary 同源修复）
                if current_expire.tzinfo is None:
                    current_expire = current_expire.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        base_time = datetime.now(timezone.utc)
        if current_expire and current_expire > base_time:
            base_time = current_expire

        if order["plan_type"] == "monthly":
            new_expire = base_time + relativedelta(months=1)
        elif order["plan_type"] == "yearly":
            new_expire = base_time + relativedelta(years=1)
        else:
            new_expire = base_time + relativedelta(months=1)

        conn.execute(
            "UPDATE orders SET status='paid', stripe_payment_intent_id=?, paid_at=?, updated_at=? WHERE id=?",
            (payment_intent_id, now, now, order["id"]),
        )
        conn.execute(
            "UPDATE users SET is_vip=1, vip_expire_at=?, updated_at=? WHERE id=?",
            (new_expire.isoformat(), now, order["user_id"]),
        )

        return dict(order)


def get_user_orders(user_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── 解析历史记录 ──────────────────────────────────────────

def _trim_parse_history(conn, user_id: int):
    """每用户只保留最近 MAX_PARSE_HISTORY_PER_USER 条记录"""
    conn.execute(
        f"""DELETE FROM parse_history WHERE user_id = ? AND id NOT IN (
            SELECT id FROM parse_history WHERE user_id = ?
            ORDER BY COALESCE(updated_at, created_at) DESC
            LIMIT {int(MAX_PARSE_HISTORY_PER_USER)})""",
        (user_id, user_id),
    )


def upsert_parse_history(
    user_id: int,
    video_url: str,
    video_title: str = "",
    video_data: dict | None = None,
    summary_md: str = "",
    mindmap_md: str = "",
    subtitle_data: dict | None = None,
) -> int:
    """按 (user_id, video_url) 去重保存解析历史，返回记录 ID。

    合并语义：新值为空时保留旧值（视频源保存不覆盖 AI 结果，反之亦然），
    非空新值覆盖旧值。查询走 idx_history_user_url 复合索引。
    """
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, video_title, video_data, summary_md, mindmap_md, subtitle_data"
            " FROM parse_history WHERE user_id = ? AND video_url = ?",
            (user_id, video_url),
        ).fetchone()

        def _merge(old_json: str, new_obj: dict | None) -> str:
            new_json = json.dumps(new_obj, ensure_ascii=False) if new_obj else ""
            return new_json or (old_json or "")

        def _merge_text(old: str, new: str) -> str:
            return new or (old or "")

        if row:
            conn.execute(
                """UPDATE parse_history
                   SET video_title=?, video_data=?, summary_md=?, mindmap_md=?,
                       subtitle_data=?, updated_at=?
                   WHERE id=?""",
                (
                    _merge_text(row["video_title"], video_title),
                    _merge(row["video_data"], video_data),
                    _merge_text(row["summary_md"], summary_md),
                    _merge_text(row["mindmap_md"], mindmap_md),
                    _merge(row["subtitle_data"], subtitle_data),
                    now, row["id"],
                ),
            )
            history_id = row["id"]
        else:
            cursor = conn.execute(
                """INSERT INTO parse_history
                   (user_id, video_url, video_title, video_data, summary_md,
                    mindmap_md, subtitle_data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, video_url, video_title,
                 json.dumps(video_data, ensure_ascii=False) if video_data else "",
                 summary_md, mindmap_md,
                 json.dumps(subtitle_data, ensure_ascii=False) if subtitle_data else "",
                 now, now),
            )
            history_id = cursor.lastrowid
        _trim_parse_history(conn, user_id)
        return history_id


def append_chat_history(user_id: int, video_url: str, question: str, answer: str) -> bool:
    """向指定视频的解析历史追加一条问答；该视频无历史记录时自动创建占位记录"""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, chat_history FROM parse_history WHERE user_id = ? AND video_url = ?",
            (user_id, video_url),
        ).fetchone()
        if row:
            try:
                chats = json.loads(row["chat_history"] or "[]")
            except (ValueError, TypeError):
                chats = []
            chats.append({"question": question, "answer": answer})
            conn.execute(
                "UPDATE parse_history SET chat_history=?, updated_at=? WHERE id=?",
                (json.dumps(chats, ensure_ascii=False), now, row["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO parse_history
                   (user_id, video_url, chat_history, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, video_url,
                 json.dumps([{"question": question, "answer": answer}], ensure_ascii=False),
                 now, now),
            )
        _trim_parse_history(conn, user_id)
    return True


def get_parse_histories(user_id: int, limit: int = MAX_PARSE_HISTORY_PER_USER) -> list:
    """历史记录列表（不含大字段，含摘要预览）"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, video_url, video_title, summary_md,
                      COALESCE(updated_at, created_at) AS updated_at,
                      created_at,
                      (chat_history IS NOT NULL AND chat_history != '[]') AS has_chat
               FROM parse_history WHERE user_id = ?
               ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        items = []
        for r in rows:
            item = dict(r)
            has_chat = bool(item.get("has_chat"))
            summary_md = item.pop("summary_md") or ""
            item["summary_preview"] = summary_md.strip()[:120]
            item["has_chat"] = has_chat
            item["has_ai_result"] = has_chat or bool(item["summary_preview"])
            items.append(item)
        return items


def get_parse_history_detail(user_id: int, history_id: int) -> dict | None:
    """单条历史记录完整内容（JSON 字段已反序列化）"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM parse_history WHERE user_id = ? AND id = ?",
            (user_id, history_id),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        for field in ("video_data", "subtitle_data"):
            try:
                item[field] = json.loads(item[field]) if item[field] else None
            except (ValueError, TypeError):
                item[field] = None
        try:
            item["chat_history"] = json.loads(item["chat_history"] or "[]")
        except (ValueError, TypeError):
            item["chat_history"] = []
        return item


def get_parse_history_by_url(user_id: int, video_url: str) -> dict | None:
    """按视频 URL 查历史记录（解析复用缓存的热点路径，走 idx_history_user_url 索引）"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM parse_history WHERE user_id = ? AND video_url = ?",
            (user_id, video_url),
        ).fetchone()
        if not row:
            return None
    return get_parse_history_detail(user_id, row["id"])


def delete_parse_history(user_id: int, history_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM parse_history WHERE user_id = ? AND id = ?",
            (user_id, history_id),
        )
        return cursor.rowcount > 0
