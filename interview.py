# -*- coding: utf-8 -*-
"""面试模拟编排：基于优化后简历 + JD 生成面试题与通俗答案，并支持对单题追问答疑。

复用 llm_service 的流式能力与 prompt_interview 的提示词拼装；
输出按 ===Q=== / ===A=== 切分为结构化题目，便于前端渲染与长期记忆保存。
"""
import re
import json

from llm_service import llm_stream, llm_has_key
from prompt_interview import (
    get_interview_prompt, get_answer_prompt, get_refine_prompt, get_genanswer_prompt, get_parse_prompt,
    get_plan_prompt,
    build_interview_prompt, build_answer_prompt, build_refine_prompt, build_genanswer_prompt, build_parse_prompt,
    build_plan_prompt,
)
from memory import put, search as memory_search
from config import VECTOR_TOP_K, SEARCH_ENABLED
from search_service import search_references_stream, build_reference_context


# 语义相似度阈值：命中相似长期记忆即视为「已存在」，复用而不重复生成
_REUSE_SCORE_THRESHOLD = 0.72
# 生成后向量兜底去重阈值（更高，仅高度相似才丢弃合法新题，避免反复补足）
_REUSE_SCORE_HIGH = 0.85

# 生成后去重补足：单轮最多生成题数、最多循环轮次（防死循环）
_GEN_BATCH = 4
_GEN_MAX_ROUNDS = 6


def _norm_question(q):
    """题目归一化：去空白/标点/大小写，用于生成后快速去重比对。

    注意：不能用 \p{P}（Java/PCRE 语法，Python re 不支持，会抛 bad escape）。
    改为保留中文、字母、数字、下划线，去掉其余全部标点与空白。
    """
    import re as _re
    s = (q or "").lower()
    s = _re.sub(r"[\W_]+", "", s, flags=_re.UNICODE)
    return s


def _is_dup_question(q, seen_norms, resume="", jd=""):
    """判断单道题是否与已见集合或长期记忆库重复。

    - seen_norms：已产出的题面归一化集合（含复用题与本轮有效题）；
    - 同集合内仅做精确归一化相等比对（不再用子串包含，避免两道不同题因部分文字重叠被误杀）；
    - 向量检索兜底：命中库里高度相似题（提高后的阈值）才判重复。
    返回 True 表示重复、应丢弃不计入有效数。
    """
    nq = _norm_question(q)
    if not nq:
        return True
    if nq in seen_norms:
        return True
    try:
        hits = memory_search(q, top_k=2)
        if any(h.get("score", 0) >= _REUSE_SCORE_HIGH for h in hits):
            return True
    except Exception:
        pass
    return False


