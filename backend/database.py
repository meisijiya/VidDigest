import os
import json
import sqlite3
import threading
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "app.db")

# 线程本地连接缓存（uvicorn 线程池复用线程，连接随之复用）
_thread_local = threading.local()

FREE_DAILY_SUMMARY_LIMIT = 3
MAX_PARSE_HISTORY_PER_USER = 30


def get_db_path():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return DB_PATH


@contextmanager
def get_db():
    """线程本地连接复用：WAL/外键等 PRAGMA 仅在新连接时设置一次，
    避免每次操作重建连接的开销（实测单次查询 155ms → 亚毫秒级）。"""
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _thread_local.conn = conn
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

def check_and_increment_summary(user_id: int) -> tuple[bool, int]:
    """
    检查用户是否可以使用 AI 总结，并自增计数。
    返回 (allowed, remaining_count)
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return False, 0

        if user["is_vip"] and user["vip_expire_at"]:
            expire = datetime.fromisoformat(user["vip_expire_at"])
            # 兜底：SQLite 里存的可能是 naive datetime，统一按 UTC 处理
            if expire.tzinfo is None:
                expire = expire.replace(tzinfo=timezone.utc)
            if expire > datetime.now(timezone.utc):
                return True, -1

        if user["last_summary_date"] != today:
            conn.execute(
                "UPDATE users SET daily_summary_count = 1, last_summary_date = ? WHERE id = ?",
                (today, user_id),
            )
            return True, FREE_DAILY_SUMMARY_LIMIT - 1

        current = user["daily_summary_count"]
        if current >= FREE_DAILY_SUMMARY_LIMIT:
            return False, 0

        conn.execute(
            "UPDATE users SET daily_summary_count = daily_summary_count + 1 WHERE id = ?",
            (user_id,),
        )
        return True, FREE_DAILY_SUMMARY_LIMIT - current - 1


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
