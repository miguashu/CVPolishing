# -*- coding: utf-8 -*-
"""Qdrant 向量存储封装：长期记忆的语义向量统一存这里。

设计原则：
- 正文（mem_key/answer/extra/seq）仍在 MySQL（关键词检索 / 用户隔离 / 管理接口不变），
  本模块只负责「向量 + 检索」这一层。
- 点 ID 由 user_id + mem_key 的稳定哈希生成（int64），同一记忆 upsert 天然覆盖旧向量，
  无需先查后删，天然幂等。
- 集合名来自 config.QDRANT_COLLECTION，按当前嵌入模型维度自动创建。
  切换嵌入模型导致维度变化时，改集合名即可触发启动时全量重建。
- 所有网络操作均做异常隔离：Qdrant 不可用时检索静默降级为空结果，
  写入操作仍会抛异常（由调用方决定如何处理）。
"""
import hashlib
import logging

from qdrant_client import QdrantClient
from qdrant_client.http import models

from config import QDRANT_API_KEY, QDRANT_COLLECTION, QDRANT_URL, VECTOR_TOP_K
from embed import embed

logger = logging.getLogger("qdrant")
# 全局复用同一个客户端（延迟初始化，import 本模块时不连接）
_client = None


def _get_client():
    global _client
    if _client is None:
        # check_compatibility=False：本地 Qdrant 服务端版本可能与 qdrant-client 有差异，
        # 已验证兼容（集合创建 / upsert / 检索均正常），显式声明避免每次运行刷版本警告。
        _client = QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY or None,
            check_compatibility=False,
        )
    return _client


def _dim():
    """探测当前嵌入模型的实际维度。"""
    return len(embed(["维度探测"])[0])


def ensure_collection():
    """确保向量集合存在且维度正确；不存在则按当前模型维度创建。"""
    c = _get_client()
    try:
        existing = c.get_collection(QDRANT_COLLECTION)
    except Exception:
        existing = None
    if existing is None:
        c.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=models.VectorParams(size=_dim(), distance=models.Distance.COSINE),
        )
        return True
    return False


def _point_id(user_id, mem_key):
    """由 user_id + mem_key 生成稳定的 63 位以内非负整数点 ID（Qdrant 支持 uint64）。"""
    h = hashlib.md5(f"{user_id}|{mem_key}".encode("utf-8")).hexdigest()
    return int(h[:15], 16)  # 15 位十六进制 < 2^60，保证为正 int64


def upsert(user_id, mem_key, vector, seq):
    """写入 / 覆盖一条记忆的向量点。vector 为 list[float]。"""
    c = _get_client()
    c.upsert(
        collection_name=QDRANT_COLLECTION,
        points=[models.PointStruct(
            id=_point_id(user_id, mem_key),
            vector=vector,
            payload={"user_id": user_id, "mem_key": mem_key, "seq": int(seq)},
        )],
    )


def delete_by_key(user_id, mem_key):
    """删除某条记忆的向量点。"""
    c = _get_client()
    c.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=models.PointIdsList(points=[_point_id(user_id, mem_key)]),
    )


def clear_user(user_id):
    """清空某用户全部向量点。"""
    c = _get_client()
    c.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=models.FilterSelector(filter=models.Filter(
            must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))],
        )),
    )


def search(user_id, query, top_k=VECTOR_TOP_K):
    """语义检索：对 query 嵌入后走 Qdrant 原生 ANN，返回
    [{"key","score","_seq"}]，按相关度降序，仅当前用户。
    Qdrant 不可用时静默降级为空结果（日均多次调用），不影响关键词检索。
    """
    if not query or not query.strip():
        return []
    qvec = embed([query])[0]
    c = _get_client()
    try:
        hits = c.query_points(
            collection_name=QDRANT_COLLECTION,
            query=qvec,
            limit=top_k,
            query_filter=models.Filter(
                must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))],
            ),
            with_payload=True,
        ).points
    except Exception as e:
        logger.warning("Qdrant 语义检索失败（服务不可用）: %s", e)
        return []
    return [
        {
            "key": h.payload.get("mem_key", ""),
            "score": float(h.score),
            "_seq": int(h.payload.get("seq", 0)),
        }
        for h in hits
    ]


def count(user_id=None):
    """统计集合内向量点数；给定 user_id 时统计该用户点数。"""
    c = _get_client()
    flt = None
    if user_id is not None:
        flt = models.Filter(
            must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))],
        )
    return c.count(collection_name=QDRANT_COLLECTION, count_filter=flt, exact=True).count
