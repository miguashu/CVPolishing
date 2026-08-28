# -*- coding: utf-8 -*-
"""发布包打包脚本：复制代码 + 携带本地数据 + 去除敏感信息。

用法：python pack_release.py
产物：release/CVPolishing/（发布目录）+ release/CVPolishing-<日期>.zip

做的事情：
1. 按白名单复制运行代码（排除测试残留、日志、缓存、私有运行数据）
2. 对复制进包的 init_db.py / config.py / start.bat / README.md 做脱敏改写
3. 携带「本地 Ollama 引用数据」：data/memory.json（含向量）、skills.json、prompts.json、SKILLS_GUIDE.md
4. 用 mysqldump 导出本地 MySQL 的 cvpolishing 库为 db_init.sql（接收方导入后即可恢复数据）
5. 生成干净的 .env.example（真实密钥绝不入包）
6. 打 zip
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(BASE_DIR, "release")
PKG_DIR = os.path.join(OUT_ROOT, "CVPolishing")

# ---------- 白名单：进入发布包的文件 ----------
CODE_FILES = [
    "app.py", "agents.py", "compare.py", "config.py", "db.py", "embed.py",
    "init_db.py", "interview.py", "llm_service.py", "memory.py",
    "qdrant_store.py", "migrate_qdrant.py",
    "optimizer.py", "prompt.py", "prompt_compare.py", "prompt_config.py",
    "prompt_interview.py", "score.py", "search_service.py", "state_recorder.py",
    "tracer.py", "user_ctx.py", "requirements.txt",
    "index.html", "interview.html", "login.html", "register.html",
    "vip.html", "prompts.html", "flow.html", "admin.html",
    "start.bat", "deploy.bat", "deploy.sh", "docker-compose.yml", "Dockerfile",
    ".env.example", ".gitignore",
]
# 静态资源整目录
STATIC_DIRS = ["static"]
# 技能目录（当前为空目录，保留结构）
SKILL_DIRS = ["skills"]
# 数据目录（Ollama 引用数据 / 公共配置，排除用户私有运行数据）
DATA_FILES = ["memory.json", "memory.json.seq", "skills.json", "prompts.json", "SKILLS_GUIDE.md"]

# ---------- 脱敏改写规则（对复制进包的文件副本执行） ----------
def sanitize_text(text):
    # 移除真实 API 密钥痕迹（.env.example 本来就为空，防残留）
    text = re.sub(r"sk-[A-Za-z0-9]{16,}", "sk-REPLACE_WITH_YOUR_KEY", text)
    text = re.sub(r"lsv2_[A-Za-z0-9_]{10,}", "lsv2_REPLACE", text)
    # init_db.py：去掉个人种子账号 liwenhao / 密码 146641（整块移除，避免生成重复 admin）
    liwenhao_block = (
        "    if not db.get_user_by_username(\"liwenhao\"):\n"
        "        db.create_user(\"liwenhao\", \"146641\", \"user\")\n"
        "        print(\"[init] 已创建用户 liwenhao\")\n"
        "    else:\n"
        "        print(\"[init] 用户 liwenhao 已存在，跳过\")\n"
    )
    text = text.replace(liwenhao_block, "")
    text = text.replace("liwenhao", "admin")
    text = text.replace("146641", "admin123!")
    # 管理员集合默认值去掉个人账号
    text = text.replace('"admin,liwenhao"', '"admin"')
    text = text.replace("admin / liwenhao", "admin")
    text = text.replace("admin、liwenhao", "admin")
    text = text.replace("如 liwenhao", "如 用户名")
    # 修正 init_db.py 在 liwenhao→admin 替换后产生的误导性 docstring（不生成多余账号，仅注释）
    text = text.replace("种子管理员 admin（admin123!）与普通用户 admin（admin123!）",
                        "种子管理员 admin（admin123!）")
    # docker-compose healthcheck 里残留的真实密码默认值
    text = text.replace("qunima213!", "root")
    return text


def ensure(p):
    os.makedirs(p, exist_ok=True)


def copy_code():
    ensure(PKG_DIR)
    for f in CODE_FILES:
        src = os.path.join(BASE_DIR, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(PKG_DIR, f))
    for d in STATIC_DIRS + SKILL_DIRS:
        src = os.path.join(BASE_DIR, d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(PKG_DIR, d), dirs_exist_ok=True)
    # 数据目录：仅公共数据
    ensure(os.path.join(PKG_DIR, "data"))
    for f in DATA_FILES:
        src = os.path.join(BASE_DIR, "data", f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(PKG_DIR, "data", f))
    # 部署说明文档
    doc_src = os.path.join(BASE_DIR, "release_docs", "部署说明.md")
    if os.path.isfile(doc_src):
        shutil.copy2(doc_src, os.path.join(PKG_DIR, "部署说明.md"))


def sanitize_copied():
    for f in CODE_FILES:
        p = os.path.join(PKG_DIR, f)
        if not os.path.isfile(p):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            txt = fh.read()
        new_txt = sanitize_text(txt)
        if new_txt != txt:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(new_txt)
            print("  [脱敏] " + f)
    # static / skills 目录下的文本文件同样脱敏
    for sub in STATIC_DIRS + SKILL_DIRS:
        base = os.path.join(PKG_DIR, sub)
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for name in files:
                if not name.endswith((".js", ".html", ".css", ".md", ".json", ".py")):
                    continue
                p = os.path.join(root, name)
                with open(p, "r", encoding="utf-8") as fh:
                    txt = fh.read()
                new_txt = sanitize_text(txt)
                if new_txt != txt:
                    with open(p, "w", encoding="utf-8") as fh:
                        fh.write(new_txt)
                    print("  [脱敏] " + os.path.join(sub, name))
    # README.md 单独改写
    readme = os.path.join(PKG_DIR, "README.md")
    if os.path.isfile(readme):
        with open(readme, "r", encoding="utf-8") as fh:
            txt = fh.read()
        with open(readme, "w", encoding="utf-8") as fh:
            fh.write(sanitize_text(txt))


def dump_mysql():
    """mysqldump 导出 cvpolishing 库。使用环境变量 MYSQL_PWD 避免命令行暴露密码。"""
    from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
    out_path = os.path.join(PKG_DIR, "db_init.sql")
    dump = r"D:\ruanjian\MYSQL\bin\mysqldump.exe"
    if not os.path.isfile(dump):
        print("  [警告] 未找到 mysqldump（D:\\ruanjian\\MYSQL\\bin），跳过数据库导出")
        return
    env = dict(os.environ)
    env["MYSQL_PWD"] = DB_PASSWORD
    cmd = [
        dump, "--host=" + str(DB_HOST), "--port=" + str(DB_PORT),
        "--user=" + DB_USER,
        "--default-character-set=utf8mb4",
        "--single-transaction", "--routines", "--triggers",
        "--databases", DB_NAME,
    ]
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            subprocess.run(cmd, env=env, check=True, stdout=fh,
                           stderr=subprocess.DEVNULL)
        size = os.path.getsize(out_path)
        print(f"  [数据库] 已导出 {DB_NAME} → db_init.sql ({size/1024:.1f} KB)")
    except Exception as e:
        print("  [警告] 数据库导出失败：" + str(e))


def sanitize_sql():
    """对 db_init.sql 做 SQL 级脱敏：
    - 清空 sessions / orders / invite_codes：运行态或个人态数据，接收方不需要，且含真实用户名
    - 清空 users：避免泄露真实账号与密码哈希（接收方由 init_db.py 创建 admin 演示账号）
    - 保留 memories（长期记忆演示数据）与 resumes（简历样例）
    只删除对应表的 INSERT 数据行，保留建表语句。
    """
    path = os.path.join(PKG_DIR, "db_init.sql")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines(keepends=True)
    clear_tables = {"users", "sessions", "orders", "invite_codes", "optimize_counts"}
    # 演示数据表：记忆/简历归属统一改为 admin，接收方用 admin 登录即可看到全部演示数据，
    # 同时避免原始用户名（liwenhao/qunima/paytest 等）随包外泄。
    demo_tables = {"memories", "resumes"}
    # 待替换的原始用户名（含密码哈希中的用户名不在此列，users 表已整体清空）
    user_names = ["liwenhao", "qunima", "paytest", "normuser01", "freshuser99", "testreg04", "verify_A", "verify_B"]
    out = []
    skip_tbl = None
    for ln in lines:
        m = re.match(r"\s*INSERT INTO `(\w+)`", ln)
        if m:
            tbl = m.group(1)
            if tbl in clear_tables:
                skip_tbl = tbl
                out.append("-- [脱敏] 已清空 " + tbl + " 数据（保留表结构）\n")
                continue
            if tbl in demo_tables:
                # 逐条替换数据行中的原始用户名为 admin
                for u in user_names:
                    ln = ln.replace("'" + u + "'", "'admin'")
                # resumes 按 user_id 唯一：多用户简历脱敏后都变 admin 会撞 uk_user，
                # 只保留第一条，避免导入失败；memories 的 uk_user_key 是 (user_id, mem_key)，
                # 不同记忆不同 key 不会冲突，无需去重。
                if tbl == "resumes":
                    m2 = re.match(r"(\s*INSERT INTO `resumes` VALUES )(.*)(;\s*)$", ln, re.S)
                    if m2:
                        first_val = m2.group(2).split("),(")[0] + ")"
                        ln = m2.group(1) + first_val + m2.group(3)
                out.append(ln)
                continue
        if skip_tbl:
            # 多行 INSERT 的续行（数据部分），直接跳过
            if ln.rstrip("\n").endswith(";"):
                skip_tbl = None
            continue
        out.append(ln)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(out)
    print("  [数据库] 已对 db_init.sql 脱敏（清空 users/sessions/orders/invite_codes/optimize_counts 数据）")


def write_env_example():
    """生成干净的 .env.example：真实密钥留空，接收方自行填写。"""
    src = os.path.join(BASE_DIR, ".env.example")
    dst = os.path.join(PKG_DIR, ".env.example")
    if os.path.isfile(src):
        shutil.copy2(src, dst)
        with open(dst, "r", encoding="utf-8") as fh:
            txt = fh.read()
        txt = sanitize_text(txt)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(txt)
    # 若没有 .env.example 则生成基础模板
    if not os.path.isfile(dst):
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(_ENV_TEMPLATE)
    print("  [配置] .env.example 已就绪（接收方填入自己的 LLM_API_KEY 等）")


_ENV_TEMPLATE = """# ===== CVPolishing 环境变量（接收方部署必填项）=====
# 复制本文件为 .env 并填入以下配置后启动：
LLM_API_BASE=https://api.agicto.cn/v1
LLM_API_KEY=sk-REPLACE_WITH_YOUR_KEY
LLM_MODEL=deepseek-v4-pro
LLM_VISION_MODEL=gpt-4o
PORT=5090
SEARCH_ENABLED=true

