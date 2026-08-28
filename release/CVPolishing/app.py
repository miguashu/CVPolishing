# -*- coding: utf-8 -*-
"""Flask 后端：简历优化 / 面试模拟 / 长期记忆接口（SSE 流式输出进度与结果）。"""
import json
import os
import re
import threading
import urllib.request
import urllib.parse

from flask import Flask, Response, jsonify, request, send_file, send_from_directory, session, redirect

import config
import db
import user_ctx
from optimizer import optimize_stream, auto_optimize_stream
from state_recorder import get_unfinished_tasks, get_task, cleanup_old_tasks
from llm_service import llm_vision_text
import datetime as dt
from tracer import read_trace
from score import score_stream
from compare import compare_stream
from interview import generate_interview_stream, answer_question_stream, generate_answer_stream, parse_file_stream
from memory import get_all, put, delete, clear, search, search_hybrid, rebuild_vectors
from prompt_config import load_prompts, save_prompts, PROMPT_META, DEFAULT_PROMPTS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = config.SECRET_KEY

# 会话有效期（天）：多实例共享登录态，cookie 仅持签名 session id，数据存 MySQL。
SESSION_LIFETIME_DAYS = int(os.environ.get("SESSION_LIFETIME_DAYS", "7"))


# ---------- 服务端会话：MySQL 存储，多实例共享登录态 ----------
from flask.sessions import SecureCookieSessionInterface, SessionMixin
from werkzeug.datastructures import CallbackDict
import uuid as _uuid


class MySQLSession(CallbackDict, SessionMixin):
    def __init__(self, initial=None):
        def on_update(self):
            self.modified = True
        CallbackDict.__init__(self, initial, on_update)
        self.sid = None
        self.modified = False


class MySQLSessionInterface(SecureCookieSessionInterface):
    """会话数据存 MySQL，浏览器 cookie 仅保存被 SECRET_KEY 签名的 sid。"""

    def _gen_sid(self):
        return _uuid.uuid4().hex + _uuid.uuid4().hex

    def _cookie_name(self, app):
        return app.config.get("SESSION_COOKIE_NAME", "session")

    def open_session(self, app, request):
        sid = request.cookies.get(self._cookie_name(app))
        if sid:
            data, _ = db.load_session(sid)
            if data:
                try:
                    return MySQLSession(json.loads(data))
                except Exception:
                    pass
        return MySQLSession()

    def save_session(self, app, session, response):
        name = self._cookie_name(app)
        if not session:
            if session.sid:
                db.delete_session(session.sid)
            response.delete_cookie(name)
            return
        if session.modified:
            sid = session.sid or self._gen_sid()
            session.sid = sid
            expires = dt.datetime.now() + dt.timedelta(days=SESSION_LIFETIME_DAYS)
            db.save_session(sid, json.dumps(dict(session)), expires)
        else:
            sid = session.sid
        if sid:
            response.set_cookie(
                name, sid,
                expires=dt.datetime.now() + dt.timedelta(days=SESSION_LIFETIME_DAYS),
                httponly=True, samesite="Lax",
            )


app.session_interface = MySQLSessionInterface()


# 无需登录即可访问的接口（登录态获取、登录本身、前端配置）
PUBLIC_API = {"/api/login", "/api/register", "/api/me", "/api/config", "/api/skills/registry"}


def is_admin(username=None):
    """是否为管理员（账号名命中 config.ADMIN_USERS）。"""
    uname = username or session.get("username")
    return bool(uname) and uname in config.ADMIN_USERS


@app.before_request
def _auth_and_ctx():
    """统一鉴权 + 注入用户上下文（供存储模块按用户隔离）。

    - 业务页面未登录跳登录页；/admin 仅管理员可进入。
    - /api/* 业务接口未登录返回 401；/api/admin/* 需管理员，否则 403。
    - 登录后将当前用户名写入线程级 user_ctx，memory/state/tracer 据此隔离。
    """
    username = session.get("username")
    path = request.path

    if path in ("/", "/interview", "/prompts", "/flow"):
        if not username:
            return redirect("/login")
    elif path == "/admin":
        if not username:
            return redirect("/login")
        if not is_admin():
            return redirect("/")

    if path.startswith("/api/"):
        if path in PUBLIC_API:
            pass
        elif path.startswith("/api/admin"):
            if not is_admin():
                return jsonify({"error": "无权限"}), 403
        else:
            if not username:
                return jsonify({"error": "未登录"}), 401

    user_ctx.set_user_id(username or user_ctx.DEFAULT_USER)


@app.route("/")
def index():
    return send_file(os.path.join(BASE_DIR, "index.html"))


@app.route("/interview")
def interview_page():
    return send_file(os.path.join(BASE_DIR, "interview.html"))


@app.route("/prompts")
def prompts_page():
    # 提示词/问答系统设置：管理员或 VIP 会员可见；其余用户不踢登录，仅展示无权限提示
    if is_admin() or db.is_vip_active(session.get("username", "")):
        return send_file(os.path.join(BASE_DIR, "prompts.html"))
    return '''
    <!doctype html><html lang="zh"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>无访问权限</title>
    <style>body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0f172a;color:#e2e8f0;
    display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
    .box{text-align:center;padding:32px 40px;background:#1e293b;border:1px solid #334155;border-radius:12px;max-width:420px}
    h2{color:#38bdf8;margin:0 0 12px}.tip{color:#94a3b8;line-height:1.6;margin:12px 0 20px}
    a{display:inline-block;padding:9px 18px;background:#2563eb;color:#fff;border-radius:8px;text-decoration:none}
    a.ghost{background:transparent;border:1px solid #475569;margin-left:8px}</style></head>
    <body><div class="box"><h2>提示词配置为会员专属</h2>
    <p class="tip">该页面仅对管理员与 VIP 会员开放。<br>开通 VIP 后可使用提示词配置等高级功能。</p>
    <a href="/vip">前往会员中心</a><a class="ghost" href="/">返回首页</a></div></body></html>
    ''', 403


@app.route("/flow")
def flow_page():
    return send_file(os.path.join(BASE_DIR, "flow.html"))


@app.route("/vip")
def vip_page():
    if not session.get("username"):
        return redirect("/login")
    return send_file(os.path.join(BASE_DIR, "vip.html"))


@app.route("/api/config")
def api_config():
    return jsonify({
        "has_llm_key": bool(config.LLM_API_KEY),
        "search_enabled": config.SEARCH_ENABLED,
        "model": config.LLM_MODEL,
    })


# ---------- 登录 / 会话 ----------
@app.route("/login", methods=["GET"])
def login_page():
    return send_file(os.path.join(BASE_DIR, "login.html"))


@app.route("/register", methods=["GET"])
def register_page():
    return send_file(os.path.join(BASE_DIR, "register.html"))


@app.route("/api/login", methods=["POST"])
def api_login():
    body = _json_body()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        return jsonify({"error": "请输入账号和密码"}), 400
    ok, user = db.verify_password(username, password)
    if not ok:
        return jsonify({"error": "账号或密码错误"}), 401
    session["username"] = user["username"]
    session["role"] = user["role"]
    return jsonify({"username": user["username"], "role": user["role"]})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/register", methods=["POST"])
def api_register():
    """开放注册：创建普通用户并自动登录。"""
    body = _json_body()
    uname = (body.get("username") or "").strip()
    pwd = body.get("password") or ""
    if not re.match(r"^[A-Za-z0-9_\-]{2,32}$", uname):
        return jsonify({"error": "账号仅限 2-32 位字母/数字/下划线/连字符"}), 400
    if len(pwd) < 6:
        return jsonify({"error": "密码至少 6 位"}), 400
    if db.get_user_by_username(uname):
        return jsonify({"error": "账号已存在"}), 409
    db.create_user(uname, pwd, "user")
    session["username"] = uname
    session["role"] = "user"
    return jsonify({"username": uname, "role": "user"})