def generate_interview_stream(resume, jd, count=8, enable_search=None, auto_save=True,
                              append=False, existing=None):
    """生成面试题主流程：生成前先查长期记忆去重，命中相似的复用、不计入生成条数；
    未命中的用「更深入 / 换角度」并可选联网最新资料生成，生成后按 auto_save 逐条入库。逐步 yield SSE 事件。

    append=True 为「补充面试题」模式：在前端已有题目基础上追加 count 道全新题。
    优化点（避免从头重跑整条流水线）：
      - 跳过主题规划（_plan_topics），不再额外消耗一次 LLM 调用；
      - 跳过「逐主题查长期记忆」的全局去重检索（改为仅用已有题目做归一化去重，零向量检索）；
      - 复用长期记忆：以整体 JD 为锚做一次记忆检索，命中的相似问答作为 reused 复用素材（仍保留记忆复用能力）；
      - 仍保留联网检索最新资料（enable_search）作为出题上下文；
      - 把已有题目（existing）一并喂给模型，模型直接避开历史，避免触发多轮避重补足循环。

    事件类型：
      - progress：进度提示
      - delta：大模型输出文本增量（前端按分隔标记整理为题目）
      - reused：一条命中长期记忆的相似问答 {key, answer}
      - saved：一条新题已写入长期记忆 {key, answer}
      - result：最终结果 {questions: [{question, answer}], reused: [{key, answer}]}
      - error：错误信息
    """
    if enable_search is None:
        enable_search = SEARCH_ENABLED
    if existing:
        existing = [str(q).strip() for q in existing if str(q).strip()]
    else:
        existing = []
    if not llm_has_key():
        yield {"type": "error", "message": "未配置 LLM_API_KEY，请在 .env 中填入后重启服务。"}
        return
    if not resume or not resume.strip():
        yield {"type": "error", "message": "缺少简历内容，请先在「简历优化」页完成优化并保存。"}
        return
    if not jd or not jd.strip():
        yield {"type": "error", "message": "缺少目标岗位 JD，无法生成针对性面试题。"}
        return

    # append 模式：已有题目作为去重锚点，避免从头规划与全局记忆检索
    if append:
        # 用整体 JD 锚一次长期记忆检索，命中的相似问答作为复用素材（仍保留记忆复用能力）
        reused = []
        seen_reuse_keys = set()
        yield {"type": "progress", "message": "正在检索长期记忆库，复用相似问答…"}
        hits = memory_search(jd, top_k=8)
        for hit in hits:
            if hit.get("score", 0) >= _REUSE_SCORE_THRESHOLD and hit["key"] not in seen_reuse_keys:
                # 已存在的题目不再作为 reused 复用（避免与现有列表重复）
                if _is_dup_question(hit["key"], {_norm_question(q) for q in existing}):
                    continue
                seen_reuse_keys.add(hit["key"])
                reused.append({"key": hit["key"], "answer": hit.get("answer", "")})
                yield {"type": "reused", "key": hit["key"], "answer": hit.get("answer", "")}
        gen_count = count
    else:
        # 第一步：规划候选面试主题
        yield {"type": "progress", "message": f"AI 正在规划 {count} 个面试主题…"}
        topics = _plan_topics(resume, jd, count=count)
        if topics is None:
            yield {"type": "error", "message": "规划面试主题失败，请重试。"}
            return

        # 第二步：逐主题查长期记忆去重
        reused = []          # 已存在、直接复用
        seen_reuse_keys = set()
        yield {"type": "progress", "message": "正在检索长期记忆库，剔除已存在的相似问题…"}
        for topic in topics:
            hits = memory_search(topic, top_k=3)
            hit = next((h for h in hits if h.get("score", 0) >= _REUSE_SCORE_THRESHOLD), None)
            if hit and hit["key"] not in seen_reuse_keys:
                seen_reuse_keys.add(hit["key"])
                reused.append({"key": hit["key"], "answer": hit.get("answer", "")})
                yield {"type": "reused", "key": hit["key"], "answer": hit.get("answer", "")}
            else:
                pass  # 未命中，进入生成环节

        gen_count = count - len(reused)
        if gen_count <= 0:
            # 全部命中已有记忆，直接复用
            yield {"type": "result", "questions": [], "reused": reused}
            return

    # 第三步（可选）：联网检索最新行业资料，作为深入出题的上下文（append 模式同样保留）
    web_context = ""
    if enable_search:
        job_title = _extract_job_title(jd)
        for ev in search_references_stream(job_title, max_results=6):
            if ev.get("type") == "progress":
                yield {"type": "progress", "message": ev.get("message", "")}
            elif ev.get("type") == "result":
                web_context = build_reference_context(ev.get("items", []))

    # 第四步：循环生成面试题，生成后去重、重复丢弃并补足，直到有效数达标
    # append 模式：need_new = count，且把已有题目喂给模型，模型首轮即避开历史，几乎不触发补足循环；
    # 非 append 模式：need_new = count - 复用数，保持原语义。
    need_new = gen_count
    questions = []
    seen_norms = {_norm_question(q) for q in existing}  # 已有题目计入去重，新题不得与之重复
    seen_norms |= {_norm_question(r.get("key", "")) for r in reused}  # 复用题也计入去重
    round_i = 0
    while len(questions) < need_new and round_i < _GEN_MAX_ROUNDS:
        round_i += 1
        remaining = need_new - len(questions)
        batch = min(_GEN_BATCH, remaining)
        if append:
            yield {"type": "progress", "message": f"AI 正在补充生成面试题（已得 {len(questions)}/{need_new} 道）…"}
        else:
            yield {"type": "progress", "message": f"AI 正在生成面试题（已得 {len(questions)}/{need_new} 道）…"}
        user_prompt = build_interview_prompt(
            resume, jd, count=batch, reused=reused,
            existing=existing + [q["question"] for q in questions], web_context=web_context)
        full = ""
        try:
            for delta in llm_stream([{"role": "system", "content": get_interview_prompt()},
                                     {"role": "user", "content": user_prompt}]):
                full += delta
                yield {"type": "delta", "text": delta}
        except Exception as e:
            yield {"type": "error", "message": f"调用大模型失败：{e}"}
            return
        # 生成后逐题去重：仅精确相等或高度相似（向量 0.85+）才丢弃，避免误杀合法新题
        prev_len = len(questions)
        for q in _parse_questions(full):
            k = (q.get("question") or "").strip()
            a = (q.get("answer") or "").strip()
            if not k or not a:
                continue
            if _is_dup_question(k, seen_norms):
                continue
            seen_norms.add(_norm_question(k))
            questions.append({"question": k, "answer": a})
        # 每批解析完立即推送新题，前端不用等全部生成完毕才展示
        new_in_batch = questions[prev_len:]
        if new_in_batch:
            yield {"type": "questions_batch", "questions": new_in_batch}

    # 生成后仍不足目标数：用长期记忆库「借题」补齐新增目标（可复用历史，避免反复逼模型重出）
    if len(questions) < need_new:
        yield {"type": "progress", "message": f"正在从长期记忆库复用相似题补齐（已得 {len(questions)}/{need_new} 道）…"}
        seen_reuse_keys = {_norm_question(r.get("key", "")) for r in reused}
        for hit in memory_search(jd, top_k=need_new * 4):
            if len(questions) >= need_new:
                break
            key = hit.get("key", "").strip()
            ans = hit.get("answer", "").strip()
            if not key or not ans:
                continue
            nk = _norm_question(key)
            # 跳过已复用集合 / 与已有题目重复的
            if nk in seen_reuse_keys or _is_dup_question(key, seen_norms):
                continue
            seen_reuse_keys.add(nk)
            seen_norms.add(nk)
            reused.append({"key": key, "answer": ans})
            yield {"type": "reused", "key": key, "answer": ans}
            questions.append({"question": key, "answer": ans})

    if len(questions) < need_new:
        yield {"type": "progress",
               "message": f"记忆库可复用题不足，实际生成 {len(questions)} 道（目标 {need_new} 道）。"}

    # 第五步：生成结果按 auto_save 逐条写入长期记忆
    for q in questions:
        k = q["question"]
        a = q["answer"]
        if auto_save:
            try:
                ok = put(k, a, "")
            except Exception:
                ok = False
            if ok:
                yield {"type": "saved", "key": k, "answer": a}

    yield {"type": "result", "questions": questions, "reused": reused}