# MySQL（安装版默认 root/root；若端口不同请改 DB_PORT）
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=root
DB_NAME=cvpolishing

# 会话签名密钥（生产环境务必改为随机强密钥）
SECRET_KEY=please-change-this-random-secret
# 会员激活码（留空关闭入口）
VIP_ACTIVATION_CODE=VIP2026
# 管理员账号（逗号分隔）
ADMIN_USERS=admin

# 向量检索：本地 Ollama（需先 ollama pull bge-m3）
EMBEDDING_PROVIDER=ollama
EMBEDDING_API_BASE=http://localhost:11434
EMBEDDING_API_KEY=ollama
EMBEDDING_MODEL=bge-m3
"""


def make_zip():
    stamp = datetime.now().strftime("%Y%m%d")
    zip_path = os.path.join(OUT_ROOT, f"CVPolishing-{stamp}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PKG_DIR):
            dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
            for f in files:
                if f.endswith((".pyc", ".log", ".err")):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, OUT_ROOT)
                zf.write(full, rel)
    size = os.path.getsize(zip_path)
    print(f"  [打包] 已生成 {zip_path} ({size/1024/1024:.2f} MB)")


def main():
    print("== CVPolishing 发布包打包 ==")
    if os.path.isdir(PKG_DIR):
        shutil.rmtree(PKG_DIR)
    print("[1/5] 复制代码…")
    copy_code()
    print("[2/5] 脱敏改写…")
    sanitize_copied()
    print("[3/5] 导出并脱敏 MySQL 数据…")
    dump_mysql()
    sanitize_sql()
    print("[4/5] 生成 .env.example…")
    write_env_example()
    print("[5/5] 压缩发布包…")
    make_zip()
    print("完成！发布目录：", PKG_DIR)


if __name__ == "__main__":
    main()
