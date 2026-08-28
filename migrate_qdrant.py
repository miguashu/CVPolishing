# -*- coding: utf-8 -*-
"""一次性迁移脚本：把 MySQL memories.embedding 里的旧向量直接搬进 Qdrant。

策略：
- 旧向量存在且维度与当前嵌入模型一致 → 直接 upsert（不重新嵌入，省时省资源）
- 旧向量缺失 / 维度不符 → 用当前模型重新嵌入后 upsert
运行：python migrate_qdrant.py（需 Qdrant 已启动、Ollama 已运行）
"""
import json

import db
import qdrant_store
from embed import embed


def main():
    print("[migrate] 开始迁移长期记忆向量到 Qdrant ...")
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id, mem_key, answer, seq, embedding FROM memories")
            rows = c.fetchall()
    finally:
        conn.close()
    print(f"[migrate] MySQL 共 {len(rows)} 条记忆")

    if not rows:
        return
    qdrant_store.ensure_collection()
    try:
        dim = len(embed(["维度探测"])[0])
    except Exception as e:
        print(f"[migrate] 无法探测嵌入维度：{e}")
        return

    reused = 0
    recomputed = 0
    failed = 0
    for r in rows:
        vec = None
        if r["embedding"]:
            try:
                v = json.loads(r["embedding"])
                if isinstance(v, list) and len(v) == dim:
                    vec = v
            except Exception:
                vec = None
        if vec is not None:
            reused += 1
        else:
            try:
                vec = embed([(r["mem_key"] or "") + "\n" + (r["answer"] or "")])[0]
                recomputed += 1
            except Exception as e:
                print(f"[migrate] 重新嵌入失败 {r['mem_key']!r}: {e}")
                failed += 1
                continue
        qdrant_store.upsert(r["user_id"], r["mem_key"], vec, r["seq"])

    print(f"[migrate] 完成：复用旧向量 {reused} 条，重新嵌入 {recomputed} 条，失败 {failed} 条")
    print(f"[migrate] Qdrant 集合 {qdrant_store.QDRANT_COLLECTION} 当前点数：{qdrant_store.count()}")


if __name__ == "__main__":
    main()