@app.route("/api/me", methods=["GET"])
def api_me():
    if not session.get("username"):
        return jsonify({"user": None})
    vip = db.get_vip_info(session["username"])
    return jsonify({
        "user": {
            "username": session["username"],
            "role": session.get("role"),
            "is_admin": is_admin(),
            "vip": vip,
        },
        "vip_enabled": bool(config.VIP_ACTIVATION_CODE),
        "vip_benefits": config.VIP_BENEFITS,
    })


# ---------- 会员 / VIP ----------
@app.route("/api/vip/activate", methods=["POST"])
def api_vip_activate():
    """凭激活码开通会员。支持两类码：
       1) 全局兜底激活码 config.VIP_ACTIVATION_CODE（管理员万能码，可关）；
       2) 专属邀请码（invite_codes 表，可绑定用户/设有效期/限时天数，用过即失效）。
    """
    if not session.get("username"):
        return jsonify({"error": "未登录"}), 401
    body = _json_body()
    code = (body.get("code") or "").strip()
    if not code:
        return jsonify({"error": "请输入激活码"}), 400
    uname = session["username"]

    # 1) 专属邀请码
    inv = db.get_invite_code(code)
    if inv:
        if inv.get("used_by"):
            return jsonify({"error": "该邀请码已被使用"}), 409
        if inv.get("target_user") and inv["target_user"] != uname:
            return jsonify({"error": "该邀请码非本人专用"}), 403
        expire = inv.get("expires_at")
        if expire is not None:
            if isinstance(expire, str):
                try:
                    expire = dt.datetime.strptime(expire, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    expire = None
            if expire is not None and expire <= dt.datetime.now():
                return jsonify({"error": "邀请码已过期"}), 410
        days = inv.get("vip_days") or None
        info = db.set_vip(uname, True, days=days)
        db.consume_invite_code(code, uname)
        return jsonify({"ok": True, "vip": info, "via": "invite"})

    # 2) 全局兜底激活码（未配置则拒绝）
    if config.VIP_ACTIVATION_CODE and code == config.VIP_ACTIVATION_CODE:
        info = db.set_vip(uname, True, days=config.VIP_DURATION_DAYS)
        return jsonify({"ok": True, "vip": info, "via": "global"})

    return jsonify({"error": "激活码无效"}), 400


@app.route("/api/vip/info", methods=["GET"])
def api_vip_info():
    if not session.get("username"):
        return jsonify({"error": "未登录"}), 401
    return jsonify({"vip": db.get_vip_info(session["username"]), "benefits": config.VIP_BENEFITS})


# ---------- 专属邀请码管理（仅管理员） ----------
@app.route("/api/admin/invite", methods=["POST"])
def api_admin_invite_create():
    if not is_admin():
        return jsonify({"error": "仅管理员可操作"}), 403
    body = _json_body()
    target = (body.get("target_user") or "").strip() or None
    vip_days = int(body.get("vip_days") or 0)
    expires_hours = int(body.get("expires_hours") or 0) or None
    code = db.create_invite_code(session["username"], target_user=target, vip_days=vip_days, expires_hours=expires_hours)
    return jsonify({"ok": True, "code": code})


@app.route("/api/admin/invite", methods=["GET"])
def api_admin_invite_list():
    if not is_admin():
        return jsonify({"error": "仅管理员可操作"}), 403
    rows = db.list_invite_codes()
    for r in rows:
        for k, v in list(r.items()):
            if hasattr(v, "strftime"):
                r[k] = v.strftime("%Y-%m-%d %H:%M:%S")
    return jsonify({"invites": rows})


@app.route("/api/admin/invite/<code>", methods=["DELETE"])
def api_admin_invite_delete(code):
    if not is_admin():
        return jsonify({"error": "仅管理员可操作"}), 403
    db.delete_invite_code(code)
    return jsonify({"ok": True})


# ---------- 简历（按用户隔离） ----------
@app.route("/api/resume", methods=["GET"])
def api_resume_get():
    r = db.get_resume(session["username"])
    return jsonify(r or {"original": "", "optimized": "", "jd": ""})


@app.route("/api/resume", methods=["POST"])
def api_resume_post():
    body = _json_body()
    db.save_resume(
        session["username"],
        body.get("original", "") or "",
        body.get("optimized", "") or "",
        body.get("jd", "") or "",
    )
    return jsonify({"ok": True})


# ---------- 标准中文简历模板 PDF 导出（贴合李文昊Agent简历.pdf 版式）----------
def _build_resume_pdf(resume_text, name=""):
    """按标准中文技术简历模板（单栏、分区标题带下边线、项目符号 •）渲染为 A4 PDF。

    版式对齐参考模板：
      - 顶部：姓名（大字号）+ 意向岗位/电话/邮箱/性别年龄等基础信息多行
      - 分区大标题（核心优势/技术栈/工作经历/项目经历/教育经历）：深色加粗 + 下边线
      - 工作经历/项目下二级小标题（如「核心业务价值」「Agent架构与管控体系建设」）：加粗深色小块标题
      - 条目以 • 项目符号列表呈现
    """
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem, HRFlowable,
    )
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    # 注册中文 CID 字体（无需外部字体文件）
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        CN = "STSong-Light"
    except Exception:
        CN = "Helvetica"

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"{name or '简历'} - 标准简历",
    )

    ink = colors.HexColor("#1f2933")
    accent = colors.HexColor("#2563eb")
    sub = colors.HexColor("#52606d")
    line = colors.HexColor("#d9e2ec")

    styles = getSampleStyleSheet()
    st_title = ParagraphStyle("cv_title", parent=styles["Title"], fontName=CN,
                              fontSize=20, textColor=ink, spaceAfter=2, alignment=TA_LEFT, leading=24)
    # 基础信息行（意向岗位 / 电话邮箱 / 性别年龄）
    st_sub = ParagraphStyle("cv_sub", parent=styles["Normal"], fontName=CN,
                            fontSize=9.5, textColor=sub, spaceAfter=2, leading=14)
    # 分区大标题：深色加粗 + 下边线（贴近模板风格）
    st_h = ParagraphStyle("cv_h", parent=styles["Heading2"], fontName=CN,
                          fontSize=13, textColor=ink, spaceBefore=12, spaceAfter=3, leading=17)
    # 二级小标题（工作经历/项目下的子模块）
    st_h2 = ParagraphStyle("cv_h2", parent=styles["Normal"], fontName=CN,
                           fontSize=10.5, textColor=ink, spaceBefore=6, spaceAfter=2, leading=14)
    st_p = ParagraphStyle("cv_p", parent=styles["Normal"], fontName=CN,
                          fontSize=10, textColor=ink, leading=15, spaceAfter=3)
    st_li = ParagraphStyle("cv_li", parent=styles["Normal"], fontName=CN,
                           fontSize=10, textColor=ink, leading=14.5)

    story = []
    lines = [ln.rstrip() for ln in (resume_text or "").split("\n")]
    # 首行作为姓名/标题
    first = next((l for l in lines if l.strip()), "")
    if first.strip():
        story.append(Paragraph(_xml_escape(first.strip()), st_title))
        story.append(HRFlowable(width="100%", thickness=1.2, color=ink, spaceAfter=6, spaceBefore=2))
    rest = [l for l in lines if l.strip() != first.strip()]

    # 基础信息多行：姓名之后、遇到第一个大模块标题之前的行，都当作基础信息（sub）渲染
    def is_big_heading(s):
        s = s.strip()
        if re.match(r"^=+\s*.*\s*=+$", s):
            return True
        if len(s) <= 12 and ("：" not in s) and not s.endswith("。") and not s.endswith("."):
            if re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9 /（）()·\-]+", s) and \
               any(k in s for k in ["经历", "技能", "教育", "项目", "简介", "优势", "评价", "信息", "背景", "业绩", "工作", "实习", "荣誉", "证书", "语言"]):
                return True
        return False

    # 二级小标题：短句、无句号结尾、不含冒号、且含常见小标题词（克制启发式，降低误判）
    SUB_KEYS = ["核心", "体系", "建设", "价值", "贡献", "优化", "落地", "架构",
                "工程", "模块", "设计", "经验", "总结", "说明", "能力", "业务", "算法", "性能", "稳定性"]
    def is_sub_heading(s):
        s = s.strip()
        if len(s) < 3 or len(s) > 20:
            return False
        if s.endswith(("。", ".", "：", ":", "；", ";")):
            return False
        if "：" in s or ":" in s:
            return False
        if any(k in s for k in SUB_KEYS):
            return True
        return False

    info_lines = []
    body_start = 0
    for i, raw in enumerate(rest):
        if is_big_heading(raw):
            body_start = i
            break
        info_lines.append(raw.strip())
        body_start = i + 1
    else:
        body_start = len(rest)

    for ln in info_lines:
        if ln:
            story.append(Paragraph(_xml_escape(ln), st_sub))
    if info_lines:
        story.append(Spacer(1, 4))

    buf_para = []
    buf_list = []

    def flush():
        nonlocal buf_para, buf_list
        if buf_list:
            items = [ListItem(Paragraph(_xml_escape(x), st_li), leftIndent=6) for x in buf_list]
            story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=12,
                                      bulletColor=ink, spaceAfter=4))
            buf_list = []
        if buf_para:
            story.append(Paragraph(_xml_escape(" ".join(buf_para).strip()), st_p))
            buf_para = []

    for raw in rest[body_start:]:
        s = raw.strip()
        if not s:
            flush()
            continue
        if re.match(r"^=+\s*.*\s*=+$", s):
            flush()
            title = re.sub(r"^=+\s*|\s*=+$", "", s)
            story.append(Paragraph(_xml_escape(title), st_h))
            story.append(HRFlowable(width="100%", thickness=0.6, color=line, spaceAfter=4, spaceBefore=1))
            continue
        if is_big_heading(s):
            flush()
            story.append(Paragraph(_xml_escape(s), st_h))
            story.append(HRFlowable(width="100%", thickness=0.6, color=line, spaceAfter=4, spaceBefore=1))
            continue
        if is_sub_heading(s):
            flush()
            story.append(Paragraph(_xml_escape(s), st_h2))
            continue
        # 时间段行：如「2026.03 - 2026.07 公司名」「2017.06 - 2021.01 学校」
        # 须先于数字列表判断，避免被误判为编号列表项。
        if re.match(r"^\d{4}\.\d{1,2}\s*[-—]\s*\d{4}\.\d{1,2}", s):
            flush()
            story.append(Paragraph(_xml_escape(s), st_h2))
            continue
        if s.startswith("- ") or s.startswith("• ") or s.startswith("* "):
            buf_list.append(s[2:].strip())
        elif re.match(r"^\d+[\.、]", s):
            buf_list.append(re.sub(r"^\d+[\.、]\s*", "", s).strip())
        else:
            buf_para.append(s)
    flush()

    doc.build(story)
    buffer.seek(0)
    return buffer


