# -*- coding: utf-8 -*-
"""优化对比编排：对比「优化前简历」与「优化后简历」，流式产出文字化诊断结论。

复用 llm_service 的流式接口，系统提示词来自 prompt_compare（可在前端编辑）。
"""
from llm_service import llm_stream, llm_has_key
from prompt_compare import get_system_prompt, build_user_prompt


def compare_stream(jd, before, after):
    """对比生成器：逐步 yield SSE 事件。

    事件类型：
      - progress：进度提示
      - delta：大模型输出文本增量（前端实时展示）
      - result：最终结果 {text}
      - error：错误信息
    """
    if not llm_has_key():
        yield {"type": "error", "message": "未配置 LLM_API_KEY，请在 .env 中填入后重启服务。"}
        return

    jd = (jd or "").strip()
    before = (before or "").strip()
    after = (after or "").strip()
    if not jd:
        yield {"type": "error", "message": "请填写目标岗位 JD 再做对比。"}
        return
    if not before:
        yield {"type": "error", "message": "请填写优化前简历（原始简历）再做对比。"}
        return
    if not after:
        yield {"type": "error", "message": "请先生成优化后简历再做对比。"}
        return

    user_prompt = build_user_prompt(jd, before, after)
    yield {"type": "progress", "step": "compare", "message": "AI 正在对比优化前 / 后简历…"}

    full = ""
    try:
        for delta in llm_stream(
            [{"role": "system", "content": get_system_prompt()},
             {"role": "user", "content": user_prompt}],
            temperature=0.4,
        ):
            full += delta
            yield {"type": "delta", "text": delta}
    except Exception as e:
        yield {"type": "error", "message": f"调用大模型失败：{e}"}
        return

    yield {"type": "result", "text": full.strip()}