def _plan_topics(resume, jd, count=8):
    """调用主题规划 Agent，提炼候选面试主题列表（字符串数组）。失败返回 None。"""
    try:
        full = ""
        for delta in llm_stream([{"role": "system", "content": get_plan_prompt()},
                                 {"role": "user", "content": build_plan_prompt(resume, jd, count=count)}],
                                temperature=0.3):
            full += delta
        raw = full.strip()
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.S)
        if m:
            raw = m.group(1).strip()
        data = json.loads(raw)
        if isinstance(data, list):
            topics = [str(t).strip() for t in data if str(t).strip()]
            return topics[:count] if topics else None
    except Exception:
        return None
    return None


def _extract_job_title(jd):
    """从 JD 文本中粗略抽取岗位名（取前 24 字、掐到换行前），用于联网检索。"""
    line = (jd or "").strip().splitlines()[0] if (jd or "").strip() else ""
    line = line.replace("#", "").strip()
    return line[:24] if line else "面试 岗位"


def answer_question_stream(question, original_answer, user_question, memory_context="", auto_memory=False):
    """对单道面试题追问答疑：流式 yield SSE 事件。

    事件类型：
      - progress：进度提示
      - delta：大模型输出文本增量（前端实时展示）
      - result：最终结果 {answer}
      - saved：已自动提炼并存入长期记忆 {key, answer}
      - error：错误信息

    auto_memory=True 时，回答完成后会用「答案提炼 Agent」精简，并自动写入长期记忆库。
    """
    if not llm_has_key():
        yield {"type": "error", "message": "未配置 LLM_API_KEY，请在 .env 中填入后重启服务。"}
        return
    if not user_question or not user_question.strip():
        yield {"type": "error", "message": "请输入你想追问的问题。"}
        return

    yield {"type": "progress", "message": "AI 正在组织通俗解答…"}
    user_prompt = build_answer_prompt(question, original_answer, user_question, memory_context=memory_context)
    full = ""
    try:
        for delta in llm_stream([{"role": "system", "content": get_answer_prompt()},
                                 {"role": "user", "content": user_prompt}]):
            full += delta
            yield {"type": "delta", "text": delta}
    except Exception as e:
        yield {"type": "error", "message": f"调用大模型失败：{e}"}
        return

    yield {"type": "result", "answer": full.strip()}

    # 自动提炼并存入长期记忆
    if auto_memory and full.strip():
        yield {"type": "progress", "message": "正在提炼答案并自动存入长期记忆…"}
        refined = ""
        try:
            for ev in refine_answer_stream(user_question, full.strip()):
                if ev.get("type") == "delta":
                    refined += ev.get("text", "")
                elif ev.get("type") == "error":
                    yield ev
                    return
        except Exception as e:
            yield {"type": "error", "message": f"提炼答案失败：{e}"}
            return
        refined = refined.strip()
        if refined:
            key = user_question
            try:
                ok = put(key, refined, "")
            except Exception as e:
                yield {"type": "error", "message": f"存入长期记忆失败：{e}"}
                return
            if ok:
                yield {"type": "saved", "key": key, "answer": refined}


