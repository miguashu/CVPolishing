# CVPolishing

AI 简历精修 · 智能面试 · 会员系统。多 Agent 协作的简历优化平台：联网检索 → 优化 → 评分 → 思考诊断 → 细节精修 → 监督终裁，并支持面试问答生成与知识沉淀。

- 技术栈：Python Flask + MySQL + Ollama 向量召回（可选 OpenAI Embedding）+ SSE 流式
- 默认端口：5090
- 支持 Docker 一键部署与本地运行

## 一、功能总览

| 模块 | 功能 | 入口 |
|------|------|------|
| 简历优化 | 基础优化（联网检索）、智能迭代优化闭环（多轮达标）、精简版/进阶强化 | 首页 `/` |
| 智能评分 | 5 维度流式评分，量化可视化 | 首页 `/` |
| 简历对比 | 与竞品/目标岗位对比（并发执行） | 首页 `/` |
| JD 识别 | 上传 JD 图片 OCR 识别文字，自动填入 | 首页 `/` |
| JD 生成经历 | 根据目标岗位 JD 反推生成匹配的工作经历 | `/jd-resume` |
| 面试问答 | 面试题生成、单题追问、自由提问、AI 生成答案 | `/interview` |
| 简历文件上传 | 上传 PDF / Word / TXT / Markdown 简历，自动解析填入 | `/interview` |
| 按需修改简历 | 输入修改需求，AI 针对性修改并回填简历框 | `/interview` |
| TXT 导入 | TXT 文件解析为问答对，沉淀入长期记忆 | `/interview` |
| 长期记忆 | MySQL 语义召回 + 手动/批量维护，问答引用上下文 | `/interview` |
| 链路追踪 | 每次运行写本地 JSONL + 可选上报 LangSmith | `/flow` |
| 提示词配置 | 全部 Agent 提示词在线查看/编辑（运行时生效） | `/prompts` |
| 技能中心 | 使用说明 + 联网查询 + 一键导入 | `/prompts` |
| 会员系统 | 激活码开通 / 在线支付 / 后台管理 / VIP 权益门禁 | `/vip` `/admin` |

## 二、技术架构

```
浏览器 (index.html / interview.html / flow.html / jd_resume.html / prompts.html / vip.html / admin.html)
   │  fetch / SSE
   ▼
app.py ── Flask 路由 + 会话（MySQL sessions 表，多实例单点登录）
   │
   ├── optimizer.py     基础优化 SSE（联网检索 → optimize_system → 优化后简历/说明）
   ├── auto_optimizer   智能迭代优化闭环（optimize → score → think → refine → supervise）
   ├── score.py         5 维度评分
   ├── compare.py       简历对比
   ├── interview.py     面试题生成 / 问我 / 答案生成 / TXT 解析
   ├── jd_resume.py     JD 生成经历
   ├── resume_file.py   简历文件解析（PDF/docx/txt/md）
   ├── resume_apply.py  按需修改简历 SSE
   ├── prompt_config.py DEFAULT_PROMPTS（单一可信源）+ PROMPT_META
   ├── prompt.py        提示词拼装 / 结果解析（优化、评分、问答等）
   ├── llm_service.py   OpenAI 兼容流式调用（llm_stream / llm_complete）
   ├── memory.py        长期记忆语义召回
   ├── embed.py         Embedding（本地 Ollama 优先）
   ├── qdrant_store.py  Qdrant 向量库
   ├── tracer.py        链路追踪（本地 JSONL + LangSmith）
   ├── db.py            MySQL 访问（用户/记忆/评分/VIP/订单/会话）
   └── config.py        环境变量配置
```

## 三、多 Agent 提示词体系（prompt_config.py）

所有 Agent 提示词集中在 `DEFAULT_PROMPTS`（单一可信源），运行时由 `load_prompts()` 读取，`data/prompts.json` 可覆盖（无需重启），`PROMPT_META` 控制前端配置页卡片。

