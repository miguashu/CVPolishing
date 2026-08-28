# -*- coding: utf-8 -*-
"""JD 生成工作经历模块：根据目标岗位 JD 反推生成匹配的工作经历条目。

一次 llm_stream 内按 ===KEYWORDS=== / ===EXP n=== 分段输出：
  - 关键词段先到，解析后立即 yield jd_keywords（失败仅跳过可视化，不影响经历生成）
  - 每个经历段切分完整后立即 yield experience（逐条流式渲染，无需等全部生成完）

流式切段状态机：token 流可能把分隔符拆到两个 chunk。段头一旦拼全立即从 buffer
消费，last_header 记录当前段类型跨轮保持；buffer 始终只保留当前段的未闭合内容，
拼上后续 token 后再切，避免半截内容或空卡片。

兼容模式：若模型未按分段格式输出（如整体 JSON 数组），流结束后按 JSON 数组容错解析。
系统提示词集中管理在 prompt_config（key: jd_resume_system），运行期实时生效。
"""
import json
import re

from llm_service import llm_stream, llm_has_key
from prompt_config import load_prompts


SEG_RE = re.compile(r"^===\s*(KEYWORDS|EXP\s*\d+)\s*===$", re.M | re.I)


def get_jd_resume_prompt():
    """运行时读取当前生效的 JD 生成经历系统提示词。"""
    return load_prompts()["jd_resume_system"]


def build_jd_resume_prompt(jd, resume="", count=3, target_role=""):
    """拼装 JD 生成工作经历用户提示词。"""
    ctx = ""
    if (resume or "").strip():
        ctx = ("# 用户现有简历（供提取真实技术栈 / 项目背景，优先复用）\n"
               + resume.strip() + "\n\n")
    target = ""
    if (target_role or "").strip():
        target = "\n# 目标岗位\n" + target_role.strip()
    return ("%s# 目标岗位 JD\n%s%s\n\n# 任务\n"
            "请根据上述 JD 反推生成 %d 条匹配的工作经历条目，时间线从远到近、相邻无空档；"
            "公司名一律填 xxx。严格按系统提示词要求的分段格式输出。"
            % (ctx, jd.strip(), target, count))


def _parse_keywords_block(body):
    """解析关键词段文本 -> {"keywords": [...], "role": "..."}；失败返回 None。"""
    body = (body or "").strip()
    if not body:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", body, re.S)
    if m:
        body = m.group(1).strip()
    s, e = body.find("{"), body.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        data = json.loads(body[s:e + 1])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    kws = data.get("keywords") or []
    if not isinstance(kws, list):
        kws = []
    kws = [str(k).strip() for k in kws if str(k).strip()]
    return {"keywords": kws[:12], "role": str(data.get("role") or "").strip()}


def _parse_exp_block(body):
    """解析单个经历段文本 -> {company, role, period, bullets}；失败返回 None。

    兼容两种内容：行文本（首行「公司 | 岗位 | 时间」，后续 - bullet）或 JSON 对象。
    """
    body = (body or "").strip()
    if not body:
        return None
    if body.startswith("{"):
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                return _normalize_exp(data)
        except Exception:
            pass
    lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    if not lines:
        return None
    head = lines[0]
    parts = [p.strip() for p in re.split(r"\s*[·|]\s*", head)]
    company = (parts[0] if parts else "") or "xxx"
    role = parts[1].strip() if len(parts) > 1 else ""
    period = parts[2].strip() if len(parts) > 2 else ""
    bullets = []
    for ln in lines[1:]:
        b = re.sub(r"^[-•·]\s*", "", ln).strip()
        if b:
            bullets.append(b)
    if not role and not bullets:
        return None
    return {"company": company, "role": role, "period": period, "bullets": bullets}


def _normalize_exp(item):
    """把 JSON 经历对象规整为 {company, role, period, bullets}；无效返回 None。"""
    if not isinstance(item, dict):
        return None
    company = str(item.get("company") or "xxx").strip() or "xxx"
    role = str(item.get("role") or "").strip()
    period = str(item.get("period") or "").strip()
    bullets = item.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = []
    bullets = [str(b).strip() for b in bullets if str(b).strip()]
    if not role and not bullets:
        return None
    return {"company": company, "role": role, "period": period, "bullets": bullets}