@app.route("/api/resume/export-pdf", methods=["POST"])
def api_resume_export_pdf():
    # PDF 导出为会员专属功能
    if not db.is_vip_active(session.get("username", "")):
        return jsonify({"error": "下载 PDF 为会员专属功能，请开通 VIP 后使用", "need_vip": True}), 403
    body = _json_body()
    resume_text = body.get("resume", "") or ""
    name = body.get("name", "") or ""
    if not resume_text.strip():
        return jsonify({"error": "简历内容为空"}), 400
    try:
        pdf = _build_resume_pdf(resume_text, name)
    except Exception as e:
        return jsonify({"error": f"PDF 生成失败：{e}"}), 500
    from io import BytesIO
    fname = f"{name or '优化后简历'}.pdf"
    return send_file(
        pdf, mimetype="application/pdf",
        as_attachment=True, download_name=fname,
    )


# ---------- 后台用户管理（仅管理员，独立页面 /admin） ----------
@app.route("/admin", methods=["GET"])
def admin_page():
    # 后台系统仅管理员（config.ADMIN_USERS：admin / admin）可访问；
    # 非管理员不踢登录，仅展示无权限提示页
    if not is_admin():
        return '''
        <!doctype html><html lang="zh"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>无访问权限</title>
        <style>body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0f172a;color:#e2e8f0;
        display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
        .box{text-align:center;padding:32px 40px;background:#1e293b;border:1px solid #334155;border-radius:12px;max-width:420px}
        h2{color:#38bdf8;margin:0 0 12px}.tip{color:#94a3b8;line-height:1.6;margin:12px 0 20px}
        a{display:inline-block;padding:9px 18px;background:#2563eb;color:#fff;border-radius:8px;text-decoration:none}</style></head>
        <body><div class="box"><h2>后台系统为管理员专属</h2>
        <p class="tip">该页面仅对系统管理员开放。<br>如需访问请联系管理员账号。</p>
        <a href="/">返回首页</a></div></body></html>
        ''', 403
    return send_file(os.path.join(BASE_DIR, "admin.html"))


@app.route("/api/admin/users", methods=["GET"])
def api_admin_users():
    users = db.list_users()
    for u in users:
        u["created_at"] = u["created_at"].strftime("%Y-%m-%d %H:%M:%S") if u.get("created_at") else None
        u["vip"] = db.get_vip_info(u["username"])
    return jsonify(users)


@app.route("/api/admin/users", methods=["POST"])
def api_admin_create_user():
    body = _json_body()
    uname = (body.get("username") or "").strip()
    pwd = body.get("password") or ""
    role = body.get("role") or "user"
    if not re.match(r"^[A-Za-z0-9_\-]{2,32}$", uname):
        return jsonify({"error": "账号仅限 2-32 位字母/数字/下划线/连字符"}), 400
    if not pwd:
        return jsonify({"error": "密码不能为空"}), 400
    if role not in ("admin", "user"):
        role = "user"
    if db.get_user_by_username(uname):
        return jsonify({"error": "账号已存在"}), 409
    db.create_user(uname, pwd, role)
    return jsonify({"ok": True})


@app.route("/api/admin/users/<username>", methods=["DELETE"])
def api_admin_delete_user(username):
    if username == session.get("username"):
        return jsonify({"error": "不能删除当前登录账号"}), 400
    if username == "admin":
        return jsonify({"error": "不能删除初始管理员账号"}), 400
    db.delete_user(username)
    return jsonify({"ok": True})


@app.route("/api/admin/users/<username>/password", methods=["POST"])
def api_admin_reset_pwd(username):
    body = _json_body()
    pwd = body.get("password") or ""
    if not pwd:
        return jsonify({"error": "密码不能为空"}), 400
    db.reset_password(username, pwd)
    return jsonify({"ok": True})


@app.route("/api/admin/users/<username>/vip", methods=["POST"])
def api_admin_set_vip(username):
    """管理员开通/取消会员：{active: bool, days?: int}。"""
    body = _json_body()
    active = bool(body.get("active"))
    days = body.get("days")
    days = int(days) if days else None
    info = db.set_vip(username, active, days=days)
    return jsonify({"ok": True, "vip": info})


def _sse(generator):
    """把事件生成器包装为 SSE 响应。"""
    def generate():
        try:
            for ev in generator:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'处理失败：{e}'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
    return Response(generate(), mimetype="text/event-stream; charset=utf-8")


def _json_body():
    """稳健解析请求体为 dict；解析失败或为空时返回 {}（不依赖 get_json 的 mimetype 行为）。"""
    raw = request.get_data(as_text=True) or ""
    try:
        obj = json.loads(raw)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _xml_escape(text):
    """转义 XML 特殊字符，供 reportlab Paragraph 安全渲染。"""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ---------- 支付宝手机网站支付 ----------
# 受保护导入：支付宝 SDK 未安装时直接抛错，不静默降级（避免误以为支付已接通）。
try:
    from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
    from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient
    from alipay.aop.api.domain.AlipayTradeWapPayModel import AlipayTradeWapPayModel
    from alipay.aop.api.request.AlipayTradeWapPayRequest import AlipayTradeWapPayRequest
    from alipay.aop.api.util.SignatureUtils import verify_with_rsa
    _ALIPAY_SDK_OK = True