| Key | 用途 |
|-----|------|
| `optimize_system` | 基础优化 Agent（联网检索 → 优化简历） |
| `compare_system` | 简历对比 Agent |
| `score_system` | 5 维度评分 Agent |
| `interview_system` | 面试题生成 Agent |
| `interview_plan_system` | 面试规划 Agent |
| `answer_system` | 单题追问/自由提问 Agent |
| `refine_system` | 答案提炼 Agent |
| `genanswer_system` | AI 生成答案 Agent |
| `parse_system` | TXT 文件解析导入 Agent |
| `think_system` | 思考诊断 Agent（分析评分薄弱点 + 输出优化指引 JSON） |
| `resume_refine_system` | 细节优化 Agent（按评分 + 思考指引精修，保护关键内容） |
| `supervise_system` | 监督/结果 Agent（判定达标 + 安全检查 + 防死循环） |
| `resume_apply_system` | 按需修改简历 Agent（按用户需求修改，保护关键内容） |

## 四、核心流程

### 1. 基础优化 `/api/optimize`（SSE）
联网检索 → `optimize_system` → 输出 `===优化后简历===` / `===优化说明===`。

### 2. 智能迭代优化闭环 `/api/optimize/auto`（SSE）
初始优化 → `score_once` 评分 → `think_system` 诊断 → `resume_refine_system` 精修 → 再评分 → `supervise_system` 终裁（达标 ≥ target_score 默认 90 / 安全未通过 / 达 max_iter / 连续两轮无提升则停）→ 循环。前端「智能迭代优化」按钮 + 目标分输入，切到「迭代过程」Tab 看量化可视化（SVG 分数曲线 + 每轮时间线卡片）。

### 3. 智能问答页简历上传 `/api/resume/parse`
前端选择 PDF / docx / txt / md 文件 → 后端解析为纯文本 → 自动填入简历框 → 同步写回 `/api/resume` 作为优化基准。

### 4. 按需修改简历 `/api/resume/apply-change`（SSE）
输入修改需求（如"突出项目管理能力"）→ `resume_apply_system` 流式修改 → 预览修改结果 → 一键回填简历框。

## 五、API 接口

### 认证与会话
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/register` | 注册 |
| POST | `/api/login` | 登录（写 MySQL session） |
| POST | `/api/logout` | 登出 |
| GET | `/api/me` | 当前用户信息 |
| GET | `/api/config` | 前端配置 |

### 简历
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/resume` | 获取当前用户简历（original / optimized / jd） |
| POST | `/api/resume` | 保存简历（original / optimized / jd，可 append_position） |
| POST | `/api/resume/parse` | 上传简历文件（PDF/docx/txt/md），返回纯文本 |
| POST | `/api/resume/apply-change` | SSE 按需修改简历 |
| POST | `/api/resume/export-pdf` | 导出简历为 PDF |

### 优化 / 评分 / 对比
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/optimize` | 基础优化（SSE，支持联网检索、版本） |
| POST | `/api/optimize/auto` | 智能迭代优化闭环（SSE） |
| POST | `/api/score` | 5 维度评分（SSE） |
| POST | `/api/compare` | 简历对比（SSE） |
| POST | `/api/jd/ocr` | JD 图片 OCR 识别（base64） |
| POST | `/api/jd/resume` | JD 生成经历（SSE） |

### 面试 / 问答
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/interview/generate` | 生成面试题（去重 + 联网 + 入库） |
| POST | `/api/interview/ask` | 单题追问（自动提炼入库） |
| POST | `/api/generate-answer` | AI 生成答案 |
| POST | `/api/interview/parse-file` | TXT 文件解析导入长期记忆（SSE） |

### 长期记忆
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/memory` / `/api/memory/search` | 查询/语义召回 |
| POST | `/api/memory` | 新增问答 |
| DELETE | `/api/memory` | 删除 |
| POST | `/api/memory/clear` | 清空 |
| POST | `/api/memory/import` | 批量导入（JSON/MD） |

### 追踪 / 任务
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/trace/<run_id>` | 读取链路数据 |
| GET | `/api/task/list` | 任务列表 |
| GET/DELETE | `/api/task/<task_id>` | 任务详情/删除 |
| POST | `/api/task/cleanup` | 清理任务 |
| GET | `/api/question/timestamps` | 提问时间戳统计 |

