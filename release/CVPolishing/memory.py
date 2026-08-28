# -*- coding: utf-8 -*-
"""长期记忆存储（MySQL 版）：面试题与答案，按 user_id 隔离，带操作时间。

对外函数签名与旧文件版保持一致，app.py 无需改动调用方式：
  get_all / put / delete / clear / search / search_hybrid / rebuild_vectors

向量以 JSON 文本存入 embedding 列；语义检索在 Python 内做余弦相似度。
created_at / updated_at 由 MySQL 自动维护（操作时间）。
"""
import json

import user_ctx
from config import VECTOR_TOP_K
from embed import embed
import db


def _uid():
    return user_ctx.get_user_id()


def _embed_text(text):
    """对单条文本做嵌入，失败时返回 None（不阻断保存，仅该条不参与语义检索）。"""
    try:
        return embed([text[:2000]])[0]
    except Exception:
        return None


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


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
    """写入 / 覆盖一条记忆（当前用户）。key 为题目标题。"""
    if not key or not key.strip():
        return False
    key = key.strip()
    answer = (answer or "").strip()
    extra = (extra or "").strip()
    uid = _uid()
    vec = _embed_text(key + "\n" + answer)
    emb_json = json.dumps(vec) if vec is not None else None
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT COALESCE(MAX(seq),0) AS m FROM memories WHERE user_id=%s", (uid,))
            seq = (c.fetchone()["m"] or 0) + 1
            c.execute(
                """INSERT INTO memories (user_id, mem_key, answer, extra, embedding, seq)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE answer=VALUES(answer), extra=VALUES(extra), embedding=VALUES(embedding)""",
                (uid, key, answer, extra, emb_json, seq),
            )
        return True
    finally:
        conn.close()


def delete(key):
    """删除一条记忆（当前用户）。"""
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM memories WHERE user_id=%s AND mem_key=%s", (_uid(), key))
            return c.rowcount > 0
    finally:
        conn.close()


def clear():
    """清空当前用户全部记忆。"""
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM memories WHERE user_id=%s", (_uid(),))
        return True
    finally:
        conn.close()


def search(query, top_k=VECTOR_TOP_K):
    """语义检索：对 query 嵌入，按余弦相似度返回最相关的 top_k 条（当前用户）。"""
    if not query or not query.strip():
        return []
    uid = _uid()
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT mem_key, answer, extra, embedding, seq FROM memories WHERE user_id=%s", (uid,))
            rows = c.fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    try:
        qvec = embed([query])[0]
        dim = len(qvec)
    except Exception:
        return []
    scored = []
    for r in rows:
        vec = r["embedding"]
        if not vec:
            continue
        try:
            vec = json.loads(vec)
        except Exception:
            continue
        if len(vec) != dim:
            continue
        scored.append({
            "key": r["mem_key"],
            "answer": r["answer"] or "",
            "extra": r["extra"] or "",
            "score": _cosine(qvec, vec),
            "_seq": r["seq"],
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def search_keyword(query, top_k=VECTOR_TOP_K):
    """关键词检索：LIKE 匹配题面/答案/答疑，返回 top_k（当前用户）。"""
    if not query or not query.strip():
        return []
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
    """混合检索：关键词优先、语义补充，按 key 去重合并。"""
    kw = search_keyword(query, top_k=top_k)
    sem = search(query, top_k=top_k)
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
    """补齐记忆向量：只处理缺失或与当前模型维度不符的条目，返回重建条数。

    覆盖库内全部用户（切换 embedding 模型通常影响所有人）。
    已是当前维度的向量直接跳过，因此常规启动几乎无开销。
    """
    conn = db.connect()
    try:
        with conn.cursor() as c:
            c.execute("SELECT id, mem_key, answer, embedding FROM memories")
            rows = c.fetchall()
        if not rows:
            return 0
        dim = len(embed(["维度探测"])[0])
        updated = 0
        for r in rows:
            old = r["embedding"]
            if old:
                try:
                    if len(json.loads(old)) == dim:
                        continue
                except Exception:
                    pass
            nv = _embed_text(r["mem_key"] + "\n" + (r["answer"] or ""))
            if nv is not None and len(nv) == dim:
                with conn.cursor() as c:
                    c.execute("UPDATE memories SET embedding=%s WHERE id=%s", (json.dumps(nv), r["id"]))
                updated += 1
        return updated
    finally:
        conn.close()