except Exception:
    _ALIPAY_SDK_OK = False


def get_alipay_client():
    """构造支付宝客户端。配置缺失或 SDK 未装时直接抛错（不静默降级）。"""
    if not _ALIPAY_SDK_OK:
        raise RuntimeError("未安装 alipay-sdk-python，请先 pip install alipay-sdk-python")
    missing = [k for k in ("ALIPAY_APP_ID", "ALIPAY_APP_PRIVATE_KEY", "ALIPAY_PUBLIC_KEY")
               if not getattr(config, k)]
    if missing:
        raise RuntimeError("支付宝配置缺失：" + ", ".join(missing))
    cfg = AlipayClientConfig()
    cfg.server_url = config.ALIPAY_GATEWAY
    cfg.app_id = config.ALIPAY_APP_ID
    cfg.app_private_key = config.ALIPAY_APP_PRIVATE_KEY
    cfg.alipay_public_key = config.ALIPAY_PUBLIC_KEY
    return DefaultAlipayClient(alipay_client_config=cfg)


@app.route("/api/pay/products", methods=["GET"])
def api_pay_products():
    """返回可购买的会员套餐列表（供前端渲染购买弹窗）与当前支付通道。"""
    if not session.get("username"):
        return jsonify({"error": "未登录"}), 401
    products = [
        {"key": k, "name": v["name"], "price": v["price"], "vip_days": v["vip_days"]}
        for k, v in config.VIP_PRODUCTS.items()
    ]
    provider = config.PAY_PROVIDER
    if provider == "alipay":
        ready = _ALIPAY_SDK_OK and bool(config.ALIPAY_APP_ID)
    else:
        ready = bool(config.AGGREGATE_MERCHANT_ID and config.AGGREGATE_KEY and config.AGGREGATE_API_URL)
    return jsonify({"products": products, "provider": provider, "ready": ready})


@app.route("/api/pay/create", methods=["POST"])
def api_pay_create():
    """为当前登录用户创建一笔支付订单，按 PAY_PROVIDER 分发到对应通道，返回支付 URL。"""
    if not session.get("username"):
        return jsonify({"error": "未登录"}), 401
    body = _json_body()
    product_key = (body.get("product_key") or "").strip()
    product = config.VIP_PRODUCTS.get(product_key)
    if not product:
        return jsonify({"error": "套餐不存在"}), 400
    uname = session["username"]
    # 生成商户订单号：时间戳 + 用户名 + 随机，保证唯一且可溯源。
    import secrets as _secrets
    out_trade_no = "{}{}{}".format(
        dt.datetime.now().strftime("%Y%m%d%H%M%S"),
        abs(hash(uname)) % 10000,
        _secrets.token_hex(4).upper(),
    )
    subject = "CVPolishing-" + product["name"]
    amount = "{:.2f}".format(product["price"])
    db.create_order(out_trade_no, uname, product_key, product["price"], subject)

    # 一键切换：按 PAY_PROVIDER 选择直连或聚合。
    if config.PAY_PROVIDER == "alipay":
        pay_url = _alipay_create(out_trade_no, subject, amount)
    else:
        pay_url = _aggregate_create(out_trade_no, subject, amount)
    return jsonify({"ok": True, "out_trade_no": out_trade_no, "pay_url": pay_url, "amount": amount})


def _alipay_create(out_trade_no, subject, amount):
    """支付宝直连：调用 alipay.trade.wap.pay 生成可跳转的支付 URL。"""
    client = get_alipay_client()
    model = AlipayTradeWapPayModel()
    model.out_trade_no = out_trade_no
    model.total_amount = amount
    model.subject = subject
    model.product_code = "QUICK_WAP_WAY"
    model.timeout_express = "{}m".format(config.ALIPAY_ORDER_TIMEOUT_MINUTES)
    req = AlipayTradeWapPayRequest()
    req.set_biz_model(model)
    req.notify_url = config.ALIPAY_NOTIFY_URL
    req.return_url = config.ALIPAY_RETURN_URL
    # page_execute 返回可直接在手机浏览器打开的支付 URL（含签名参数）。
    return client.page_execute(req, http_method="GET")


def _aggregate_create(out_trade_no, subject, amount):
    """第三方聚合收款：调用聚合下单 API 生成跳转支付 URL。"""
    if not (config.AGGREGATE_MERCHANT_ID and config.AGGREGATE_KEY and config.AGGREGATE_API_URL):
        raise RuntimeError("聚合支付配置缺失：AGGREGATE_MERCHANT_ID / AGGREGATE_KEY / AGGREGATE_API_URL")
    # 典型聚合下单参数（虎皮椒/易支付通用字段，具体以平台文档为准）。
    payload = {
        "pid": config.AGGREGATE_MERCHANT_ID,
        "out_trade_no": out_trade_no,
        "name": subject,
        "money": amount,
        "notify_url": config.AGGREGATE_NOTIFY_URL,
        "return_url": config.AGGREGATE_RETURN_URL,
        "sitename": "CVPolishing",
    }
    import hashlib as _hashlib
    # 按 key 升序拼接 + 商户密钥 做 MD5 签名（聚合平台常见做法）。
    sign_str = "&".join("{}={}".format(k, payload[k]) for k in sorted(payload)) + config.AGGREGATE_KEY
    payload["sign"] = _hashlib.md5(sign_str.encode("utf-8")).hexdigest()
    payload["sign_type"] = "MD5"
    import urllib.parse as _up
    # 聚合平台通常返回带参数的支付跳转 URL；此处按约定拼接（平台不同可微调）。
    return "{}?{}".format(config.AGGREGATE_API_URL, _up.urlencode(payload))


@app.route("/api/pay/notify", methods=["POST"])
def api_pay_notify():
    """支付异步回调（公网可达）。按 PAY_PROVIDER 验签 -> 校验 -> 开通会员。
    这是会员开通的唯一权威入口；前端仅通过 /api/pay/order 轮询做 UI 刷新。
    """
    if config.PAY_PROVIDER == "alipay":
        return _alipay_notify()
    return _aggregate_notify()


def _alipay_notify():
    """支付宝回调：验签 -> 校验状态 -> 开通会员（含幂等）。"""
    params = request.form.to_dict()
    sign = params.get("sign")
    if not sign:
        return "failure"
    verify_params = {k: v for k, v in params.items() if k not in ("sign", "sign_type")}
    if not verify_with_rsa(config.ALIPAY_PUBLIC_KEY, _sorted_query(verify_params), sign):
        return "failure"
    if params.get("trade_status") not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
        return "success"
    _grant_member(params.get("out_trade_no"), params.get("trade_no"), json.dumps(params, ensure_ascii=False))
    return "success"


def _aggregate_notify():
    """聚合支付回调：验签 -> 校验状态 -> 开通会员（含幂等）。"""
    params = request.form.to_dict()
    sign_field = config.AGGREGATE_SIGN_FIELD
    sign = params.get(sign_field)
    if not sign:
        return "fail"
    verify_params = {k: v for k, v in params.items() if k != sign_field}
    import hashlib as _hashlib
    sign_str = "&".join("{}={}".format(k, verify_params[k]) for k in sorted(verify_params)) + config.AGGREGATE_KEY
    if _hashlib.md5(sign_str.encode("utf-8")).hexdigest() != sign:
        return "fail"
    if params.get("trade_status") != config.AGGREGATE_SUCCESS_STATUS:
        return "success"
    _grant_member(params.get("out_trade_no"), params.get("trade_no"), json.dumps(params, ensure_ascii=False))
    return "success"


def _grant_member(out_trade_no, trade_no, raw):
    """按订单开通会员（幂等）：重复回调不重复开通。"""
    if not out_trade_no:
        return
    order = db.get_order(out_trade_no)
    if not order:
        return
    if order["status"] == 1:
        return
    db.mark_order_paid(out_trade_no, trade_no, raw)
    product = config.VIP_PRODUCTS.get(order["product_key"])
    if product:
        db.set_vip(order["username"], True, days=product["vip_days"])


