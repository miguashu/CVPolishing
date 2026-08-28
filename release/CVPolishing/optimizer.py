# -*- coding: utf-8 -*-
"""简历优化编排：前置联网检索参考素材 -> 流式调用大模型产出优化简历。

复用 search_service 的检索能力与 OpenAI 兼容接口，输出按 ===优化后简历=== / ===优化说明=== 切分。
"""
import json
import re
import time

from config import SEARCH_ENABLED
from llm_service import llm_stream, llm_has_key
from prompt import get_system_prompt, build_user_prompt, build_think_prompt, build_refine_user_prompt, build_supervise_prompt
from prompt_config import load_prompts
from agents import AgentSpec, run_subagents
from score import score_once
from search_service import search_references_stream, build_reference_context
from state_recorder import StateRecorder, MAX_ERROR_RETRY
from tracer import Tracer
import db


def optimize_stream(resume, jd, version="标准优化", enable_search=None, username=None):
    """主流程生成器：逐步 yield SSE 事件。

    事件类型：
      - progress：检索 / 优化进度提示
      - delta：大模型输出文本增量（最终由前端按分隔标记切分）
      - result：最终结果 {resume, notes, references}
      - error：错误信息
    """
    if enable_search is None:
        enable_search = SEARCH_ENABLED
    if not llm_has_key():
        yield {"type": "error", "message": "未配置 LLM_API_KEY，请在 .env 中填入后重启服务。"}
        return

    job_title = _guess_job_title(jd)

    references = ""
    ref_titles = []
    if enable_search:
        for ev in search_references_stream(job_title):
            if ev.get("type") == "result":
                items = ev.get("items", [])
                references = build_reference_context(items)
                ref_titles = [it.get("title", "") for it in items]
            else:
                yield ev
    else:
        yield {"type": "progress", "step": "search", "message": "已跳过前置联网检索（离线模式），直接基于 JD 匹配优化。"}

    # 检索为空时不影响优化，仅提示
    if enable_search and not references:
        yield {"type": "progress", "step": "search", "message": "未检索到有效参考素材，将基于通用高分简历逻辑优化。"}

    # 优化前基线评分：放到后台线程异步执行，不阻塞主优化流程，
    # 整体耗时降到 max(评分, 优化) 而非两者串行之和。评分结果在优化完成后吐出。
    pre_score_holder = {"value": None, "done": False, "error": None}

    def _run_prescore():
        try:
            pre_score_holder["value"] = score_once(jd, resume)
        except Exception as e:
            pre_score_holder["error"] = e
        finally:
            pre_score_holder["done"] = True

    import threading
    prescore_thread = threading.Thread(target=_run_prescore, daemon=True)
    prescore_thread.start()

    user_prompt = build_user_prompt(resume, jd, references, version=version)
    yield {"type": "progress", "step": "optimize", "message": "AI 正在逐行拆解 JD 并优化简历…"}

    full = ""
    try:
        for delta in llm_stream([{"role": "system", "content": get_system_prompt()},
                                 {"role": "user", "content": user_prompt}]):
            full += delta
            yield {"type": "delta", "text": delta}
    except Exception as e:
        yield {"type": "error", "message": f"调用大模型失败：{e}"}
        return

    resume_text, notes = _split_output(full)
    yield {
        "type": "result",
        "resume": resume_text,
        "notes": notes,
        "references": ref_titles,
    }

    # 优化完成后再补出优化前基线评分卡（评分线程此时基本已结束，join 不阻塞）
    try:
        prescore_thread.join(timeout=120)
    except Exception:
        pass
    if pre_score_holder["error"]:
        yield {"type": "progress", "step": "prescore", "message": f"优化前评分暂不可用：{pre_score_holder['error']}"}
    elif pre_score_holder["value"]:
        pre_score = pre_score_holder["value"]
        yield {
            "type": "score",
            "phase": "pre",
            "total": pre_score.get("total", 0),
            "dimensions": pre_score.get("dimensions", []),
            "summary": pre_score.get("summary", ""),
        }

    # 普通用户完成一次基础优化后自增计数（会员不限，不计数）
    if username and not db.is_vip_active(username):
        try:
            db.inc_optimize_count(username)
        except Exception:
            pass


