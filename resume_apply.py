# -*- coding: utf-8 -*-
"""简历按需修改（SSE 流式）：按用户提出的修改需求，AI 针对性修改简历。

智能问答页「按需修改简历」的后端支撑：
- 系统提示词取自 prompt_config.resume_apply_system（支持前端覆盖，运行时实时生效）；
- 用户提示词由 prompt.build_apply_prompt 拼装；
- 复用 llm_service.llm_stream 流式调用，逐块推送 delta；
- 结果解析出「===修改后简历===」「===修改说明===」两段。
"""
import re

from llm_service import llm_stream, llm_has_key
from prompt_config import load_prompts
from prompt import build_apply_prompt

_MARK_RESUME = "===修改后简历==="
_MARK_NOTES = "===修改说明==="


def _split_result(full):
    """把 LLM 输出拆分为（修改后简历, 修改说明）。任一标记缺失时回退整段为简历。"""
    if not full:
        return "", ""
    if _MARK_RESUME in full:
        head = full.split(_MARK_RESUME, 1)[1]
    else:
        head = full
    if _MARK_NOTES in head:
        resume, notes = head.split(_MARK_NOTES, 1)
    else:
        resume, notes = head, ""
    return resume.strip(), notes.strip()


def apply_change_stream(resume, requirement, jd=""):
    """SSE 事件生成器：按用户需求流式修改简历。

    事件:
        {"type": "progress", "message": ...}
        {"type": "delta", "text": ...}
        {"type": "result", "resume": ..., "notes": ..., "raw": ...}
        {"type": "error", "message": ...}
    """
    if not llm_has_key():
        yield {"type": "error", "message": "未配置 LLM_API_KEY，无法修改简历"}
        return
    if not resume.strip():
        yield {"type": "error", "message": "请先填写或上传简历"}
        return
    if not requirement.strip():
        yield {"type": "error", "message": "请填写修改需求（例如：突出项目管理能力）"}
        return

    system = load_prompts()["resume_apply_system"]
    user = build_apply_prompt(resume, requirement, jd)
    yield {"type": "progress", "message": "正在按需求修改简历…"}

    parts = []
    try:
        for delta in llm_stream([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]):
            parts.append(delta)
            yield {"type": "delta", "text": delta}
    except Exception as e:
        yield {"type": "error", "message": f"修改失败：{e}"}
        return

    full = "".join(parts)
    new_resume, notes = _split_result(full)
    if not new_resume:
        yield {"type": "error", "message": "修改结果为空，请重试"}
        return
    yield {"type": "result", "resume": new_resume, "notes": notes, "raw": full}