@app.route("/api/pay/order/<out_trade_no>", methods=["GET"])
def api_pay_order_query(out_trade_no):
    """前端轮询订单状态（支付结果以回调为准，本接口仅刷新 UI）。"""
    if not session.get("username"):
        return jsonify({"error": "未登录"}), 401
    order = db.get_order(out_trade_no)
    if not order:
        return jsonify({"error": "订单不存在"}), 404
    if order["username"] != session["username"]:
        return jsonify({"error": "无权查看该订单"}), 403
    return jsonify({
        "out_trade_no": out_trade_no,
        "status": order["status"],       # 0 待付 1 已付 2 已关闭
        "paid": order["status"] == 1,
    })


def _sorted_query(params):
    """将参数字典按 key 升序拼成支付宝验签所需的待签字符串。"""
    items = sorted(params.items())
    return "&".join("{}={}".format(k, v) for k, v in items if v != "")


@app.route("/api/optimize", methods=["POST"])
def api_optimize():
    """SSE 流式优化：接收 resume + jd，逐步推送检索/优化进度与最终结果。"""
    if not config.LLM_API_KEY:
        return jsonify({"error": "未配置 LLM_API_KEY，无法优化"}), 400
    data = _json_body()
    resume = (data.get("resume") or "").strip()
    jd = (data.get("jd") or "").strip()
    version = data.get("version") or "标准优化"
    enable_search = data.get("enable_search")
    if enable_search is None:
        enable_search = config.SEARCH_ENABLED
    else:
        enable_search = bool(enable_search)

    if not resume:
        return jsonify({"error": "请填写简历原文"}), 400
    if not jd:
        return jsonify({"error": "请填写目标岗位 JD"}), 400

    # 普通用户基础优化免费上限：会员不限，非会员达上限后提醒开通 VIP
    username = session.get("username", "")
    if not db.is_vip_active(username):
        used = db.get_optimize_count(username)
        if used >= config.OPTIMIZE_FREE_LIMIT:
            return jsonify({
                "error": f"免费优化次数已用完（{config.OPTIMIZE_FREE_LIMIT} 次）。开通 VIP 可享无限次优化与 PDF 导出。",
                "need_vip": True,
                "used": used,
                "limit": config.OPTIMIZE_FREE_LIMIT,
            }), 403

    _record_question("optimize", "基础简历优化", jd[:100])
    return _sse(optimize_stream(resume, jd, version=version, enable_search=enable_search, username=username))


@app.route("/api/optimize/auto", methods=["POST"])
def api_optimize_auto():
    """SSE 智能迭代优化闭环：初始优化 -> 评分 -> 思考诊断 -> 细节优化 -> 评分 -> 监督终裁，
    循环直到评分达标（默认 90）或达最大轮次 / 安全未通过 / 连续两轮无提升。

    会员专属：非会员调用返回 403（前端引导前往会员中心）。"""
    if not db.is_vip_active(session.get("username")):
        return jsonify({"error": "智能迭代优化为会员专属功能，请前往会员中心开通 VIP", "need_vip": True}), 403
    if not config.LLM_API_KEY:
        return jsonify({"error": "未配置 LLM_API_KEY，无法优化"}), 400
    data = _json_body()
    resume = (data.get("resume") or "").strip()
    jd = (data.get("jd") or "").strip()
    version = data.get("version") or "标准优化"
    enable_search = data.get("enable_search")
    if enable_search is None:
        enable_search = config.SEARCH_ENABLED
    else:
        enable_search = bool(enable_search)
    target_score = int(data.get("target_score") or 90)
    target_score = max(60, min(100, target_score))
    max_iter = int(data.get("max_iter") or 5)
    max_iter = max(1, min(8, max_iter))

    if not resume:
        return jsonify({"error": "请填写简历原文"}), 400
    if not jd:
        return jsonify({"error": "请填写目标岗位 JD"}), 400

    task_id = data.get("task_id")  # 传入已有 task_id 可从断点续传，不传则新建任务

    _record_question("auto_optimize",
                     f"智能迭代优化（达标{target_score}分）" + ("[断点续传]" if task_id else ""),
                     jd[:100])

    return _sse(auto_optimize_stream(resume, jd, version=version,
                                    enable_search=enable_search,
                                    target_score=target_score, max_iter=max_iter,
                                    task_id=task_id))


@app.route("/api/jd/ocr", methods=["POST"])
def api_jd_ocr():
    """JD 图片识别：接收 base64 图片（或 dataURL），用多模态大模型读取 JD 文字。"""
    if not config.LLM_API_KEY:
        return jsonify({"error": "未配置 LLM_API_KEY，无法识别图片"}), 400
    data = _json_body()
    image = (data.get("image") or "").strip()
    mime = (data.get("mime") or "image/png").strip()
    if not image:
        return jsonify({"error": "缺少图片数据"}), 400
    # 兼容 dataURL 形式
    if "," in image and image.strip().lower().startswith("data:"):
        header, b64 = image.split(",", 1)
        mime_match = re.search(r"data:([^;]+);", header)
        if mime_match:
            mime = mime_match.group(1)
        image = b64
    system = ("你是一位专业的招聘 JD 文字识别与整理助手。请仔细识别图片中的岗位招聘信息（JD），"
              "完整提取其中的岗位名称、工作职责、任职要求、公司福利等全部文字，"
              "尽量保持原文结构，不要臆造图片中不存在的内容；若图片含表格，请转为清晰的条目文本。")
    prompt = "请识别并提取这张图片中的岗位 JD 文字内容，尽量完整、准确。"
    try:
        text = llm_vision_text(system, image, mime, prompt, temperature=0.2)
    except Exception as e:
        return jsonify({"error": f"图片识别失败：{e}"}), 500
    return jsonify({"text": text})


@app.route("/api/trace/<run_id>", methods=["GET"])
def api_trace(run_id):
    """读取某次迭代优化的链路日志（供前端可视化与 LangSmith 关联查看）。"""
    trace = read_trace(run_id)
    if not trace:
        return jsonify({"error": "未找到对应链路日志"}), 404
    return jsonify(trace)


# ---------- 任务管理与断点续传 ----------
@app.route("/api/task/list", methods=["GET"])
def api_task_list():
    """列出所有未完成（running/created）的迭代优化任务，供前端选择续传。"""
    tasks = get_unfinished_tasks()
    return jsonify(tasks)


@app.route("/api/task/<task_id>", methods=["GET"])
def api_task_detail(task_id):
    """读取指定任务状态（步骤记录 + 断点 + 错误日志）。"""
    t = get_task(task_id)
    if not t:
        return jsonify({"error": "未找到该任务"}), 404
    return jsonify(t)


@app.route("/api/task/<task_id>", methods=["DELETE"])
def api_task_delete(task_id):
    """删除指定任务的状态记录（放弃断点续传时不恢复）。"""
    import os
    from state_recorder import STATE_DIR
    path = os.path.join(STATE_DIR, f"task_{task_id}.json")
    if not os.path.exists(path):
        return jsonify({"error": "未找到该任务"}), 404
    try:
        os.remove(path)
    except Exception as e:
        return jsonify({"error": f"删除失败：{e}"}), 500
    return jsonify({"ok": True, "task_id": task_id})


@app.route("/api/task/cleanup", methods=["POST"])
def api_task_cleanup():
    """清理 7 天前的已完成任务文件。"""
    cleanup_old_tasks(keep_days=7)
    return jsonify({"ok": True})


# ---------- 用户问题时间戳记录（按用户隔离） ----------
def _question_ts_path():
    return os.path.join(user_ctx.user_data_dir(), "question_timestamps.jsonl")


def _record_question(kind, text, context=""):
    """追加一条用户问题时间戳记录到 JSONL 文件（当前用户）。"""
    record = {
        "ts": dt.datetime.utcnow().isoformat() + "Z",
        "kind": kind,           # "generate" | "ask" | "refine" 等
        "text": (text or "")[:500],
        "context": (context or "")[:300],
    }
    try:
        path = _question_ts_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 静默失败，不阻塞主流程