def _guess_job_title(jd):
    """从 JD 中粗略提取岗位名，用于检索词。优先匹配常见岗位后缀。"""
    text = (jd or "").strip()
    for kw in ["工程师", "专员", "经理", "主管", "总监", "分析师", "设计师", "运营", "产品", "开发", "销售", "助理", "架构师"]:
        m = re.search(r"([\u4e00-\u9fa5]{1,8}" + kw + r")", text)
        if m:
            return m.group(1)
    # 退而求其次：取前 12 个非空白字符
    return text[:12].replace("\n", " ").strip() or "简历优化"


def _split_output(full):
    """按分隔标记切分大模型输出为简历正文与优化说明。

    兼容模型省略「===优化说明===」仅以「优化说明：」引导的情形，并清理残留标记。
    """
    # 简历正文：从 ===优化后简历=== 取到说明段之前
    m = re.search(r"===优化后简历===(.*?)(?:===优化说明===|优化说明[:：])", full, re.S)
    if m:
        resume_text = m.group(1).strip()
    else:
        m2 = re.search(r"===优化后简历===(.*)", full, re.S)
        resume_text = m2.group(1).strip() if m2 else full.strip()
    # 清理可能残留的尾随分隔符
    resume_text = re.sub(r"={3,}\s*$", "", resume_text).strip()

    # 优化说明：从说明段标记取到文末
    nm = re.search(r"(?:===优化说明===|优化说明[:：])\s*(.*)", full, re.S)
    notes = nm.group(1).strip() if nm else ""
    return resume_text, notes


def _extract_json(raw):
    """从模型输出中尽力解析 JSON 对象。兼容被 ```json 代码块包裹、首尾杂质。"""
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


def _score_breakdown_text(score):
    """把评分字典格式化为可读明细文本，供细节优化 Agent 提示词内嵌。"""
    lines = []
    for d in score.get("dimensions", []):
        lines.append(f"- {d.get('name', '')}：{d.get('score', 0)} 分 —— {d.get('comment', '')}")
    lines.append(f"总分：{score.get('total', 0)}")
    if score.get("summary"):
        lines.append(f"总评：{score.get('summary', '')}")
    return "\n".join(lines)