### 会员 / 支付 / 管理
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/vip/activate` | 激活码开通会员 |
| GET | `/api/vip/info` | 会员信息 |
| GET | `/api/pay/products` | 套餐列表 |
| POST | `/api/pay/create` | 创建支付订单 |
| POST | `/api/pay/notify` | 支付异步回调（验签 + 幂等开通会员） |
| GET | `/api/pay/order/<out_trade_no>` | 轮询订单状态 |
| GET/POST | `/api/admin/users` | 用户列表/新增 |
| DELETE | `/api/admin/users/<username>` | 删除用户 |
| POST | `/api/admin/users/<username>/password` | 重置密码 |
| POST | `/api/admin/users/<username>/vip` | 设置/取消会员 |
| GET/POST/DELETE | `/api/admin/invite` | 邀请码管理 |

### 提示词 / 技能
| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/prompts` | 提示词查看/保存（全量写入 DEFAULT_PROMPTS 全部 key） |
| GET | `/api/skills` | 已安装技能 |
| GET | `/api/skills/<sid>` | 技能详情 |
| POST | `/api/skills/<sid>/install` / `/uninstall` | 安装/卸载 |
| GET | `/api/skills/registry` / `/online` | 技能市场 / 在线检索 |
| POST | `/api/skills/import` | 一键导入 |

## 六、前端页面

| 文件 | 说明 |
|------|------|
| `index.html` + `static/app.js` | 首页：简历输入 + 基础优化 / 智能迭代优化 / 评分 / 对比 / JD 识别 |
| `interview.html` + `static/interview.js` | 智能问答页：简历优化 / 简历文件上传 / 按需修改 / 生成面试题 / 问我 / TXT 导入 / 长期记忆 / 语音 |
| `flow.html` + `static/flow.js` | 链路追踪可视化 |
| `jd_resume.html` + `static/jd_resume.js` | JD 生成经历 |
| `prompts.html` + `static/prompts.js` | 提示词配置 + 技能中心 |
| `vip.html` + `static/vip.js` | 会员中心 |
| `admin.html` + `static/admin.js` | 后台管理 |
| `login.html` / `register.html` | 登录 / 注册 |

## 七、快速部署

### Docker 一键

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

### 本地运行（非 Docker）

1. 安装 MySQL 并建库 `cvpolishing`（utf8mb4）；
2. 复制 `.env.example` 为 `.env`，填入 `LLM_API_KEY`、数据库等；
3. `pip install -r requirements.txt`（PDF 解析需 `pip install pdfplumber`，已包含于 requirements.txt）
4. `python init_db.py`（建表 + 种子账号）
5. `python app.py`（访问 http://localhost:5090）

## 八、配置说明（.env）

