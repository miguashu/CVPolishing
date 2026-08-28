# -*- coding: utf-8 -*-
"""一次性初始化脚本：建库建表、种子账号、迁移现有长期记忆给 liwenhao。

运行：python init_db.py
- 创建数据库 cvpolishing 与 users / memories / resumes 三张表
- 种子管理员 admin（admin123!）与普通用户 liwenhao（146641）
- 把现有 data/memory.json 的全部长期记忆迁移到 liwenhao 名下（仅首次，已存在则跳过）
"""
import json
import os

import db
import user_ctx
from memory import get_all, put


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def seed_users():
    if not db.get_user_by_username("admin"):
        db.create_user("admin", "admin123!", "admin")
        print("[init] 已创建管理员账号 admin")
    else:
        print("[init] 管理员 admin 已存在，跳过")
    if not db.get_user_by_username("liwenhao"):
        db.create_user("liwenhao", "146641", "user")
        print("[init] 已创建用户 liwenhao")
    else:
        print("[init] 用户 liwenhao 已存在，跳过")


def migrate_memory():
    """把现有 data/memory.json 的 118 条长期记忆迁移给 liwenhao（首次运行）。"""
    path = os.path.join(BASE_DIR, "data", "memory.json")
    if not os.path.exists(path):
        print("[init] 无现有记忆文件，跳过迁移")
        return
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print("[init] 记忆文件读取失败，跳过：", e)
        return
    if not isinstance(data, dict):
        print("[init] 记忆文件格式异常，跳过")
        return

    user_ctx.set_user_id("liwenhao")
    if get_all():
        print("[init] liwenhao 名下已有长期记忆，跳过迁移")
        return

    n = 0
    for k, v in data.items():
        answer = v.get("answer", "") if isinstance(v, dict) else ""
        extra = v.get("extra", "") if isinstance(v, dict) else ""
        if put(k, answer, extra):
            n += 1
    print(f"[init] 已迁移 {n} 条长期记忆给 liwenhao（正文入 MySQL，向量已同步写入 Qdrant）")


if __name__ == "__main__":
    db.init_schema()
    print("[init] 库表已就绪")
    seed_users()
    migrate_memory()
    print("[init] 初始化完成")