def auto_optimize_stream(resume, jd, version="标准优化", enable_search=None,
                         target_score=90, max_iter=5, task_id=None):
    """智能迭代优化闭环生成器：初始优化 -> 评分 -> 思考诊断 -> 细节优化 -> 评分 -> 监督终裁，
    循环直到达标（>=target_score）或达最大轮次 / 安全未通过 / 连续两轮无提升。

    事件类型（在 optimize_stream 基础上新增）：
      - think：思考 Agent 诊断 {iteration, weak, issues, guidance}
      - supervise：监督 Agent 终裁 {iteration, decision, safe, reason, risk}
      - score：每轮评分 {iteration, total, dimensions, summary}
      - iteration_result：每轮产出的简历 {iteration, resume, notes}
      - result：最终 {resume, notes, references, iterations, target_score,
                     target_met, final_score, trace_id, task_id}

    task_id：传入已有任务 ID 可从断点续传；不传则自动创建新任务。
    """
    if enable_search is None:
        enable_search = SEARCH_ENABLED
    if not llm_has_key():
        yield {"type": "error", "message": "未配置 LLM_API_KEY，请在 .env 中填入后重启服务。"}
        return

    tracer = Tracer("auto_optimize")
    prompts = load_prompts()

    # ------ StateRecorder：创建或加载任务状态 ------
    sr = StateRecorder(task_id=task_id)
    task_id = sr.task_id
    completed = sr.get_completed_state()
    cp = completed["checkpoint"]

    yield {"type": "progress", "step": "state_init",
           "message": f"任务 {task_id} 启动中…",
           "task_id": task_id,
           "status": completed["status"],
           "cp_iteration": cp.get("iteration", 0) if cp else 0}

    job_title = cp.get("job_title") or _guess_job_title(jd)

    # 检索阶段：断点在 init 或 search 之前才执行
    references = cp.get("references", "") if cp else ""
    ref_titles = cp.get("ref_titles", []) if cp else []
    if cp and cp.get("phase") not in ("init", "search"):
        # 已过检索阶段，跳过
        yield {"type": "progress", "step": "search",
               "message": "从断点恢复，跳过已完成的前置检索。"}
    else:
        if enable_search:
            search_ok = False
            for ev in search_references_stream(job_title):
                if ev.get("type") == "result":
                    items = ev.get("items", [])
                    references = build_reference_context(items)
                    ref_titles = [it.get("title", "") for it in items]
                    search_ok = True
                else:
                    yield ev
            sr.step(0, "search", input_text=job_title,
                    output_text=references[:300],
                    status="ok" if search_ok else "ok")
        else:
            yield {"type": "progress", "step": "search", "message": "已跳过前置联网检索（离线模式）。"}
            sr.step(0, "search", input_text=job_title, output_text="", status="skipped")

    # 从断点恢复运行状态
    # 已确认完成（completed）或已出错（error）的任务不续传：确认完成后下次点击一律从最开始进行。
    cp_resumable = cp and cp.get("phase") not in ("init", "search") and completed["status"] not in ("completed", "error")
    if cp_resumable:
        current = cp.get("current", resume)
        iterations = cp.get("iterations", [])
        prev_total = cp.get("prev_total", -1)
        no_improve = cp.get("no_improve", 0)
        start_iter = cp.get("iteration", 0) + 1
        yield {"type": "progress", "step": "resume",
               "message": f"从断点恢复：已完成 {cp.get('iteration', 0)} 轮，从第 {start_iter} 轮继续。",
               "task_id": task_id}
    else:
        current = resume
        iterations = []
        prev_total = -1
        no_improve = 0
        start_iter = 1

    sr.start(resume, jd, version, enable_search, target_score, max_iter)

    # 原始简历基线评分：在迭代开始前对原始简历做一次整体评分，
    # 作为「原始简历评分」在迭代过程下方展示，让用户看清起点。
    yield {"type": "progress", "step": "baseline",
           "message": "AI 正在对原始简历做整体基线评分…"}
    try:
        baseline_score = score_once(jd, resume)
        yield {
            "type": "baseline_score",
            "total": baseline_score.get("total", 0),
            "dimensions": baseline_score.get("dimensions", []),
            "summary": baseline_score.get("summary", ""),
        }
    except Exception as e:
        baseline_score = None
        yield {"type": "progress", "step": "baseline", "message": f"原始简历评分暂不可用：{e}"}

    for it in range(start_iter, max_iter + 1):
        # —— 产出简历（首轮完整优化，之后按评分细节优化）——
        if it == 1:
            system = prompts["optimize_system"]
            user = build_user_prompt(current, jd, references, version=version)
            step_name = "optimize"
        else:
            system = prompts["resume_refine_system"]
            last = iterations[-1]
            score_breakdown = _score_breakdown_text(last["score"])
            guidance = last.get("guidance", "")
            user = build_refine_user_prompt(current, jd, score_breakdown, guidance)
            step_name = "refine"

        yield {"type": "progress", "step": step_name, "iteration": it,
               "message": f"第 {it} 轮：AI 正在{'完整优化' if it == 1 else '按评分细节优化'}简历…"}
        full = ""
        t0 = time.time()
        try:
            for delta in llm_stream([{"role": "system", "content": system},
                                     {"role": "user", "content": user}]):
                full += delta
                yield {"type": "delta", "text": delta, "iteration": it}
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            tokens_est = _estimate_tokens(user, full)
            sr.step(it, step_name, input_text=user, output_text="",
                    status="error", error=e, duration_ms=elapsed, tokens=tokens_est)
            yield {"type": "node_metrics", "iteration": it, "name": step_name,
                   "status": "error", "duration_ms": elapsed, "tokens": tokens_est, "error": str(e)}
            if sr.should_terminate:
                yield {"type": "error",
                       "message": f"第 {it} 轮 {step_name} 连续失败 {MAX_ERROR_RETRY} 次，监督 Agent 终止任务。"
                                  f"原因：{sr.last_error_info.get('error','')}"}
            else:
                yield {"type": "error", "message": f"第 {it} 轮调用大模型失败：{e}"}
            tracer.finish(status="error")
            sr.finish("error")
            return

        resume_text, notes = _split_output(full)
        current = resume_text
        elapsed_ms = int((time.time() - t0) * 1000)
        tokens_est = _estimate_tokens(user, full)
        tracer.step(step_name, input_text=user[:1500], output_text=resume_text[:1500])
        sr.step(it, step_name, input_text=user, output_text=resume_text[:800],
                status="ok", duration_ms=elapsed_ms, tokens=tokens_est)
        yield {"type": "node_metrics", "iteration": it, "name": step_name,
               "status": "ok", "duration_ms": elapsed_ms, "tokens": tokens_est}
        yield {"type": "iteration_result", "iteration": it, "resume": resume_text, "notes": notes}

        # —— 评分 ——
        yield {"type": "progress", "step": "score", "iteration": it,
               "message": f"第 {it} 轮：AI 正在评分…"}
        t0 = time.time()
        try:
            score = score_once(jd, resume_text)
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            sr.step(it, "score", input_text=resume_text[:500], output_text="",
                    status="error", error=e, duration_ms=elapsed)
            yield {"type": "node_metrics", "iteration": it, "name": "score",
                   "status": "error", "duration_ms": elapsed, "tokens": {},
                   "error": str(e)}
            if sr.should_terminate:
                yield {"type": "error",
                       "message": f"第 {it} 轮评分连续失败 {MAX_ERROR_RETRY} 次，监督 Agent 终止任务。"}
            else:
                yield {"type": "error", "message": f"评分失败：{e}"}
            tracer.finish(status="error")
            sr.finish("error")
            return
        elapsed_ms = int((time.time() - t0) * 1000)
        score_json = json.dumps(score, ensure_ascii=False)
        tokens_est = _estimate_tokens(resume_text[:500], score_json)
        tracer.step("score", output_text=score_json[:1500])
        sr.step(it, "score", input_text=resume_text[:500],
                output_text=score_json[:500],
                status="ok", duration_ms=elapsed_ms, tokens=tokens_est)
        yield {"type": "node_metrics", "iteration": it, "name": "score",
               "status": "ok", "duration_ms": elapsed_ms, "tokens": tokens_est}
        yield {"type": "score", "iteration": it, "total": score["total"],
               "dimensions": score["dimensions"], "summary": score.get("summary", "")}

        # —— 思考 Agent 与 监督/结果 Agent：子 Agent 沙盒并行 ——
        yield {"type": "progress", "step": "think+supervise", "iteration": it,
               "message": f"第 {it} 轮：思考 Agent 与监督 Agent 并行分析中…"}
        prev_guidance = iterations[-1].get("guidance", "") if iterations else ""
        t0 = time.time()
        results = run_subagents(
            AgentSpec(
                name="think",
                system=prompts["think_system"],
                user=build_think_prompt(resume_text, jd, score),
                temperature=0.3,
                parser="json",
                with_usage=True,
            ),
            AgentSpec(
                name="supervise",
                system=prompts["supervise_system"],
                user=build_supervise_prompt(
                    score, it, max_iter, notes, prev_guidance),
                temperature=0.1,
                parser="json",
                with_usage=True,
            ),
        )
        # 合并：思考 Agent 结果
        think_res = results.get("think", {})
        think_raw = think_res.get("content", "")
        think = think_res.get("parsed") or {}
        think_usage = think_res.get("usage", {})
        if not think_res.get("ok"):
            think = {"guidance": "", "issues": [f"思考 Agent 调用失败：{think_res.get('error', '未知')}"]}
        elapsed = int((time.time() - t0) * 1000)
        if think_res.get("ok"):
            sr.step(it, "think", input_text=resume_text[:500],
                    output_text=think_raw[:500], status="ok",
                    duration_ms=elapsed, tokens=think_usage)
            yield {"type": "node_metrics", "iteration": it, "name": "think",
                   "status": "ok", "duration_ms": elapsed, "tokens": think_usage}
        else:
            sr.step(it, "think", input_text=resume_text[:500], output_text="",
                    status="error", error=think_res.get("error"), duration_ms=elapsed)
            yield {"type": "node_metrics", "iteration": it, "name": "think",
                   "status": "error", "duration_ms": elapsed, "tokens": {},
                   "error": think_res.get("error")}
        tracer.step("think", input_text=resume_text[:1000], output_text=think_raw[:1500])
        yield {"type": "think", "iteration": it,
               "weak": think.get("weak_dimensions", []),
               "issues": think.get("issues", []),
               "guidance": think.get("guidance", ""),
               "project_explanations": think.get("project_explanations", "")}

        # —— 合并：监督 / 结果 Agent 结果（已在上方沙盒并行调用）——
        sup_res = results.get("supervise", {})
        sup_raw = sup_res.get("content", "")
        sup = sup_res.get("parsed") or {}
        sup_usage = sup_res.get("usage", {})
        if not sup_res.get("ok"):
            sup = {"decision": "stop", "safe": True,
                   "reason": f"监督 Agent 调用失败，保守结束：{sup_res.get('error', '未知')}"}
        if sup_res.get("ok"):
            sr.step(it, "supervise", input_text="",
                    output_text=sup_raw[:500], status="ok",
                    duration_ms=elapsed, tokens=sup_usage)
            yield {"type": "node_metrics", "iteration": it, "name": "supervise",
                   "status": "ok", "duration_ms": elapsed, "tokens": sup_usage}
        else:
            sr.step(it, "supervise", input_text="", output_text="",
                    status="error", error=sup_res.get("error"), duration_ms=elapsed)
            yield {"type": "node_metrics", "iteration": it, "name": "supervise",
                   "status": "error", "duration_ms": elapsed, "tokens": {},
                   "error": sup_res.get("error")}
        tracer.step("supervise", output_text=sup_raw[:1500])
        yield {"type": "supervise", "iteration": it,
               "decision": sup.get("decision", "stop"),
               "safe": bool(sup.get("safe", True)),
               "reason": sup.get("reason", ""),
               "risk": sup.get("risk", "")}

        iterations.append({
            "iteration": it, "score": score, "notes": notes,
            "resume": resume_text,
            "guidance": think.get("guidance", ""), "issues": think.get("issues", []),
            "weak": think.get("weak_dimensions", []),
            "project_explanations": think.get("project_explanations", ""),
            "decision": sup.get("decision", "stop"),
            "safe": bool(sup.get("safe", True)),
            "reason": sup.get("reason", ""),
            "risk": sup.get("risk", ""),
        })

        # —— 每轮完成后保存断点 ——
        sr.checkpoint(iteration=it, phase="supervise", current=current,
                      iterations=iterations, prev_total=prev_total,
                      no_improve=no_improve,
                      references=references, ref_titles=ref_titles,
                      job_title=job_title)

        # —— 终止判定 ——
        if not sup.get("safe", True):
            yield {"type": "progress", "step": "done", "iteration": it,
                   "message": "安全检查未通过，终止迭代并保留当前最优结果。"}
            break
        if score["total"] >= target_score:
            yield {"type": "progress", "step": "done", "iteration": it,
                   "message": f"已达标（{score['total']} ≥ {target_score}），结束迭代。"}
            break
        if it >= max_iter:
            yield {"type": "progress", "step": "done", "iteration": it,
                   "message": f"已达最大轮次 {max_iter}，终止迭代。"}
            break
        # 防死循环：连续两轮总分无提升则停止
        if prev_total >= 0 and score["total"] <= prev_total:
            no_improve += 1
        else:
            no_improve = 0
        prev_total = score["total"]
        if no_improve >= 2:
            yield {"type": "progress", "step": "done", "iteration": it,
                   "message": "连续两轮分数无提升，终止迭代避免死循环。"}
            break

    final = iterations[-1] if iterations else None
    tracer.finish(status="ok", extra={
        "iterations": len(iterations),
        "final_score": final["score"]["total"] if final else 0,
    })
    sr.finish("completed")
    yield {
        "type": "result",
        "resume": current,
        "notes": final["notes"] if final else "",
        "references": ref_titles,
        "iterations": iterations,
        "target_score": target_score,
        "target_met": bool(final and final["score"]["total"] >= target_score),
        "final_score": final["score"]["total"] if final else 0,
        "trace_id": tracer.run_id,
        "task_id": task_id,
    }


def _estimate_tokens(prompt_text, completion_text):
    """根据字符数估算 token 用量（中英混合：约 1.5 字符/token）。"""
    p = len(prompt_text or "")
    c = len(completion_text or "")
    pt = max(1, int(p / 1.8))
    ct = max(1, int(c / 1.8))
    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
