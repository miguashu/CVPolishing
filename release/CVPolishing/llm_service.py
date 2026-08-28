# -*- coding: utf-8 -*-
"""大模型问答引擎适配层：对 OpenAI 兼容接口做流式补全，供简历优化 / 面试答疑 / 记忆问答复用。

集中维护一个 _llm_stream，避免各模块重复造轮子（optimizer / interview 共用）。
"""
import json

import requests

from config import (LLM_API_BASE, LLM_API_KEY, LLM_MODEL, LLM_VISION_MODEL,
                     LLM_TEMPERATURE, LLM_TIMEOUT, LLM_MAX_TOKENS)


def llm_stream(messages, temperature=None):
    """OpenAI 兼容接口的流式补全，逐块 yield 文本增量。

    messages 形如 [{"role": "system", "content": ...}, {"role": "user", "content": ...}]。
    """
    url = LLM_API_BASE.rstrip("/") + "/chat/completions"
    payload = {
        "model": LLM_MODEL,
        "temperature": LLM_TEMPERATURE if temperature is None else temperature,
        "stream": True,
        "max_tokens": LLM_MAX_TOKENS,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=LLM_TIMEOUT, stream=True)
    resp.raise_for_status()
    resp.encoding = "utf-8"  # 强制 UTF-8，避免 requests 默认 latin-1 导致中文乱码
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("data:"):
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = obj["choices"][0]["delta"].get("content", "")
                if delta:
                    yield delta
            except (json.JSONDecodeError, KeyError, IndexError):
                continue


def llm_has_key():
    """是否已配置大模型密钥。"""
    return bool(LLM_API_KEY)


def llm_complete(messages, temperature=None, with_usage=False):
    """OpenAI 兼容接口的非流式补全，返回完整文本。

    用于思考 / 监督等轻量结构化调用（无需逐字流式的场景）。

    with_usage=True 时返回 {"content": "...", "usage": {prompt_tokens, completion_tokens, total_tokens}}。
    """
    url = LLM_API_BASE.rstrip("/") + "/chat/completions"
    payload = {
        "model": LLM_MODEL,
        "temperature": LLM_TEMPERATURE if temperature is None else temperature,
        "stream": False,
        "max_tokens": LLM_MAX_TOKENS,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=LLM_TIMEOUT)
    resp.raise_for_status()
    obj = resp.json()
    content = obj["choices"][0]["message"].get("content", "")
    if with_usage:
        return {"content": content, "usage": obj.get("usage", {})}
    return content


def llm_vision_text(system, image_b64, mime, prompt, temperature=0.2):
    """多模态视觉补全：把图片（base64）连同文字指令发给支持视觉的模型，返回识别/理解结果。

    兼容 OpenAI 视觉消息结构（content 内嵌 image_url）。模型不支持视觉时会抛出异常，
    由调用方转换为「图片识别失败」错误返回前端。
    """
    url = LLM_API_BASE.rstrip("/") + "/chat/completions"
    payload = {
        "model": LLM_VISION_MODEL,
        "temperature": temperature,
        "stream": False,
        "max_tokens": LLM_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ]},
        ],
    }
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=LLM_TIMEOUT)
    resp.raise_for_status()
    obj = resp.json()
    # 端点可能返回 {"error": {...}} 而非标准 choices 结构（如图片格式非法），需显式暴露真实原因
    if isinstance(obj, dict) and obj.get("error"):
        err = obj["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"视觉模型返回错误：{msg}")
    if not isinstance(obj, dict) or not obj.get("choices"):
        raise RuntimeError(f"视觉模型返回结构异常（无 choices）：{str(obj)[:200]}")
    return obj["choices"][0]["message"].get("content", "")
