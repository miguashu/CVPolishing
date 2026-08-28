# -*- coding: utf-8 -*-
"""MySQL 数据访问层：用户 / 长期记忆 / 简历。

数据库：cvpolishing（MySQL 5.7，utf8mb4）。
表：
  users    用户账号（username 唯一，role=admin/user）
  memories 长期记忆（面试题库），按 user_id 隔离，带操作时间（created_at/updated_at）
  resumes  简历（原文/优化后/JD），按 user_id 隔离
"""
import json
from datetime import datetime, timedelta

import pymysql
import pymysql.cursors
from werkzeug.security import generate_password_hash, check_password_hash

import config


def connect(db_name=None):
    """返回一个 pymysql 连接（DictCursor，自动提交）。调用方负责关闭。"""
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=db_name or config.DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


# ---------------- 建库建表 ----------------
def init_schema():
    # 先连到 mysql 系统库建数据库（CREATE DATABASE 不能带 USE）
    conn = pymysql.connect(
        host=config.DB_HOST, port=config.DB_PORT,
        user=config.DB_USER, password=config.DB_PASSWORD,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as c:
            c.execute(
                "CREATE DATABASE IF NOT EXISTS `{}` "
                "DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci".format(config.DB_NAME)
            )
    finally:
        conn.close()

    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS users (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  username VARCHAR(64) NOT NULL,
                  password_hash VARCHAR(255) NOT NULL,
                  role VARCHAR(16) NOT NULL DEFAULT 'user',
                  is_vip TINYINT NOT NULL DEFAULT 0,
                  vip_expire DATETIME DEFAULT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY uk_username (username)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            # 兼容旧库：补充 VIP 字段（已存在则跳过）
            c.execute("SHOW COLUMNS FROM users LIKE 'is_vip'")
            if not c.fetchone():
                c.execute("ALTER TABLE users ADD COLUMN is_vip TINYINT NOT NULL DEFAULT 0")
            c.execute("SHOW COLUMNS FROM users LIKE 'vip_expire'")
            if not c.fetchone():
                c.execute("ALTER TABLE users ADD COLUMN vip_expire DATETIME DEFAULT NULL")
            c.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  user_id VARCHAR(64) NOT NULL,
                  mem_key TEXT NOT NULL,
                  answer MEDIUMTEXT,
                  extra MEDIUMTEXT,
                  embedding MEDIUMTEXT,
                  seq INT NOT NULL DEFAULT 0,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY uk_user_key (user_id, mem_key(255))
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS resumes (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  user_id VARCHAR(64) NOT NULL,
                  original MEDIUMTEXT,
                  optimized MEDIUMTEXT,
                  jd MEDIUMTEXT,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  UNIQUE KEY uk_user (user_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            # 普通用户基础优化次数统计：达上限后提醒开通 VIP（会员不限）
            c.execute("""
                CREATE TABLE IF NOT EXISTS optimize_counts (
                  username VARCHAR(64) NOT NULL,
                  cnt INT NOT NULL DEFAULT 0,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  PRIMARY KEY (username)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            # 服务端会话表：多实例共享登录态（cookie 仅持有签名 session id）
            c.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                  sid VARCHAR(64) NOT NULL,
                  data MEDIUMTEXT NOT NULL,
                  expires_at DATETIME NOT NULL,
                  PRIMARY KEY (sid),
                  KEY idx_expires (expires_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            # 专属邀请码表：每个码可绑定用户、设有效期与开通天数，用过即失效
            c.execute("""
                CREATE TABLE IF NOT EXISTS invite_codes (
                  code VARCHAR(64) NOT NULL,
                  owner VARCHAR(64) NOT NULL,
                  target_user VARCHAR(64) DEFAULT NULL,
                  vip_days INT NOT NULL DEFAULT 0,
                  expires_at DATETIME DEFAULT NULL,
                  used_by VARCHAR(64) DEFAULT NULL,
                  used_at DATETIME DEFAULT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (code),
                  KEY idx_owner (owner),
                  KEY idx_target (target_user)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
            # 支付订单表：支付宝 wap 支付下单与回调闭环
            c.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                  out_trade_no VARCHAR(64) NOT NULL,
                  username VARCHAR(64) NOT NULL,
                  product_key VARCHAR(32) NOT NULL,
                  amount DECIMAL(10,2) NOT NULL,
                  subject VARCHAR(128) NOT NULL,
                  status TINYINT NOT NULL DEFAULT 0,
                  trade_no VARCHAR(64) DEFAULT NULL,
                  raw_notify MEDIUMTEXT,
                  paid_at DATETIME DEFAULT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (out_trade_no),
                  KEY idx_username (username),
                  KEY idx_status (status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
    finally:
        conn.close()


# ---------------- 用户 ----------------
def get_user_by_username(username):
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT id, username, password_hash, role, is_vip, vip_expire, created_at FROM users WHERE username=%s", (username,))
            return c.fetchone()
    finally:
        conn.close()


def verify_password(username, password):
    u = get_user_by_username(username)
    if not u:
        return False, None
    if check_password_hash(u["password_hash"], password):
        return True, u
    return False, None


def create_user(username, password, role="user"):
    pw = generate_password_hash(password, method="pbkdf2:sha256")
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)", (username, pw, role))
    finally:
        conn.close()


def list_users():
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT id, username, role, is_vip, vip_expire, created_at FROM users ORDER BY id")
            return c.fetchall()
    finally:
        conn.close()


# ---------------- VIP / 会员 ----------------
def is_vip_active(username):
    """会员是否在有效期内（is_vip=1 且未过期；vip_expire 为空表示永久）。"""
    u = get_user_by_username(username)
    if not u or not u.get("is_vip"):
        return False
    expire = u.get("vip_expire")
    if expire is None:
        return True
    if isinstance(expire, str):
        try:
            expire = datetime.strptime(expire, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return True
    return expire > datetime.now()


def get_vip_info(username):
    """返回会员信息：is_vip(bool) / expire(字符串或 None) / active(bool)。"""
    u = get_user_by_username(username)
    if not u:
        return {"is_vip": False, "expire": None, "active": False}
    expire = u.get("vip_expire")
    expire_str = expire.strftime("%Y-%m-%d %H:%M:%S") if hasattr(expire, "strftime") else (expire if isinstance(expire, str) else None)
    return {"is_vip": bool(u.get("is_vip")), "expire": expire_str, "active": is_vip_active(username)}


def set_vip(username, active, days=None):
    """设置会员状态：active=True 开通（days 指定有效期天数，None 为永久）；active=False 取消。"""
    conn = connect()
    try:
        with conn.cursor() as c:
            if active:
                expire = (datetime.now() + timedelta(days=int(days))) if days else None
                c.execute("UPDATE users SET is_vip=1, vip_expire=%s WHERE username=%s", (expire, username))
            else:
                c.execute("UPDATE users SET is_vip=0, vip_expire=NULL WHERE username=%s", (username,))
    finally:
        conn.close()
    return get_vip_info(username)


# ---------------- 优化次数统计（普通用户免费上限） ----------------
def get_optimize_count(username):
    """返回普通用户已使用的基础优化次数。"""
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT cnt FROM optimize_counts WHERE username=%s", (username,))
            row = c.fetchone()
            return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def inc_optimize_count(username):
    """普通用户完成一次基础优化后自增计数（不存在则插入）。"""
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO optimize_counts (username, cnt) VALUES (%s, 1) "
                "ON DUPLICATE KEY UPDATE cnt = cnt + 1",
                (username,),
            )
            conn.commit()
    finally:
        conn.close()


def delete_user(username):
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM memories WHERE user_id=%s", (username,))
            c.execute("DELETE FROM resumes WHERE user_id=%s", (username,))
            c.execute("DELETE FROM users WHERE username=%s", (username,))
    finally:
        conn.close()


def reset_password(username, new_password):
    pw = generate_password_hash(new_password, method="pbkdf2:sha256")
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE users SET password_hash=%s WHERE username=%s", (pw, username))
    finally:
        conn.close()


def count_users():
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) AS n FROM users")
            return c.fetchone()["n"]
    finally:
        conn.close()


# ---------------- 简历（按用户隔离） ----------------
def get_resume(username):
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT original, optimized, jd FROM resumes WHERE user_id=%s", (username,))
            return c.fetchone()
    finally:
        conn.close()


def save_resume(username, original, optimized, jd):
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO resumes (user_id, original, optimized, jd) VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE original=VALUES(original), optimized=VALUES(optimized), jd=VALUES(jd)
            """, (username, original, optimized, jd))
    finally:
        conn.close()


# ---------------- 服务端会话（多实例共享登录态） ----------------
def save_session(sid, data, expires_at):
    """写入/更新一条会话记录。data 为 JSON 字符串。"""
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO sessions (sid, data, expires_at) VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE data=VALUES(data), expires_at=VALUES(expires_at)
            """, (sid, data, expires_at))
    finally:
        conn.close()


def load_session(sid):
    """读取会话：返回 (data_json, expires_at)；过期或不存在返回 (None, None)。"""
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT data, expires_at FROM sessions WHERE sid=%s", (sid,))
            row = c.fetchone()
            if not row:
                return None, None
            expire = row["expires_at"]
            if isinstance(expire, str):
                try:
                    expire = datetime.strptime(expire, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    expire = None
            if expire is not None and expire <= datetime.now():
                delete_session(sid)
                return None, None
            return row["data"], expire
    finally:
        conn.close()


def delete_session(sid):
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM sessions WHERE sid=%s", (sid,))
    finally:
        conn.close()


def purge_expired_sessions():
    """清理过期会话（可定时任务调用）。"""
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM sessions WHERE expires_at <= %s", (datetime.now(),))
    finally:
        conn.close()


# ---------------- 专属邀请码 ----------------
def create_invite_code(owner, target_user=None, vip_days=0, expires_hours=None):
    """生成一条专属邀请码。返回 code 字符串。"""
    import secrets
    code = "VIP-" + secrets.token_hex(8).upper()
    expire = (datetime.now() + timedelta(hours=int(expires_hours))) if expires_hours else None
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO invite_codes (code, owner, target_user, vip_days, expires_at) VALUES (%s, %s, %s, %s, %s)",
                (code, owner, target_user, int(vip_days), expire),
            )
    finally:
        conn.close()
    return code


def list_invite_codes(owner=None):
    conn = connect()
    try:
        with conn.cursor() as c:
            if owner:
                c.execute("SELECT * FROM invite_codes WHERE owner=%s ORDER BY created_at DESC", (owner,))
            else:
                c.execute("SELECT * FROM invite_codes ORDER BY created_at DESC")
            return c.fetchall()
    finally:
        conn.close()


def get_invite_code(code):
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT * FROM invite_codes WHERE code=%s", (code,))
            return c.fetchone()
    finally:
        conn.close()


def consume_invite_code(code, used_by):
    """标记邀请码已使用。"""
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute(
                "UPDATE invite_codes SET used_by=%s, used_at=%s WHERE code=%s",
                (used_by, datetime.now(), code),
            )
    finally:
        conn.close()


def delete_invite_code(code):
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM invite_codes WHERE code=%s", (code,))
    finally:
        conn.close()


# ---------------- 支付订单（支付宝 wap 支付） ----------------
def create_order(out_trade_no, username, product_key, amount, subject):
    """创建一条待支付订单（status=0）。"""
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO orders (out_trade_no, username, product_key, amount, subject, status) "
                "VALUES (%s, %s, %s, %s, %s, 0)",
                (out_trade_no, username, product_key, amount, subject),
            )
    finally:
        conn.close()


def get_order(out_trade_no):
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT * FROM orders WHERE out_trade_no=%s", (out_trade_no,))
            return c.fetchone()
    finally:
        conn.close()


def mark_order_paid(out_trade_no, trade_no, raw_notify):
    """标记订单已支付（status=1）并写入支付宝流水号与原始报文。
    幂等：已支付（status=1）直接返回当前订单，避免重复开通会员。
    """
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT * FROM orders WHERE out_trade_no=%s", (out_trade_no,))
            order = c.fetchone()
            if not order:
                return None
            if order["status"] == 1:
                return order
            c.execute(
                "UPDATE orders SET status=1, trade_no=%s, raw_notify=%s, paid_at=%s "
                "WHERE out_trade_no=%s",
                (trade_no, raw_notify, datetime.now(), out_trade_no),
            )
        order["status"] = 1
        order["trade_no"] = trade_no
        return order
    finally:
        conn.close()


def close_order(out_trade_no):
    """关闭超时未付订单（status=2）。"""
    conn = connect()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE orders SET status=2 WHERE out_trade_no=%s AND status=0", (out_trade_no,))
    finally:
        conn.close()
