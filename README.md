# CVPolishing

AI 简历精修 · 智能面试 · 会员系统。多 Agent 协作的简历优化平台：联网检索 → 优化 → 评分 → 思考诊断 → 细节精修 → 监督终裁，并支持面试问答生成与知识沉淀。

## 功能概览
- 简历基础优化 / 智能迭代优化（会员专属，自动多轮达标）
- 5 维度评分、与竞品对比、JD 图片识别
- 面试问答生成、TXT 导入、长期记忆（MySQL 语义召回）
- 技能中心：使用说明 + 联网查询 + 一键导入
- 会员系统：激活码开通 / 后台管理 / VIP 权益门禁
- 链路追踪（LangSmith 可选）

## 快速部署（Docker 一键）

依赖：已安装 Docker Desktop。

```bash
# Windows
deploy.bat
# 或 Linux / macOS
bash deploy.sh
```

脚本会复制 `.env.example` 为 `.env`（如不存在），构建并启动「应用 + MySQL」。完成后访问 http://localhost:5090 ，默认管理员 `admin / admin123!`。

手动方式：

```bash
docker compose up -d --build
```

## 本地运行（非 Docker）

1. 安装 MySQL 并建库 `cvpolishing`（utf8mb4）；
2. 复制 `.env.example` 为 `.env`，填入 `LLM_API_KEY`、数据库等；
3. `pip install -r requirements.txt`
4. `python init_db.py`（建表 + 种子账号）
5. `python app.py`（访问 http://localhost:5090）

## 配置说明（.env）
- `LLM_API_KEY` / `LLM_API_BASE` / `LLM_MODEL`：大模型问答引擎（OpenAI 兼容）。
- `DB_*`：MySQL 连接。
- `EMBEDDING_*`：向量召回（ollama / openai）。
- `VIP_ACTIVATION_CODE`：全局兜底激活码（万能码），用户在「会员中心」输入即可开通；留空则关闭该码。
- `VIP_DURATION_DAYS`：全局兜底码开通后的有效期天数，0/留空为永久。
- `ADMIN_USERS`：管理员账号集合（逗号分隔，默认 `admin,liwenhao`），拥有全部权限（含提示词/问答系统设置、邀请码管理）。
- `SESSION_LIFETIME_DAYS`：登录会话有效期（天，默认 7）；会话数据存 MySQL，多实例共享登录态。
- 支付通道一键切换：`PAY_PROVIDER`（`alipay`=支付宝手机网站支付直连 / `aggregate`=第三方聚合收款）。改这一个变量即可切换，**无需改代码**。
- 会员套餐三档（`VIP_PRODUCTS`）：月卡 ¥9.9/30天、半年卡 ¥49/182天、年卡 ¥99/365天。
- 支付宝直连：`ALIPAY_APP_ID`、`ALIPAY_APP_PRIVATE_KEY`、`ALIPAY_PUBLIC_KEY`、`ALIPAY_GATEWAY`、`ALIPAY_NOTIFY_URL`（公网可达的异步回调地址）、`ALIPAY_RETURN_URL`、`ALIPAY_ORDER_TIMEOUT_MINUTES`（订单超时分钟，默认 30）。
- 第三方聚合收款（虎皮椒/易支付等）：`AGGREGATE_MERCHANT_ID`、`AGGREGATE_KEY`、`AGGREGATE_API_URL`、`AGGREGATE_NOTIFY_URL`、`AGGREGATE_RETURN_URL`、`AGGREGATE_SUCCESS_STATUS`（成功状态值，默认 `success`）、`AGGREGATE_SIGN_FIELD`（回调签名字段名，默认 `sign`）、`AGGREGATE_ORDER_TIMEOUT_MINUTES`。
- 可选 LangSmith 链路追踪（不配置则仅本地 JSONL 落盘，前端可视化不受影响）：`LANGSMITH_API_KEY`、`LANGSMITH_API_URL`（默认 `https://api.smith.langchain.com`）、`LANGSMITH_PROJECT`（默认 `cvpolish`）。配置后在 tracer.py 中每个链路步骤会同步上报到对应项目，前端链路 ID 可在 LangSmith 按 ID 检索。

## 会员管理
- 用户：登录后访问 `/vip`，可「输入激活码开通」或「在线购买」（支付宝 wap 支付，付款后自动开通）。
- 管理员：访问 `/admin` 可「设为会员 / 取消会员」，并在「专属邀请码」区生成分发给用户的个性化邀请码（可绑定账号、设会员天数与有效期，用过即失效）。
- 接口：`/api/vip/activate`、`/api/vip/info`、`/api/admin/users/<username>/vip`、`/api/admin/invite`(GET/POST/DELETE)。
- 支付接口：`/api/pay/products`（套餐列表）、`/api/pay/create`（创建订单并返回支付宝支付 URL）、`/api/pay/notify`（支付宝异步回调，会员开通的唯一权威入口，含验签与幂等）、`/api/pay/order/<out_trade_no>`（前端轮询刷新 UI）。
- 当前会员权益：智能迭代优化闭环（普通用户使用单次基础优化）、面试问答无限生成（普通用户限 10 道）。

## 支付对接要点
- 收款闭环（`PAY_PROVIDER` 决定走哪条）：用户点购买 → `/api/pay/create` 按 provider 调对应下单接口生成支付 URL → 前端跳转/打开 → 平台异步回调 `/api/pay/notify`（按 provider 验签 → 标记订单已付 → 按套餐 `vip_days` 调 `db.set_vip` 开通）→ 前端轮询 `/api/pay/order` 刷新状态。
- 一键切换：`PAY_PROVIDER=alipay` 走支付宝直连（`alipay.trade.wap.pay`）；`PAY_PROVIDER=aggregate` 走第三方聚合（MD5 签名下单 + 回调验签）。两条链路共用 `orders` 表与会员开通逻辑，互不影响。
- 支付宝直连：`ALIPAY_NOTIFY_URL` 须为公网可达 HTTPS；本地部署需内网穿透或上公网服务器。依赖 `alipay-sdk-python`（`pip install alipay-sdk-python`），未装或配置缺失时明确报错，不静默降级。
- 聚合收款：回调签名用 `AGGREGATE_KEY` 做 MD5 校验，订单按 `out_trade_no` 幂等，重复回调不重复开通会员。
- 会员状态只由后端回调决定，前端轮询仅刷新 UI，无法伪造。

## 多实例与会话
- 登录会话集中存于 MySQL `sessions` 表，浏览器 cookie 仅持签名 session id；部署多个副本（或经反向代理多节点）只要连同一 MySQL，即实现一次登录处处可用（单点登录）。

## 目录
- `app.py`：Flask 主程序与全部 API（含会员接口）
- `db.py`：MySQL 访问（用户 / 记忆 / 评分 / VIP）
- `config.py`：配置与提示词（运行时读取 `data/prompts.json` 覆盖）
- `static/`：前端（user.js / vip.js / admin.js / app.js / style.css）