- `LLM_API_KEY` / `LLM_API_BASE` / `LLM_MODEL`：大模型问答引擎（OpenAI 兼容）。
- `DB_*`：MySQL 连接。
- `EMBEDDING_*`：向量召回（ollama / openai）。
- `VIP_ACTIVATION_CODE`：全局兜底激活码（万能码），用户在「会员中心」输入即可开通；留空则关闭该码。
- `VIP_DURATION_DAYS`：全局兜底码开通后的有效期天数，0/留空为永久。
- `ADMIN_USERS`：管理员账号集合（逗号分隔，默认 `admin,liwenhao`），拥有全部权限（含提示词/问答系统设置、邀请码管理）。
- `SESSION_LIFETIME_DAYS`：登录会话有效期（天，默认 7）；会话数据存 MySQL，多实例共享登录态。
- `PAY_PROVIDER`：支付通道一键切换（`alipay`=支付宝手机网站支付直连 / `aggregate`=第三方聚合收款）。
- 会员套餐三档（`VIP_PRODUCTS`）：月卡 ¥9.9/30天、半年卡 ¥49/182天、年卡 ¥99/365天。
- 支付宝直连：`ALIPAY_APP_ID`、`ALIPAY_APP_PRIVATE_KEY`、`ALIPAY_PUBLIC_KEY`、`ALIPAY_GATEWAY`、`ALIPAY_NOTIFY_URL`、`ALIPAY_RETURN_URL`、`ALIPAY_ORDER_TIMEOUT_MINUTES`。
- 第三方聚合收款：`AGGREGATE_MERCHANT_ID`、`AGGREGATE_KEY`、`AGGREGATE_API_URL`、`AGGREGATE_NOTIFY_URL`、`AGGREGATE_RETURN_URL`、`AGGREGATE_SUCCESS_STATUS`、`AGGREGATE_SIGN_FIELD`、`AGGREGATE_ORDER_TIMEOUT_MINUTES`。
- 可选 LangSmith 链路追踪（不配置则仅本地 JSONL 落盘，前端可视化不受影响）：`LANGSMITH_API_KEY`、`LANGSMITH_API_URL`、`LANGSMITH_PROJECT`。

## 九、会员与支付

- 用户：登录后访问 `/vip`，可「输入激活码开通」或「在线购买」（支付宝 wap 支付，付款后自动开通）。
- 管理员：访问 `/admin` 可「设为会员 / 取消会员」，并生成分发给用户的个性化邀请码。
- 当前会员权益：智能迭代优化闭环（普通用户使用单次基础优化）、面试问答无限生成（普通用户限 10 道）。
- 收款闭环：用户点购买 → `/api/pay/create` 生成支付 URL → 支付 → 异步回调 `/api/pay/notify`（验签 → 幂等开通）→ 前端轮询刷新。
- 会员状态只由后端回调决定，前端轮询仅刷新 UI，无法伪造。

## 十、多实例与会话

登录会话集中存于 MySQL `sessions` 表，浏览器 cookie 仅持签名 session id；部署多个副本（或经反向代理多节点）只要连同一 MySQL，即实现一次登录处处可用（单点登录）。

## 十一、目录结构

```
CVPolishing/
├── app.py                  Flask 主程序与全部 API
├── agents.py               提示词获取 getter
├── optimizer.py             基础优化 / 智能迭代优化 SSE
├── score.py                5 维度评分
├── compare.py              简历对比
├── interview.py            面试题 / 问我 / 答案 / TXT 解析
├── jd_resume.py            JD 生成经历
├── resume_file.py          简历文件解析（PDF/docx/txt/md）
├── resume_apply.py         按需修改简历 SSE
├── prompt_config.py        DEFAULT_PROMPTS + PROMPT_META（单一可信源）
├── prompt.py               提示词拼装与结果解析
├── prompt_interview.py     面试相关提示词
├── llm_service.py          LLM 流式/完整调用
├── memory.py / embed.py / qdrant_store.py / search_service.py   记忆与召回
├── tracer.py               链路追踪（JSONL + LangSmith）
├── db.py                   MySQL 访问
├── config.py               环境变量配置
├── init_db.py              建表 + 种子账号
├── requirements.txt        依赖清单
├── docker-compose.yml / deploy.bat / deploy.sh   部署
├── static/                 前端（app.js / interview.js / flow.js / jd_resume.js / prompts.js / user.js / vip.js / admin.js / login.js / register.js / zoom.js / style.css）
└── data/                   运行时数据（prompts.json / traces / task）
```

## 十二、常见问题

- **PDF 上传解析失败**：确认已 `pip install pdfplumber`；扫描件（纯图片 PDF）无法提取文字，请使用文字版简历。
- **提示词改了不生效**：提示词在运行时读取（`data/prompts.json` 覆盖 `DEFAULT_PROMPTS`），前端「提示词配置」保存后即时生效，无需重启。
- **向量召回异常**：默认使用本地 Ollama Embedding，确保 Ollama 已启动且模型可用；也可切换 `EMBEDDING_*` 为 OpenAI。