@app.route("/api/question/timestamps", methods=["GET"])
def api_question_timestamps():
    """列出最近的用户问题时间戳（最近 200 条）。"""
    try:
        with open(_question_ts_path(), "r", encoding="utf-8") as f:
            lines = f.readlines()[-200:]
    except FileNotFoundError:
        return jsonify([])
    records = []
    for line in lines:
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    records.reverse()  # 最新在前
    return jsonify(records)


@app.route("/api/score", methods=["POST"])
def api_score():
    """SSE 流式评分：接收 jd + 简历正文（可选 after 为优化后版本，做前后对比）。"""
    if not config.LLM_API_KEY:
        return jsonify({"error": "未配置 LLM_API_KEY，无法评分"}), 400
    data = request.get_json(force=True)
    jd = (data.get("jd") or "").strip()
    before = (data.get("before") or "").strip()
    after = (data.get("after") or "").strip()
    if not jd:
        return jsonify({"error": "请填写目标岗位 JD"}), 400
    if not before:
        return jsonify({"error": "请填写待评分的简历"}), 400
    return _sse(score_stream(jd, before, resume_after=after or None))


@app.route("/api/interview/generate", methods=["POST"])
def api_interview_generate():
    """SSE 流式生成面试题：接收 resume + jd，逐步推送生成进度与最终题目列表。"""
    if not config.LLM_API_KEY:
        return jsonify({"error": "未配置 LLM_API_KEY，无法生成面试题"}), 400
    data = request.get_json(force=True)
    resume = (data.get("resume") or "").strip()
    jd = (data.get("jd") or "").strip()
    count = int(data.get("count") or 8)
    # 非会员面试生成题目上限（会员不限）
    if not db.is_vip_active(session["username"]):
        count = min(count, config.INTERVIEW_FREE_LIMIT)
    enable_search = data.get("enable_search")
    if enable_search is None:
        enable_search = config.SEARCH_ENABLED
    else:
        enable_search = bool(enable_search)
    auto_save = bool(data.get("auto_save", True))
    _record_question("generate", f"生成{count}道面试题", jd[:100])
    return _sse(generate_interview_stream(resume, jd, count=count, enable_search=enable_search, auto_save=auto_save))


@app.route("/api/interview/ask", methods=["POST"])
def api_interview_ask():
    """SSE 流式追问答疑：接收 question + original_answer + user_question + query + auto_memory。

    query 用于从长期记忆中语义召回最相关的 top-K 条，避免把整份记忆塞进 prompt；
    auto_memory=True 时，回答完成后自动提炼并存入长期记忆库。
    """
    if not config.LLM_API_KEY:
        return jsonify({"error": "未配置 LLM_API_KEY，无法答疑"}), 400
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    original_answer = (data.get("original_answer") or "").strip()
    user_question = (data.get("user_question") or "").strip()
    query = (data.get("query") or user_question).strip()
    auto_memory = bool(data.get("auto_memory", False))
    _record_question("ask", user_question[:200], question[:100])
    relevant = search_hybrid(query, top_k=config.VECTOR_TOP_K)
    memory_context = _build_memory_context(relevant)
    return _sse(answer_question_stream(question, original_answer, user_question,
                                       memory_context=memory_context, auto_memory=auto_memory))


@app.route("/api/generate-answer", methods=["POST"])
def api_generate_answer():
    """SSE 流式答案生成：为新增的面试题 / 记忆问答生成标准答案（结合简历 + JD 上下文）。"""
    if not config.LLM_API_KEY:
        return jsonify({"error": "未配置 LLM_API_KEY，无法生成答案"}), 400
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    resume = (data.get("resume") or "").strip()
    jd = (data.get("jd") or "").strip()
    return _sse(generate_answer_stream(question, resume, jd))


@app.route("/api/interview/parse-file", methods=["POST"])
def api_parse_file():
    """SSE 流式 TXT 文件解析导入：把上传的纯文本交给解析 Agent 整理为问答 JSON 并逐条入库。"""
    if not config.LLM_API_KEY:
        return jsonify({"error": "未配置 LLM_API_KEY，无法解析文件"}), 400
    data = request.get_json(force=True)
    content = (data.get("content") or "").strip()
    filename = (data.get("filename") or "").strip()
    if not content:
        return jsonify({"error": "文件内容为空"}), 400
    return _sse(parse_file_stream(content, filename=filename))


@app.route("/api/memory/search", methods=["GET"])
def api_memory_search():
    """混合检索长期记忆：?q=查询&k=条数。

    倒排索引关键词命中优先（kw_score），语义相似度（score）补充，按 key 去重合并。
    """
    q = request.args.get("q", "").strip()
    try:
        k = int(request.args.get("k", config.VECTOR_TOP_K))
    except ValueError:
        k = config.VECTOR_TOP_K
    if not q:
        return jsonify({"error": "缺少查询参数 q"}), 400
    return jsonify(search_hybrid(q, top_k=k))


def _build_memory_context(entries):
    """把检索到的记忆条目（list[{key,answer,extra,score}]）拼成可读文本，供大模型引用。"""
    if not entries:
        return ""
    parts = []
    total = 0
    for e in entries:
        ans = (e.get("answer") or "").strip()
        extra = (e.get("extra") or "").strip()
        block = f"【问题】{e.get('key', '')}\n答案：{ans}"
        if extra:
            block += f"\n答疑补充：{extra}"
        if total + len(block) > 6000:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


@app.route("/api/memory", methods=["GET"])
def api_memory_get():
    """读取全部长期记忆（key-value 结构，剥离向量，前端无需关心）。"""
    data = get_all()
    clean = {k: {
        "answer": v.get("answer", ""),
        "extra": v.get("extra", ""),
        "_seq": v.get("_seq", 0),
        "_ts": v.get("_ts", 0),
    } for k, v in data.items()}
    return jsonify(clean)


@app.route("/api/memory", methods=["POST"])
def api_memory_post():
    """写入 / 更新一条记忆：{key, answer, extra}。"""
    data = request.get_json(force=True)
    key = (data.get("key") or "").strip()
    answer = data.get("answer") or ""
    extra = data.get("extra") or ""
    if not key:
        return jsonify({"error": "key 不能为空"}), 400
    if not put(key, answer, extra):
        return jsonify({"error": "保存失败"}), 500
    return jsonify({"ok": True, "key": key})


@app.route("/api/memory", methods=["DELETE"])
def api_memory_delete():
    """删除一条记忆：?key=。"""
    key = request.args.get("key", "").strip()
    if not key:
        return jsonify({"error": "key 不能为空"}), 400
    ok = delete(key)
    return jsonify({"ok": ok, "key": key})


@app.route("/api/memory/clear", methods=["POST"])
def api_memory_clear():
    """清空全部记忆。"""
    clear()
    return jsonify({"ok": True})


def _parse_json_memory(text):
    """解析导出/通用的记忆 JSON：支持 {key:{answer,extra}} 与 [{key,answer,extra}]。"""
    obj = json.loads(text)
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                items.append({
                    "key": k,
                    "answer": v.get("answer", "") or "",
                    "extra": v.get("extra", "") or "",
                })
            else:
                items.append({"key": k, "answer": str(v), "extra": ""})
    elif isinstance(obj, list):
        for it in obj:
            if isinstance(it, dict):
                key = it.get("key") or it.get("question") or it.get("title") or ""
                items.append({
                    "key": key,
                    "answer": it.get("answer", "") or "",
                    "extra": it.get("extra", "") or "",
                })
    return items