def refine_answer_stream(question, answer):
    """答案提炼生成器：把完整答疑精简为可沉淀进长期记忆的精炼条目。"""
    if not llm_has_key():
        yield {"type": "error", "message": "未配置 LLM_API_KEY，请在 .env 中填入后重启服务。"}
        return
    if not (answer or "").strip():
        yield {"type": "error", "message": "没有可提炼的回答内容。"}
        return

    user_prompt = build_refine_prompt(question, answer)
    full = ""
    try:
        for delta in llm_stream([{"role": "system", "content": get_refine_prompt()},
                                 {"role": "user", "content": user_prompt}],
                                temperature=0.3):
            full += delta
            yield {"type": "delta", "text": delta}
    except Exception as e:
        yield {"type": "error", "message": f"调用大模型失败：{e}"}
        return
    yield {"type": "result", "answer": full.strip()}


def generate_answer_stream(question, resume="", jd=""):
    """答案生成生成器：为新增的面试题 / 记忆问答生成标准答案（可结合简历 + JD）。"""
    if not llm_has_key():
        yield {"type": "error", "message": "未配置 LLM_API_KEY，请在 .env 中填入后重启服务。"}
        return
    question = (question or "").strip()
    if not question:
        yield {"type": "error", "message": "请输入问题再生成答案。"}
        return

    user_prompt = build_genanswer_prompt(question, resume, jd)
    full = ""
    try:
        for delta in llm_stream([{"role": "system", "content": get_genanswer_prompt()},
                                 {"role": "user", "content": user_prompt}],
                                temperature=0.4):
            full += delta
            yield {"type": "delta", "text": delta}
    except Exception as e:
        yield {"type": "error", "message": f"调用大模型失败：{e}"}
        return
    yield {"type": "result", "answer": full.strip()}


