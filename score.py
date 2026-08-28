# -*- coding: utf-8 -*-
"""简历评分智能体：针对单份简历或「优化前 vs 优化后」两份简历，结合 JD 做多维度评分。

复用 llm_service 的流式接口，输出严格 JSON（模型温度调低以保证可解析），
由后端解析为结构化维度分与总评，前端据此渲染雷达图式评分卡。
"""
import json

from llm_service import llm_stream, llm_has_key
from prompt_config import load_prompts


def get_system_prompt():
    """运行时读取当前生效的评分系统提示词（含前端覆盖）。"""
    return load_prompts()["score_system"]


def score_once(jd, resume):
    """非流式评分：返回规整后的评分字典 {dimensions, total, summary}。

    供迭代优化闭环在每轮优化后复用，避免重复拼装与解析。
    """
    if not llm_has_key():
        raise RuntimeError("未配置 LLM_API_KEY，请在 .env 中填入后重启服务。")
    user_prompt = build_user_prompt(jd, resume)
    full = ""
    for delta in llm_stream(
        [{"role": "system", "content": get_system_prompt()},
         {"role": "user", "content": user_prompt}],
        temperature=0.2,
    ):
        full += delta
    parsed = _parse_score_json(full)
    if not parsed:
        raise RuntimeError("评分结果解析失败，请重试。")
    return _normalize_one(parsed)


# 评分维度定义：名称 + 权重 + 说明（前端展示与提示词共用）
DIMENSIONS = [
    ("jd匹配度", 0.30, "关键词、技能栈、业务职责与 JD 硬性要求的吻合程度"),
    ("量化成果", 0.20, "项目/经历中是否用数据、指标、业务收益佐证能力"),
    ("结构规范", 0.15, "模块划分、排版层次、重点突出、ATS 可读性"),
    ("经历表现", 0.20, "项目挖掘深度、职责描述、成就含金量与真实性"),
    ("语言表达", 0.15, "书面简洁度、专业度、无冗余口语与空洞表述"),
]


def build_user_prompt(jd, resume_before, resume_after=None):
    """拼装评分用户提示词。

    resume_after 为空 -> 单份评分；不为空 -> 优化前/后对比评分（分别给两套分数）。
    """
    if resume_after and resume_after.strip():
        return f"""# 目标岗位 JD
{jd.strip()}

# 优化前简历
{resume_before.strip()}

# 优化后简历
{resume_after.strip()}

# 任务
请分别对「优化前简历」与「优化后简历」各打一套分（维度与权重同上），最后额外输出两项的对比结论 improvement（一句话说明优化后在哪些维度提升、提升幅度）。
输出 JSON 格式：
{{
  "before": <单份评分的 JSON 结构>,
  "after": <单份评分的 JSON 结构>,
  "improvement": "对比结论（一句话）"
}}"""
    return f"""# 目标岗位 JD
{jd.strip()}

# 简历内容
{resume_before.strip()}

# 任务
请按固定维度与权重对简历打分，输出单份评分 JSON。"""


def _parse_score_json(text):
    """从模型输出中尽力解析评分 JSON。兼容被 ```json 代码块包裹的情形。"""
    text = text.strip()
    if text.startswith("```"):
        # 去除 ```json ... ``` 包裹
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def score_stream(jd, resume_before, resume_after=None):
    """评分生成器：逐步 yield SSE 事件。

    事件类型：
      - progress：评分进度提示
      - delta：大模型输出增量（便于前端展示实时生成）
      - result：结构化评分 {before, after?, improvement?, dimensions, total, summary}
      - error：错误
    """
    if not llm_has_key():
        yield {"type": "error", "message": "未配置 LLM_API_KEY，请在 .env 中填入后重启服务。"}
        return

    user_prompt = build_user_prompt(jd, resume_before, resume_after)
    yield {"type": "progress", "step": "score", "message": "AI 正在从 HR / ATS / 面试官视角为简历打分…"}

    full = ""
    try:
        for delta in llm_stream(
            [{"role": "system", "content": get_system_prompt()},
             {"role": "user", "content": user_prompt}],
            temperature=0.2,
        ):
            full += delta
            yield {"type": "delta", "text": delta}
    except Exception as e:
        yield {"type": "error", "message": f"调用大模型失败：{e}"}
        return

    parsed = _parse_score_json(full)
    if not parsed:
        yield {"type": "error", "message": "评分结果解析失败，请重试。原始输出：" + full[:200]}
        return

    # 单份 / 对比两套结果归一化
    if "before" in parsed and "after" in parsed:
        result = {
            "mode": "compare",
            "before": _normalize_one(parsed.get("before")),
            "after": _normalize_one(parsed.get("after")),
            "improvement": parsed.get("improvement", ""),
        }
    else:
        result = {"mode": "single", "before": _normalize_one(parsed), "after": None, "improvement": ""}

    yield {"type": "result", **result}


def _normalize_one(obj):
    """把单份评分对象规整为 {dimensions, total, summary}。"""
    if not isinstance(obj, dict):
        return {"dimensions": [], "total": 0, "summary": ""}
    dims = []
    for d in obj.get("dimensions", []):
        try:
            score = int(d.get("score", 0))
        except (ValueError, TypeError):
            score = 0
        dims.append({
            "name": d.get("name", ""),
            "score": max(0, min(100, score)),
            "comment": d.get("comment", ""),
        })
    try:
        total = int(obj.get("total", 0))
    except (ValueError, TypeError):
        total = 0
    return {"dimensions": dims, "total": max(0, min(100, total)), "summary": obj.get("summary", "")}