def _parse_md_memory(text, fallback_key):
    """解析 Markdown 问答：按 #/##/### 标题切块，标题为问题，块内正文为答案。

    兼容本系统「导出 Markdown」格式（含 **答案** / **答疑补充** 标记）。
    """
    lines = text.splitlines()
    blocks = []
    cur_title = None
    buf = []

    def flush():
        if cur_title is not None:
            blocks.append((cur_title, "\n".join(buf).strip()))
        elif buf:
            blocks.append((fallback_key, "\n".join(buf).strip()))

    for line in lines:
        m = re.match(r"^\s*#{1,6}\s+(.*)$", line)
        if m:
            flush()
            # 去掉如 "1. " 的序号前缀
            cur_title = re.sub(r"^\d+\.\s*", "", m.group(1)).strip()
            buf = []
        else:
            buf.append(line)
    flush()

    items = []
    for title, body in blocks:
        if not body:
            continue
        ans = body
        extra = ""
        am = re.search(r"\*\*答案\*\*(.*?)(\*\*答疑补充\*\*|$)", body, re.S)
        if am:
            ans = am.group(1).strip()
            em = re.search(r"\*\*答疑补充\*\*(.*)$", body, re.S)
            if em:
                extra = em.group(1).strip()
        items.append({"key": title, "answer": ans, "extra": extra})
    return items


@app.route("/api/memory/import", methods=["POST"])
def api_memory_import():
    """从 JSON / Markdown 文件批量导入长期记忆（含向量）。

    文件以 multipart 上传（字段 file）。逐条 PUT，自动生成向量并落盘；
    与删除/新增走同一套存储，向量库天然同步。
    """
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "未收到文件"}), 400
    name = f.filename.lower()
    try:
        raw = f.read().decode("utf-8-sig", "ignore")
    except Exception as e:
        return jsonify({"error": f"文件读取失败：{e}"}), 400

    if name.endswith(".json"):
        try:
            items = _parse_json_memory(raw)
        except Exception as e:
            return jsonify({"error": f"JSON 解析失败：{e}"}), 400
    elif name.endswith((".md", ".markdown", ".txt")):
        base = os.path.splitext(f.filename)[0]
        items = _parse_md_memory(raw, base)
    else:
        return jsonify({"error": "仅支持 .json / .md / .txt 文件"}), 400

    if not items:
        return jsonify({"ok": True, "imported": 0, "note": "未解析到任何问答对"})
    imported = 0
    for it in items:
        if it.get("key") and put(it["key"], it.get("answer", ""), it.get("extra", "")):
            imported += 1
    return jsonify({"ok": True, "imported": imported})


# ---------- 提示词配置（可视化编辑 / 保存） ----------
@app.route("/api/prompts", methods=["GET"])
def api_prompts_get():
    """返回当前生效提示词、默认值与展示元信息。"""
    return jsonify({
        "prompts": load_prompts(),
        "defaults": DEFAULT_PROMPTS,
        "meta": PROMPT_META,
    })


@app.route("/api/prompts", methods=["POST"])
def api_prompts_post():
    """保存提示词覆盖（仅管理员，写 data/prompts.json）。"""
    if not is_admin():
        return jsonify({"error": "仅管理员可修改提示词"}), 403
    data = request.get_json(force=True)
    try:
        saved = save_prompts(data)
    except Exception as e:
        return jsonify({"error": f"保存失败：{e}"}), 500
    return jsonify({"ok": True, "count": len(saved)})


@app.route("/api/compare", methods=["POST"])
def api_compare():
    """SSE 流式优化对比：接收 jd + 优化前(before) + 优化后(after)，产出文字化诊断。"""
    if not config.LLM_API_KEY:
        return jsonify({"error": "未配置 LLM_API_KEY，无法对比"}), 400
    data = request.get_json(force=True)
    jd = (data.get("jd") or "").strip()
    before = (data.get("before") or "").strip()
    after = (data.get("after") or "").strip()
    return _sse(compare_stream(jd, before, after))


# ---------- Skills 中心：发现 / 查询 / 安装 / 联网导入 ----------
SKILLS_FILE = os.path.join(BASE_DIR, "data", "skills.json")
SKILLS_DIR = os.path.join(BASE_DIR, "skills")  # 导入的技能正文落盘目录


# 内置能力的「使用说明 / 何时使用」，与 PROMPT_META 的 key 一一对应。
SKILL_USAGE = {
    "optimize_system": "在用户点击「简历优化」、希望把原始简历对标目标 JD 做一轮精准改写时使用；是主流程入口。",
    "compare_system": "在用户想看清「优化前 vs 优化后」差异、提升点与风险点时使用，配合评分做对比分析。",
    "score_system": "在用户需要一份可量化的简历体检报告（HR/ATS/面试官视角、五维度打分）时使用。",
    "interview_system": "在用户准备面试、需要基于简历 + JD 生成真实面试题与通俗答案时使用。",
    "answer_system": "在用户对某道已生成的面试题/答案继续追问、需要把抽象概念讲通俗时使用。",
    "refine_system": "在「问我」得到完整回答后，需要把啰嗦内容提炼成可沉淀进长期记忆库的精炼答案时使用。",
    "genanswer_system": "在用户新增面试题或想为某条记忆生成标准答案时使用，产出通顺口语化回答。",
    "parse_system": "在用户上传 TXT 问答素材、希望自动拆分为结构化问答并导入记忆库时使用。",
    "interview_plan_system": "在生成面试题之前，用于先规划候选主题并对长期记忆去重，避免重复出题。",
    "think_system": "在「智能迭代优化」闭环中、每轮评分后使用，分析薄弱维度并给出下一步优化指引。",
    "resume_refine_system": "在迭代闭环中、依据评分与思考指引对简历做针对性细节精修，同时保护关键内容。",
    "supervise_system": "在迭代闭环终裁时使用，判定是否达标、做安全检查（防死循环 + 防关键内容被篡改）。",
}


def _default_skills():
    """兜底清单：仅从 PROMPT_META 生成内置能力，保证文件缺失时界面不空白。"""
    skills = []
    for k, meta in PROMPT_META.items():
        skills.append({
            "id": k,
            "name": meta.get("title", k),
            "category": "内置能力",
            "source": "内置",
            "builtin": True,
            "installed": True,
            "version": "1.0",
            "desc": meta.get("desc", ""),
            "detail": meta.get("desc", ""),
            "usage": SKILL_USAGE.get(k, ""),
            "tags": [],
        })
    return skills


