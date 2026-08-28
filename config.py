# -*- coding: utf-8 -*-
"""配置模块：从 .env 读取运行参数，未提供时使用内置默认值。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env():
    """极简 .env 解析，避免引入额外依赖。"""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_env()

# 大模型问答引擎（OpenAI 兼容接口，可指向 CodeBuddy / OpenAI / 本地 Ollama 等）
LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://api.agicto.cn/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-pro")
# 多模态视觉模型（JD 图片识别等），可单独指定，不指定则复用 LLM_MODEL
LLM_VISION_MODEL = os.environ.get("LLM_VISION_MODEL", LLM_MODEL)
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.4"))
# 大模型单次输出上限（token）。不设时网关默认值很低，长简历会被物理截断，
# 表现为「自动截断 / 内容越跑越短」。给足上限避免输出被切断。
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "8192"))
# 大模型接口读超时（秒）。首字延迟过久会触发 Read timed out，可按模型/网络调大。
# 智能迭代优化第 4 轮「细节优化」需生成完整简历（max_tokens 8192），api.agicto.cn
# 长响应可能超过 240s，超时调至 600s 给足时间，避免长输出被 requests 中途切断。
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "600"))

PORT = int(os.environ.get("PORT", "5090"))

# 向量检索（面试题长期记忆语义召回）：默认使用本地 Ollama 的嵌入模型，无需联网。
# 可选 embedding 模型（需先 `ollama pull`）：
#   bge-m3            —— 多语言/中文检索更强（1024 维，默认）
#   nomic-embed-text  —— 英文/通用场景（768 维）
# 切换模型后，旧记忆中向量维度不一致会在服务启动时自动按新维度重建。
# 如需改用远程 OpenAI 兼容服务，把 EMBEDDING_PROVIDER 设为 openai 并覆盖下面三项即可。
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "ollama")  # ollama | openai
EMBEDDING_API_BASE = os.environ.get("EMBEDDING_API_BASE", "http://localhost:11434")
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "ollama")  # 本地 Ollama 不校验，占位即可
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
# 答疑时从长期记忆召回的条数上限
VECTOR_TOP_K = int(os.environ.get("VECTOR_TOP_K", "5"))

# ---------- Qdrant 向量数据库（长期记忆的语义向量存储与检索） ----------
# 长期记忆正文仍在 MySQL（关键词检索 / 用户隔离 / 管理接口不变），
# 语义向量与检索全部走 Qdrant：保存时 upsert 向量点，答疑时原生 ANN 召回 top-K。
# 本地 Qdrant 用默认 http://localhost:6333 即可；远程可覆盖 QDRANT_URL。
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")  # 本地 Qdrant 默认无鉴权，留空
# 向量集合名：bge-m3 是 1024 维。若切换嵌入模型导致维度变化，改集合名即可
# （旧集合数据自动保留，应用启动时对新集合重建向量）。
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "cvpolishing_memories")

# 联网检索：单次检索抓取与喂给模型的资料上限（避免读取过多、prompt 过大致超时）
SEARCH_FETCH_LIMIT = int(os.environ.get("SEARCH_FETCH_LIMIT", "5"))      # 每次最多抓取的网页数
SEARCH_PER_LIMIT = int(os.environ.get("SEARCH_PER_LIMIT", "2500"))       # 喂给模型的单条来源上限（字符）
SEARCH_TOTAL_LIMIT = int(os.environ.get("SEARCH_TOTAL_LIMIT", "14000"))  # 喂给模型的总量上限（字符）
# 联网检索开关：设为 false 可跳过前置联网检索（离线只做 JD 匹配优化）
SEARCH_ENABLED = os.environ.get("SEARCH_ENABLED", "true").lower() != "false"

# ---------- MySQL 配置（多用户登录 / 长期记忆 / 简历隔离） ----------
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
# 本机 MySQL 5.7 实例监听 3308（见 D:/ruanjian/MYSQL/my.ini），3306 已被 WSL 端口转发占用
DB_PORT = int(os.environ.get("DB_PORT", "3308"))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "root")
DB_NAME = os.environ.get("DB_NAME", "cvpolishing")
# Flask 会话签名密钥（生产环境请改为随机强密钥，可通过 SECRET_KEY 环境变量覆盖）
SECRET_KEY = os.environ.get("SECRET_KEY", "cvpolishing-dev-secret-key-change-me")

# 技能 registry（联网查询 / 一键导入的来源）。默认指向本应用内置的示例 registry，
# 可直接演示「联网查询 + 一键导入」完整链路。生产环境请改为真实的远程 registry 地址
# （需返回 {"skills":[...]}，单条技能字段见 app.py normalize_skill）。
SKILL_REGISTRY_URL = os.environ.get(
    "SKILL_REGISTRY_URL",
    "http://127.0.0.1:5090/api/skills/registry",
)

# ---------- VIP / 会员系统 ----------
# 会员激活码：用户在「会员中心」输入此码即可开通会员（默认永久，可用 VIP_DURATION_DAYS 改为限时）。
# 生产环境通过环境变量 VIP_ACTIVATION_CODE 覆盖；留空则关闭「激活码开通」入口。
VIP_ACTIVATION_CODE = os.environ.get("VIP_ACTIVATION_CODE", "VIP2026")
# 激活码开通后的有效期天数（None/空 = 永久会员）。
VIP_DURATION_DAYS = int(os.environ.get("VIP_DURATION_DAYS", "0")) or None
# 会员专属权益说明（前端会员中心展示）。
VIP_BENEFITS = [
    "智能迭代优化：自动多轮评分→诊断→精修，直到达标分（普通用户单次优化）",
    "面试问答无限生成：海量面试题 + 自动入库长期记忆",
    "优先使用联网检索与多模态 JD 识别",
    "会员专属加速通道与更高并发额度",
]

# ---------- 管理员权限 ----------
# 管理员账号集合：拥有全部权限（含提示词/问答系统设置等）。
# 生产环境可用环境变量 ADMIN_USERS 覆盖（逗号分隔）；留空则仅内置 admin / liwenhao。
ADMIN_USERS = set(
    (os.environ.get("ADMIN_USERS") or "admin,liwenhao").split(",")
)
# 非会员用户面试生成题目数量上限（会员不限）。
INTERVIEW_FREE_LIMIT = 10
# 非会员用户「基础简历优化」免费次数上限（会员不限）。
OPTIMIZE_FREE_LIMIT = 5
# 非会员用户「JD 生成工作经历」免费次数上限（会员不限）。
JD_RESUME_FREE_LIMIT = 3

# ---------- 支付通道（一键切换） ----------
# PAY_PROVIDER 取值：alipay=支付宝手机网站支付直连；aggregate=第三方聚合收款（虎皮椒/易支付等）。
# 切换只需改这一个变量，无需改动代码。
PAY_PROVIDER = (os.environ.get("PAY_PROVIDER") or "alipay").strip().lower()
if PAY_PROVIDER not in ("alipay", "aggregate"):
    raise RuntimeError("PAY_PROVIDER 非法，仅支持 alipay / aggregate，当前值：{}".format(PAY_PROVIDER))

# 会员套餐：月/半年/年三档，下单与回调均按 product_key 映射开通时长。
VIP_PRODUCTS = {
    "vip_month": {"name": "会员月卡", "price": 9.9, "vip_days": 30},
    "vip_half": {"name": "会员半年卡", "price": 49.0, "vip_days": 182},
    "vip_year": {"name": "会员年卡", "price": 99.0, "vip_days": 365},
}

# ---------- 支付宝手机网站支付（alipay.trade.wap.pay 直连） ----------
# 在支付宝开放平台「手机网站支付」应用下获取。以下均可用环境变量覆盖。
ALIPAY_APP_ID = os.environ.get("ALIPAY_APP_ID", "")
# 应用私钥（PKCS1/PKCS8 均可，sdk 内部自动适配），换行用 \n 或读取文件。
ALIPAY_APP_PRIVATE_KEY = os.environ.get("ALIPAY_APP_PRIVATE_KEY", "")
# 支付宝公钥（注意：不是应用公钥），用于回调验签。
ALIPAY_PUBLIC_KEY = os.environ.get("ALIPAY_PUBLIC_KEY", "")
# 支付宝网关：生产环境 https://openapi.alipay.com/gateway.do
ALIPAY_GATEWAY = os.environ.get("ALIPAY_GATEWAY", "https://openapi.alipay.com/gateway.do")
# 支付异步回调公网地址（需公网可达，可带 https）。留空则不下发回调（仅演示）。
ALIPAY_NOTIFY_URL = os.environ.get("ALIPAY_NOTIFY_URL", "")
# 支付完成后跳转的页面（前端会员中心地址，可带 scheme）。
ALIPAY_RETURN_URL = os.environ.get("ALIPAY_RETURN_URL", "")
# 订单有效期（分钟）：超时未付自动关闭。
ALIPAY_ORDER_TIMEOUT_MINUTES = int(os.environ.get("ALIPAY_ORDER_TIMEOUT_MINUTES", "30"))

# ---------- 第三方聚合收款（虎皮椒 / 易支付等，路径 B） ----------
# 聚合平台分配的商户号与通信密钥、下单 API 地址；回调同样走 /api/pay/notify。
AGGREGATE_MERCHANT_ID = os.environ.get("AGGREGATE_MERCHANT_ID", "")
AGGREGATE_KEY = os.environ.get("AGGREGATE_KEY", "")
# 聚合下单接口地址（如 https://pay.example.com/api/order）
AGGREGATE_API_URL = os.environ.get("AGGREGATE_API_URL", "")
# 聚合同步跳转地址（支付完成后返回会员中心）。
AGGREGATE_RETURN_URL = os.environ.get("AGGREGATE_RETURN_URL", "")
# 聚合异步回调地址（公网可达，平台主动通知付款结果）。
AGGREGATE_NOTIFY_URL = os.environ.get("AGGREGATE_NOTIFY_URL", "")
# 聚合平台标识字段：下单/回调中代表“付款成功”的状态值（默认 "success"，可覆盖）。
AGGREGATE_SUCCESS_STATUS = os.environ.get("AGGREGATE_SUCCESS_STATUS", "success")
# 回调签名参数名（不同聚合平台可能叫 sign / signature）。
AGGREGATE_SIGN_FIELD = os.environ.get("AGGREGATE_SIGN_FIELD", "sign")
# 订单有效期（分钟）：超时未付自动关闭。
AGGREGATE_ORDER_TIMEOUT_MINUTES = int(os.environ.get("AGGREGATE_ORDER_TIMEOUT_MINUTES", "30"))
