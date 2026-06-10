"""
SQLite 数据库模型和初始化。
users / verification_codes / payment_orders 三张表。
"""

import sqlite3
import os
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

            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                payment_method TEXT NOT NULL DEFAULT 'wechat',
                qr_code_path TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                paid_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_vc_email ON verification_codes(email);
            CREATE INDEX IF NOT EXISTS idx_orders_user ON payment_orders(user_id);
        """)
        conn.commit()
    finally:
        conn.close()


def generate_account_no() -> str:
    """生成唯一账号：8位数字（日期+序号）。"""
    import random
    today = datetime.now().strftime("%m%d")
    seq = random.randint(1000, 9999)
    return f"{today}{seq}"


# ============================================================
# User 操作
# ============================================================

def create_user(email: str, password_hash: str) -> dict:
    conn = get_connection()
    try:
        account_no = generate_account_no()
        # 确保唯一
        while True:
            existing = conn.execute(
                "SELECT id FROM users WHERE account_no=?", (account_no,)
            ).fetchone()
            if not existing:
                break
            account_no = generate_account_no()

        cursor = conn.execute(
            "INSERT INTO users (email, password_hash, account_no) VALUES (?, ?, ?)",
            (email, password_hash, account_no),
        )
        conn.commit()
        user_id = cursor.lastrowid
        return {"id": user_id, "email": email, "account_no": account_no, "plan": "none"}
    except sqlite3.IntegrityError:
        raise ValueError("该邮箱已注册")
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


def update_user_plan(user_id: int, plan: str):
    """开通套餐，有效期30天。"""
    expires = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET plan=?, plan_expires_at=? WHERE id=?",
            (plan, expires, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_user_password(email: str, password_hash: str):
    """更新用户密码。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET password_hash=? WHERE email=?",
            (password_hash, email),
        )
        conn.commit()
    finally:
        conn.close()


# ============================================================
# 验证码操作
# ============================================================

def save_verification_code(email: str, code: str):
    """保存验证码，有效期5分钟。"""
    expires = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
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
            # 标记为已使用
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


# ============================================================
# 支付订单操作
# ============================================================

def create_payment_order(user_id: int, plan: str, amount: int, method: str, qr_path: str = "") -> dict:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO payment_orders (user_id, plan, amount, payment_method, qr_code_path) VALUES (?, ?, ?, ?, ?)",
            (user_id, plan, amount, method, qr_path),
        )
        conn.commit()
        order_id = cursor.lastrowid
        return {"id": order_id, "user_id": user_id, "plan": plan, "amount": amount, "status": "pending"}
    finally:
        conn.close()


def get_payment_order(order_id: int):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM payment_orders WHERE id=?", (order_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def mark_order_paid(order_id: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE payment_orders SET status='paid', paid_at=? WHERE id=?",
            (now, order_id),
        )
        conn.commit()
    finally:
        conn.close()


# 启动时初始化
init_db()
