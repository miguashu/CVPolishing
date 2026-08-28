# -*- coding: utf-8 -*-
"""子 Agent 沙盒层：并发派发多个子 Agent，各自独立调用大模型，只回吐结构化结果。

设计原则（与编排器模式一致）：
- 主 Agent（闭环编排器）负责串联依赖、合并结果；本模块只做「无依赖子 Agent 的并行执行」。
- 每个子 Agent 是一个独立沙盒：独立的 system/user 提示词、独立的 LLM 调用、互不共享状态。
- 调用方传入若干 AgentSpec，本模块用线程池并发执行，等全部完成后以字典形式回吐结果。
- 子 Agent 失败不影响其他子 Agent：单个失败以 error 标记返回，由主 Agent 决定兜底，
  不抛异常中断整个批次（消除「一个挂了全崩」的脆弱性，而非堆 fallback 重试）。
"""
import json
import re
import traceback
from concurrent.futures import ThreadPoolExecutor

from llm_service import llm_complete


def _extract_json(raw):
    """从模型输出中尽力解析 JSON 对象。兼容被 ```json 代码块包裹、首尾杂质。

    内联实现以避免与 optimizer 形成循环导入（optimizer 反向依赖本模块）。
    """
    if not raw:
        return None
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        s = text.find("{")
        e = text.rfind("}")
        if s != -1 and e != -1 and e > s:
            text = text[s:e + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


class AgentSpec:
    """子 Agent 规格：名称 + 系统提示词 + 用户提示词 + 输出解析方式。

    parser 可选：
      - None：直接返回原始文本（content）
      - "json"：从输出中尽力解析 JSON 对象
      - 可调用对象：接收原始文本，返回解析后的结果（异常时回退为原始文本）
    """

    def __init__(self, name, system, user, temperature=0.3, parser="json", with_usage=False):
        self.name = name
        self.system = system
        self.user = user
        self.temperature = temperature
        self.parser = parser
        self.with_usage = with_usage


def _run_one(spec):
    """执行单个子 Agent 沙盒，返回 {name, ok, content, parsed, usage, error}。

    任何异常都被捕获并结构化返回，绝不向上抛出——保证一个子 Agent 失败不拖垮整批。
    """
    result = {"name": spec.name, "ok": False, "content": "", "parsed": None,
              "usage": {}, "error": None}
    try:
        raw = llm_complete(
            [{"role": "system", "content": spec.system},
             {"role": "user", "content": spec.user}],
            temperature=spec.temperature,
            with_usage=spec.with_usage,
        )
        if isinstance(raw, dict):
            content = raw.get("content", "")
            result["usage"] = raw.get("usage", {})
        else:
            content = raw
        result["content"] = content

        if spec.parser == "json":
            result["parsed"] = _extract_json(content) or {}
        elif callable(spec.parser):
            try:
                result["parsed"] = spec.parser(content)
            except Exception:
                result["parsed"] = content
        else:
            result["parsed"] = content

        result["ok"] = True
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()
    return result


def run_subagents(*specs, max_workers=None):
    """并发派发多个子 Agent 沙盒，返回 {name: result_dict}。

    - 子 Agent 之间彼此独立、无依赖，全部并行执行。
    - 主 Agent（调用方）负责拿到结果后做依赖合并。
    - max_workers 缺省为子 Agent 数量（每个 Agent 一个线程，互不阻塞）。
    """
    if not specs:
        return {}
    workers = max_workers or len(specs)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_one, s): s.name for s in specs}
        out = {}
        for fut in futures:
            res = fut.result()
            out[res["name"]] = res
    return out