def _parse_questions(full):
    """按 ===Q=== / ===A=== 标记切分大模型输出为结构化题目列表。"""
    blocks = re.split(r"===Q===", full)
    questions = []
    for b in blocks[1:]:
        # 截到下一个 ===A=== 之前为问题，之后为答案
        m = re.search(r"(.*?)(?:===A===)(.*)", b, re.S)
        if not m:
            continue
        q = m.group(1).strip()
        a = m.group(2).strip()
        q = re.sub(r"={3,}\s*$", "", q).strip()
        a = re.sub(r"={3,}\s*$", "", a).strip()
        if q:
            questions.append({"question": q, "answer": a})
    return questions


def parse_file_stream(content, filename=""):
    """TXT 文件解析导入生成器：把上传文本交给「文件解析导入 Agent」解析为问答 JSON 数组，
    逐条写入长期记忆库。

    事件类型：
      - progress：进度提示（解析中 / 入库中）
      - delta：解析阶段大模型输出增量（前端可展示原始 JSON 流）
      - result：解析结果 {entries: [{key, answer, extra}]}
      - saved：单条已写入长期记忆 {index, total, key, answer, extra}
      - done：全部完成 {saved, total}
      - error：错误信息
    """
    if not llm_has_key():
        yield {"type": "error", "message": "未配置 LLM_API_KEY，请在 .env 中填入后重启服务。"}
        return
    if not content or not content.strip():
        yield {"type": "error", "message": "文件内容为空，无法解析。"}
        return

    yield {"type": "progress", "message": "AI 正在解析文件内容并整理为问答条目…"}
    user_prompt = build_parse_prompt(content)
    full = ""
    try:
        for delta in llm_stream([{"role": "system", "content": get_parse_prompt()},
                                 {"role": "user", "content": user_prompt}],
                                temperature=0.3):
            full += delta
            yield {"type": "delta", "text": delta}
    except Exception as e:
        yield {"type": "error", "message": f"调用大模型失败：{e}"}
        return

    raw = full.strip()
    entries = _extract_json_entries(raw)
    if entries is None:
        yield {"type": "error", "message": "模型未返回可解析的问答 JSON，请检查文件内容或重新上传。"}
        return

    yield {"type": "result", "entries": entries}

    # 逐条写入长期记忆
    total = len(entries)
    saved = 0
    for i, ent in enumerate(entries, start=1):
        key = (ent.get("key") or "").strip()
        answer = (ent.get("answer") or "").strip()
        extra = (ent.get("extra") or "").strip()
        if not key or not answer:
            continue
        yield {"type": "progress", "message": f"正在导入第 {i}/{total} 条：{key}"}
        try:
            ok = put(key, answer, extra)
        except Exception as e:
            yield {"type": "error", "message": f"第 {i} 条存入长期记忆失败：{e}"}
            return
        if ok:
            saved += 1
            yield {"type": "saved", "index": i, "total": total, "key": key, "answer": answer, "extra": extra}

    yield {"type": "done", "saved": saved, "total": total}


def _extract_json_entries(raw):
    """从模型原始输出中提取问答 JSON 数组；兼容被 Markdown 代码块包裹的情况。"""
    # 去掉可能的 ```json ... ``` 包裹
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    try:
        data = json.loads(raw)
    except Exception:
        # 退一步：截取第一个 [ 到最后一个 ] 之间
        s = raw.find("[")
        e = raw.rfind("]")
        if s == -1 or e == -1 or e <= s:
            return None
        try:
            data = json.loads(raw[s:e + 1])
        except Exception:
            return None
    if not isinstance(data, list):
        return None
    entries = []
    for item in data:
        if not isinstance(item, dict):
            continue
        entries.append({
            "key": item.get("key", ""),
            "answer": item.get("answer", ""),
            "extra": item.get("extra", ""),
        })
    return entries