def load_skills():
    if not os.path.exists(SKILLS_FILE):
        data = _default_skills()
        save_skills(data)
        return data
    try:
        with open(SKILLS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _default_skills()
    # 兜底：内置能力若缺少 usage，从 SKILL_USAGE 补回（兼容旧版 skills.json）
    changed = False
    for s in data:
        if s.get("builtin") and not s.get("usage") and s.get("id") in SKILL_USAGE:
            s["usage"] = SKILL_USAGE[s["id"]]
            changed = True
    if changed:
        save_skills(data)
    return data


def save_skills(data):
    os.makedirs(os.path.dirname(SKILLS_FILE), exist_ok=True)
    with open(SKILLS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_skill(s, installed=False):
    """把任意来源的技能字典收敛为本应用的统一结构。"""
    return {
        "id": str(s.get("id") or s.get("name") or "").strip(),
        "name": s.get("name") or s.get("id") or "未命名技能",
        "category": s.get("category") or "通用",
        "source": s.get("source") or "在线",
        "builtin": False,
        "installed": bool(installed),
        "version": s.get("version") or "1.0",
        "desc": s.get("desc") or "",
        "detail": s.get("detail") or s.get("desc") or "",
        "usage": s.get("usage") or "",
        "tags": s.get("tags") or [],
        "content": s.get("content") or "",
    }


def _installed_ids():
    return {s.get("id") for s in load_skills() if s.get("installed")}


@app.route("/api/skills", methods=["GET"])
def api_skills():
    """列出全部 Skill；支持关键字搜索（名称/描述/分类/标签）。"""
    q = (request.args.get("q") or "").strip().lower()
    skills = load_skills()
    if q:
        def hit(s):
            blob = " ".join([
                s.get("name", ""), s.get("desc", ""), s.get("category", ""),
                s.get("detail", ""), " ".join(s.get("tags", [])),
            ]).lower()
            return q in blob
        skills = [s for s in skills if hit(s)]
    return jsonify({"skills": skills, "total": len(skills)})


@app.route("/api/skills/<sid>", methods=["GET"])
def api_skill_detail(sid):
    """查询单个 Skill 的详情。"""
    for s in load_skills():
        if s.get("id") == sid:
            return jsonify(s)
    return jsonify({"error": "未找到该 Skill"}), 404


@app.route("/api/skills/<sid>/install", methods=["POST"])
def api_skill_install(sid):
    """用户确认后安装（标记 installed=true 并持久化）。"""
    skills = load_skills()
    for s in skills:
        if s.get("id") == sid:
            s["installed"] = True
            save_skills(skills)
            return jsonify({"ok": True, "skill": s})
    return jsonify({"error": "未找到该 Skill"}), 404


@app.route("/api/skills/<sid>/uninstall", methods=["POST"])
def api_skill_uninstall(sid):
    """卸载市场 Skill（内置能力不可卸载）。"""
    skills = load_skills()
    for s in skills:
        if s.get("id") == sid:
            if s.get("builtin"):
                return jsonify({"error": "内置能力不可卸载，仅可查看"}), 400
            s["installed"] = False
            save_skills(skills)
            return jsonify({"ok": True, "skill": s})
    return jsonify({"error": "未找到该 Skill"}), 404


# 内置示例 registry：模拟一个远程技能仓库，用于演示「联网查询 + 一键导入」完整链路。
# 真实部署时把 config.SKILL_REGISTRY_URL 指向任意返回 {"skills":[...]} 的远程地址即可。
SAMPLE_REGISTRY = [
    {
        "id": "ats_keywords",
        "name": "ATS 关键词命中 Skill",
        "category": "简历优化",
        "source": "在线仓库",
        "version": "1.1",
        "desc": "分析 JD 高频词与行业黑话，自动把关键词自然嵌入简历，提升 ATS 初筛通过率。",
        "usage": "当用户目标岗位有明确的行业术语/证书要求、且担心简历被 ATS 机筛刷掉时使用。",
        "tags": ["ATS", "关键词", "机筛"],
        "detail": "读取 JD 提取高频关键词与硬技能，判断简历缺失项，在不堆砌的前提下把关键词写入项目经历与技能栏。",
        "content": "# ATS 关键词命中 Skill\n\n## 何时使用\n当用户目标岗位有明确行业术语/证书要求、担心简历被 ATS 机筛刷掉时使用。\n\n## 做法\n1. 提取 JD 高频词与硬技能；2. 对比简历缺失项；3. 自然嵌入项目经历与技能栏，避免关键词堆砌。\n",
    },
    {
        "id": "star_story",
        "name": "STAR 经历故事化 Skill",
        "category": "简历优化",
        "source": "在线仓库",
        "version": "1.0",
        "desc": "把干瘪的职责描述改写为 STAR 结构（情境-任务-行动-结果）的量化故事，增强可读性。",
        "usage": "当用户简历充满「负责 xxx」式职责罗列、缺乏成果量化与情境时使用。",
        "tags": ["STAR", "故事化", "量化"],
        "detail": "识别可量化的项目，按 STAR 重组叙述，补充可验证的指标与业务影响。",
        "content": "# STAR 经历故事化 Skill\n\n## 何时使用\n简历充满职责罗列、缺乏成果量化与情境时使用。\n\n## 做法\n按 STAR 重组项目叙述，补充可验证指标与业务影响。\n",
    },
    {
        "id": "jd_tailor",
        "name": "JD 定向裁剪 Skill",
        "category": "简历优化",
        "source": "在线仓库",
        "version": "2.0",
        "desc": "针对不同 JD 生成差异化版本简历，突出最相关的经历，弱化无关内容。",
        "usage": "当用户要海投多个方向差异较大的岗位、需要一份简历多版本定向裁剪时使用。",
        "tags": ["JD匹配", "多版本", "裁剪"],
        "detail": "按 JD 优先级重排经历顺序、调整措辞权重，生成面向该岗位的专属版本。",
        "content": "# JD 定向裁剪 Skill\n\n## 何时使用\n海投多个方向差异较大的岗位、需要一份简历多版本定向裁剪时使用。\n\n## 做法\n按 JD 优先级重排经历、调整措辞权重。\n",
    },
    {
        "id": "interview_coach",
        "name": "面试话术陪练 Skill",
        "category": "面试模拟",
        "source": "在线仓库",
        "version": "1.2",
        "desc": "在模拟面试基础上，给出每题的回答框架、避坑提示与追问应对话术。",
        "usage": "当用户已生成面试题、希望在正式面试前做话术打磨与临场应对训练时使用。",
        "tags": ["话术", "陪练", "面试"],
        "detail": "为每道面试题提供回答结构、常见扣分点与追问应对，模拟真实对话节奏。",
        "content": "# 面试话术陪练 Skill\n\n## 何时使用\n已生成面试题、希望在正式面试前做话术打磨时使用。\n\n## 做法\n为每题提供回答结构、避坑提示与追问应对。\n",
    },
]


@app.route("/api/skills/registry", methods=["GET"])
def api_skill_registry():
    """示例在线 registry：返回可被联网查询与一键导入的技能清单。"""
    installed = _installed_ids()
    return jsonify({"skills": [normalize_skill(s, s["id"] in installed) for s in SAMPLE_REGISTRY]})


@app.route("/api/skills/online", methods=["GET"])
def api_skills_online():
    """联网查询技能：从 config.SKILL_REGISTRY_URL 拉取远程仓库并归一化返回。"""
    q = (request.args.get("q") or "").strip().lower()
    url = config.SKILL_REGISTRY_URL
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return jsonify({"skills": [], "error": f"无法连接技能 registry（{url}）：{e}"})
    remote = data.get("skills", []) if isinstance(data, dict) else []
    installed = _installed_ids()
    skills = [normalize_skill(s, s.get("id") in installed) for s in remote]
    if q:
        skills = [s for s in skills if q in " ".join([
            s["name"], s["desc"], s["category"], s["detail"], s["usage"], " ".join(s["tags"])
        ]).lower()]
    return jsonify({"skills": skills, "total": len(skills), "source": url})


@app.route("/api/skills/import", methods=["POST"])
def api_skill_import():
    """一键导入：把联网查询到的技能写入本地 skills.json 并落盘 SKILL.md。"""
    body = request.get_json(silent=True) or {}
    raw = body.get("skill")
    if not raw or not (raw.get("id") or raw.get("name")):
        return jsonify({"error": "缺少技能数据"}), 400
    skill = normalize_skill(raw, installed=True)
    if not skill["id"]:
        return jsonify({"error": "技能 id 不能为空"}), 400

    skills = load_skills()
    existing = next((s for s in skills if s.get("id") == skill["id"]), None)
    if existing:
        # 已存在则覆盖内容，并标记为已安装
        existing.update({k: v for k, v in skill.items() if k != "builtin"})
        existing["installed"] = True
    else:
        skills.append(skill)
    save_skills(skills)

    # 落盘技能正文到 skills/<id>/SKILL.md，便于复用与版本管理
    skill_dir = os.path.join(SKILLS_DIR, skill["id"])
    os.makedirs(skill_dir, exist_ok=True)
    md = skill["content"] or (
        f"# {skill['name']}\n\n"
        f"> 来源：{skill['source']} · 版本 {skill['version']}\n\n"
        f"## 功能\n{skill['detail'] or skill['desc']}\n\n"
        f"## 何时使用\n{skill['usage']}\n"
    )
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(md)

    return jsonify({"ok": True, "skill": skill, "path": os.path.relpath(skill_dir, BASE_DIR)})


def _rebuild_vectors_bg():
    """后台补齐记忆向量：逐条调用本地 Ollama 较慢，放到子线程避免阻塞服务启动。"""
    try:
        n = rebuild_vectors()
        if n:
            print(f"[memory] 已按本地模型 {config.EMBEDDING_MODEL} 补齐 {n} 条记忆向量")
    except Exception as e:
        print(f"[memory] 向量补齐跳过：{e}")


if __name__ == "__main__":
    try:
        db.init_schema()
    except Exception as e:
        print(f"[init_schema] 失败（忽略）：{e}")
    threading.Thread(target=_rebuild_vectors_bg, daemon=True).start()
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
