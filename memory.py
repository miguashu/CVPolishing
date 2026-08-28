# -*- coding: utf-8 -*-
"""长期记忆存储（MySQL + Qdrant 版）：面试题与答案，按 user_id 隔离，带操作时间。

对外函数签名与旧文件版保持一致，app.py 无需改动调用方式：
  get_all / put / delete / clear / search / search_hybrid / rebuild_vectors

分层：
- 正文（mem_key/answer/extra/seq）存 MySQL memories 表：关键词检索 / 用户隔离 / 管理接口不变。
- 语义向量存 Qdrant（qdrant_store）：保存时 upsert 向量点，答疑时原生 ANN 召回 top-K。
created_at / updated_at 由 MySQL 自动维护（操作时间）。
"""
import user_ctx
from config import VECTOR_TOP_K
from embed import embed
import db
import qdrant_store


def _uid():
    return user_ctx.get_user_id()


def _embed_text(text):
    """对单条文本做嵌入，失败时返回 None（不阻断保存，仅该条不参与语义检索）。"""
    try:
        return embed([text[:2000]])[0]
    except Exception:
        return None


def get_all():
    """返回当前用户的全部记忆条目，按加入序号 seq 倒序。"""
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT mem_key, answer, extra, seq FROM memories WHERE user_id=%s ORDER BY seq DESC",
                (_uid(),),
            )
            rows = c.fetchall()
    finally:
        conn.close()
    return {r["mem_key"]: {"answer": r["answer"] or "", "extra": r["extra"] or "", "_seq": r["seq"], "_ts": 0} for r in rows}


def put(key, answer, extra=""):
    """写入 / 覆盖一条记忆（当前用户）。key 为题目标题。正文入 MySQL，向量 upsert 进 Qdrant。"""
    if not key or not key.strip():
        return False
    key = key.strip()
    answer = (answer or "").strip()
    extra = (extra or "").strip()
    uid = _uid()
    vec = _embed_text(key + "\n" + answer)
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT COALESCE(MAX(seq),0) AS m FROM memories WHERE user_id=%s", (uid,))
            seq = (c.fetchone()["m"] or 0) + 1
            c.execute(
                """INSERT INTO memories (user_id, mem_key, answer, extra, seq)
                   VALUES (%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE answer=VALUES(answer), extra=VALUES(extra), seq=VALUES(seq)""",
                (uid, key, answer, extra, seq),
            )
        if vec is not None:
            qdrant_store.upsert(uid, key, vec, seq)
        return True
    finally:
        conn.close()


def delete(key):
    """删除一条记忆（当前用户），MySQL 正文与 Qdrant 向量同步删除。"""
    uid = _uid()
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM memories WHERE user_id=%s AND mem_key=%s", (uid, key))
            removed = c.rowcount > 0
    finally:
        conn.close()
    if removed:
        qdrant_store.delete_by_key(uid, key)
    return removed


def clear():
    """清空当前用户全部记忆，MySQL 正文与 Qdrant 向量同步清空。"""
    uid = _uid()
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM memories WHERE user_id=%s", (uid,))
    finally:
        conn.close()
    qdrant_store.clear_user(uid)
    return True


def search(query, top_k=VECTOR_TOP_K, uid=None):
    """语义检索：Qdrant 原生 ANN 按余弦召回 top_k（当前用户），正文批量回查 MySQL。"""
    if not query or not query.strip():
        return []
    if uid is None:
        uid = _uid()
    hits = qdrant_store.search(uid, query, top_k=top_k)
    if not hits:
        return []
    keys = [h["key"] for h in hits]
    conn = db.connect()
    try:
        with conn.cursor() as c:
            placeholders = ",".join(["%s"] * len(keys))
            c.execute(
                f"SELECT mem_key, answer, extra FROM memories WHERE user_id=%s AND mem_key IN ({placeholders})",
                (uid, *keys),
            )
            rows = {r["mem_key"]: r for r in c.fetchall()}
    finally:
        conn.close()
    results = []
    for h in hits:
        r = rows.get(h["key"])
        results.append({
            "key": h["key"],
            "answer": (r["answer"] if r else "") or "",
            "extra": (r["extra"] if r else "") or "",
            "score": h["score"],
            "_seq": h["_seq"],
        })
    return results


def search_keyword(query, top_k=VECTOR_TOP_K, uid=None):
    """关键词检索：LIKE 匹配题面/答案/答疑，返回 top_k（当前用户）。"""
    if not query or not query.strip():
        return []
    if uid is None:
        uid = _uid()
    like = "%" + query + "%"
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute(
                """SELECT mem_key, answer, extra, seq FROM memories
                   WHERE user_id=%s AND (mem_key LIKE %s OR answer LIKE %s OR extra LIKE %s)
                   ORDER BY seq DESC LIMIT %s""",
                (uid, like, like, like, top_k),
            )
            rows = c.fetchall()
    finally:
        conn.close()
    return [{"key": r["mem_key"], "answer": r["answer"] or "", "extra": r["extra"] or "", "kw_score": 1, "_seq": r["seq"]} for r in rows]


def search_hybrid(query, top_k=VECTOR_TOP_K):
    """混合检索：关键词优先、语义补充，按 key 去重合并。
    关键词与语义检索并行执行，减少首字等待时间。
    """
    if not query or not query.strip():
        return []
    uid = _uid()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        kw_future = pool.submit(search_keyword, query, top_k, uid)
        sem_future = pool.submit(search, query, top_k, uid)
        kw = kw_future.result()
        sem = sem_future.result()
    merged = {}
    for e in kw:
        e["score"] = 0.0
        merged[e["key"]] = e
    for e in sem:
        if e["key"] in merged:
            merged[e["key"]]["score"] = e["score"]
        else:
            e["kw_score"] = 0
            merged[e["key"]] = e
    ranked = sorted(merged.values(), key=lambda x: (x.get("kw_score", 0), x.get("score", 0.0)), reverse=True)
    return ranked[:top_k]


def rebuild_vectors():
    """把 MySQL 全部记忆的向量重建进 Qdrant（幂等 upsert）。

    覆盖库内全部用户（切换 embedding 模型后调用，确保维度一致）。
    向量不再存 MySQL，重建即「全量重嵌入 + upsert 覆盖」。常规启动时
    若 Qdrant 点数与 MySQL 行数一致则跳过（几乎零开销），仅缺向量时全量重建。
    """
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id, mem_key, answer, seq FROM memories")
            rows = c.fetchall()
    finally:
        conn.close()
    if not rows:
        return 0
    qdrant_store.ensure_collection()
    # 快速路径：集合点数与 MySQL 行数一致，说明向量已齐（幂等），直接跳过
    if qdrant_store.count() == len(rows):
        return 0
    updated = 0
    for r in rows:
        nv = _embed_text(r["mem_key"] + "\n" + (r["answer"] or ""))
        if nv is not None:
            qdrant_store.upsert(r["user_id"], r["mem_key"], nv, r["seq"])
            updated += 1
    return updated