def _parse_experiences(raw):
    """兼容模式：整段文本按 JSON 数组解析（模型未按分段格式输出时兜底）。"""
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    try:
        data = json.loads(raw)
    except Exception:
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
    experiences = []
    for item in data:
        exp = _normalize_exp(item)
        if exp:
            experiences.append(exp)
    return experiences


def generate_experiences_stream(jd, resume="", count=3, target_role=""):
    """SSE 事件生成器：一次 llm_stream 内按段切流，逐条产出工作经历。

    事件：
      - status：开始生成
      - jd_keywords：JD 关键词段解析完成 {keywords, role}（失败仅跳过可视化）
      - experience：每个经历段切分完整立即 {index, company, role, period, bullets}
      - result：全部完成 {experiences, total}
      - error：错误
    """
    if not llm_has_key():
        yield {"type": "error", "message": "未配置 LLM_API_KEY，请在 .env 中填入后重启服务。"}
        return
    if not (jd or "").strip():
        yield {"type": "error", "message": "请先填写目标岗位 JD。"}
        return

    yield {"type": "status", "message": "AI 正在按 JD 要求生成 %d 条工作经历…" % count}

    user_p = build_jd_resume_prompt(jd, resume, count, target_role)
    buf = ""                # 当前段未闭合内容（含可能的段头碎片）
    last_header = None      # 最近一次完整出现的段头类型（跨轮保持）
    experiences = []
    segment_mode = False    # 是否出现过段头（区分分段/JSON 两种模式）

    def emit_ready():
        """把 buf 中已拼全的段头及其前一段切出；段头立即消费。"""
        nonlocal buf, last_header, segment_mode
        out = []
        while True:
            m = SEG_RE.search(buf)
            if not m:
                break
            if last_header:
                out.append((last_header, buf[:m.start()]))
            last_header = m.group(1).strip().lower()
            segment_mode = True
            buf = buf[m.end():]
        return out

    try:
        for delta in llm_stream([{"role": "system", "content": get_jd_resume_prompt()},
                                 {"role": "user", "content": user_p}],
                                temperature=0.4):
            buf += delta
            for header, body in emit_ready():
                if header == "keywords":
                    kw = _parse_keywords_block(body)
                    if kw:
                        yield {"type": "jd_keywords", "keywords": kw.get("keywords", []), "role": kw.get("role", "")}
                elif len(experiences) < count:
                    exp = _parse_exp_block(body)
                    if exp:
                        experiences.append(exp)
                        yield {"type": "experience",
                               "index": len(experiences) - 1,
                               "company": exp["company"], "role": exp["role"],
                               "period": exp["period"], "bullets": exp["bullets"]}
    except Exception as e:
        yield {"type": "error", "message": "调用大模型失败：%s" % e}
        return

    # 流结束：处理 buf 中剩余的完整段 + 最后一个未闭合段
    for header, body in emit_ready():
        if header == "keywords":
            kw = _parse_keywords_block(body)
            if kw:
                yield {"type": "jd_keywords", "keywords": kw.get("keywords", []), "role": kw.get("role", "")}
        elif len(experiences) < count:
            exp = _parse_exp_block(body)
            if exp:
                experiences.append(exp)
                yield {"type": "experience",
                       "index": len(experiences) - 1,
                       "company": exp["company"], "role": exp["role"],
                       "period": exp["period"], "bullets": exp["bullets"]}
    if last_header and buf.strip():
        if last_header == "keywords":
            kw = _parse_keywords_block(buf)
            if kw:
                yield {"type": "jd_keywords", "keywords": kw.get("keywords", []), "role": kw.get("role", "")}
        elif len(experiences) < count:
            exp = _parse_exp_block(buf)
            if exp:
                experiences.append(exp)
                yield {"type": "experience",
                       "index": len(experiences) - 1,
                       "company": exp["company"], "role": exp["role"],
                       "period": exp["period"], "bullets": exp["bullets"]}

    # 兼容：未识别分段格式，整段按 JSON 数组解析
    if not experiences and not segment_mode:
        fallback = _parse_experiences(buf)
        if fallback:
            for exp in fallback[:count]:
                experiences.append(exp)
                yield {"type": "experience",
                       "index": len(experiences) - 1,
                       "company": exp["company"], "role": exp["role"],
                       "period": exp["period"], "bullets": exp["bullets"]}

    if not experiences:
        yield {"type": "error", "message": "模型未返回可解析的工作经历，请重试或减少生成条数。"}
        return
    yield {"type": "result", "experiences": experiences, "total": len(experiences)}
