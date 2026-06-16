"""
SQLite 数据库模型和初始化。
users / verification_codes 两张表。
"""

import sqlite3
import os
import random
import string
from datetime import datetime, timedelta
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent / "data"
DB_DIR.mkdir(exist_ok=True)
DB_PATH = str(DB_DIR / "app.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """初始化数据库表结构。"""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                account_no TEXT NOT NULL UNIQUE,
                plan TEXT NOT NULL DEFAULT 'none',
                plan_expires_at TEXT,
                usage_count INTEGER NOT NULL DEFAULT 0,
                max_usage INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                note TEXT NOT NULL DEFAULT '',
                initial_password TEXT NOT NULL DEFAULT '',
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS verification_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                code TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_vc_email ON verification_codes(email);
        """)
        conn.commit()
    finally:
        conn.close()


def migrate_db():
    """迁移旧数据库：添加新字段（如果不存在）。"""
    conn = get_connection()
    try:
        # 检查列是否存在
        cursor = conn.execute("PRAGMA table_info(users)")
        existing_cols = {row["name"] for row in cursor.fetchall()}

        new_cols = {
            "usage_count": "INTEGER NOT NULL DEFAULT 0",
            "max_usage": "INTEGER NOT NULL DEFAULT 0",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "note": "TEXT NOT NULL DEFAULT ''",
            "initial_password": "TEXT NOT NULL DEFAULT ''",
            "is_admin": "INTEGER NOT NULL DEFAULT 0",
        }
        for col_name, col_def in new_cols.items():
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")

        # 检查 payment_orders 表是否存在，存在则删除
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='payment_orders'")
        if cursor.fetchone():
            conn.execute("DROP TABLE IF EXISTS payment_orders")

        conn.commit()
    finally:
        conn.close()


def generate_account_no() -> str:
    """生成唯一账号：8位数字（日期+序号）。"""
    today = datetime.now().strftime("%m%d")
    seq = random.randint(1000, 9999)
    return f"{today}{seq}"


def generate_initial_password(length: int = 8) -> str:
    """生成随机初始密码：字母+数字组合。"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


# ============================================================
# User 操作
# ============================================================

def create_user(email: str, password_hash: str, initial_password: str = "",
                plan: str = "none", plan_expires_at: str = "",
                max_usage: int = 0, note: str = "") -> dict:
    conn = get_connection()
    try:
        # account_no 与 email 相同，前端只认邮箱
        account_no = email

        cursor = conn.execute(
            """INSERT INTO users (email, password_hash, account_no, initial_password,
               plan, plan_expires_at, max_usage, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (email, password_hash, account_no, initial_password,
             plan, plan_expires_at, max_usage, note),
        )
        conn.commit()
        user_id = cursor.lastrowid
        return {"id": user_id, "email": email, "account_no": account_no, "plan": plan}
    except sqlite3.IntegrityError:
        raise ValueError("该邮箱已存在")
    finally:
        conn.close()


def get_user_by_email(email: str):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_users():
    """获取所有用户列表。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_user_plan(user_id: int, plan: str, days: int = 30):
    """更新套餐，设置有效期。"""
    expires = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET plan=?, plan_expires_at=? WHERE id=?",
            (plan, expires, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_user_password(email: str, password_hash: str, initial_password: str = ""):
    """更新用户密码。如果提供了 initial_password，同时更新初始密码字段。"""
    conn = get_connection()
    try:
        if initial_password:
            conn.execute(
                "UPDATE users SET password_hash=?, initial_password=? WHERE email=?",
                (password_hash, initial_password, email),
            )
        else:
            conn.execute(
                "UPDATE users SET password_hash=? WHERE email=?",
                (password_hash, email),
            )
        conn.commit()
    finally:
        conn.close()


def increment_usage(user_id: int):
    """增加用户使用次数。"""
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET usage_count = usage_count + 1 WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def toggle_user_status(user_id: int) -> str:
    """切换用户启用/禁用状态，返回新状态。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT status FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        new_status = "disabled" if row["status"] == "active" else "active"
        conn.execute("UPDATE users SET status=? WHERE id=?", (new_status, user_id))
        conn.commit()
        return new_status
    finally:
        conn.close()


def set_first_user_as_admin():
    """将第一个用户设为管理员。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
        if row:
            conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (row["id"],))
            conn.commit()
    finally:
        conn.close()


# ============================================================
# 验证码操作
# ============================================================

def save_verification_code(email: str, code: str):
    """保存验证码，有效期1分钟。"""
    expires = (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO verification_codes (email, code, expires_at) VALUES (?, ?, ?)",
            (email, code, expires),
        )
        conn.commit()
    finally:
        conn.close()


def verify_code(email: str, code: str) -> bool:
    """校验验证码：查找未使用、未过期、匹配的验证码。"""
    conn = get_connection()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            """SELECT id FROM verification_codes
               WHERE email=? AND code=? AND used=0 AND expires_at>?
               ORDER BY id DESC LIMIT 1""",
            (email, code, now),
        ).fetchone()
        if row:
            conn.execute("UPDATE verification_codes SET used=1 WHERE id=?", (row["id"],))
            conn.commit()
            return True
        return False
    finally:
        conn.close()


def can_send_code(email: str) -> bool:
    """检查60秒内是否已发送过验证码。"""
    conn = get_connection()
    try:
        since = (datetime.now() - timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT id FROM verification_codes WHERE email=? AND created_at>? LIMIT 1",
            (email, since),
        ).fetchone()
        return row is None
    finally:
        conn.close()


# 启动时初始化
init_db()
migrate_db()
set_first_user_as_admin()
