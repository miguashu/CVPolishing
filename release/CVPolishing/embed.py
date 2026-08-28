# -*- coding: utf-8 -*-
"""向量嵌入客户端：封装两种 /embeddings 来源，供长期记忆语义检索复用。

- provider=ollama：调用本地 Ollama 原生接口 POST {base}/api/embeddings
  （请求字段 prompt，返回 embedding），无需联网、无需校验 key。
- provider=openai：调用 OpenAI 兼容接口 POST {base}/embeddings
  （请求字段 input，返回 data[].embedding）。

向量在记忆条目保存时计算并落盘，答疑时只须对查询句嵌入一次，按余弦相似度召回 top-K。
"""
import requests

from config import (
    EMBEDDING_API_BASE,
    EMBEDDING_API_KEY,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
    LLM_TIMEOUT,
)


def embed(texts):
    """对一批文本做嵌入，返回与输入顺序一致的向量列表（list[list[float]]）。

    texts: list[str]
    """
    if not texts:
        return []
    if EMBEDDING_PROVIDER == "ollama":
        return _embed_ollama(texts)
    return _embed_openai(texts)


def _embed_openai(texts):
    """OpenAI 兼容 /embeddings：input 支持字符串或列表，返回 data[].embedding。"""
    if not EMBEDDING_API_KEY:
        raise RuntimeError("未配置 EMBEDDING_API_KEY，无法进行向量检索。")
    url = EMBEDDING_API_BASE.rstrip("/") + "/embeddings"
    payload = {"model": EMBEDDING_MODEL, "input": texts}
    headers = {
        "Authorization": f"Bearer {EMBEDDING_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=LLM_TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    data.sort(key=lambda x: x.get("index", 0))
    return [d["embedding"] for d in data]


def _embed_ollama(texts):
    """本地 Ollama 原生 /api/embeddings：单次只接受单条 prompt，循环嵌入保持顺序。"""
    base = EMBEDDING_API_BASE.rstrip("/")
    url = base + "/api/embeddings"
    out = []
    for t in texts:
        payload = {"model": EMBEDDING_MODEL, "prompt": t[:4000]}
        resp = requests.post(url, json=payload, timeout=LLM_TIMEOUT)
        resp.raise_for_status()
        emb = resp.json().get("embedding")
        if not emb:
            raise RuntimeError(
                f"Ollama 未返回 embedding，请确认模型 {EMBEDDING_MODEL} 已拉取且可运行。"
            )
        out.append(emb)
    return out
