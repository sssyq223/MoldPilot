# cyqlc 邮件监听与解析迁移到 MoldPilot 的 Tool/Skill 技术方案

版本：0.1  
日期：2026-09-28  
状态：设计稿与首批实现已落地（未配置真实邮箱）
源项目：D:\mold-agent\cyqlc  
目标项目：D:\mold-agent\MoldPilot

## 1. 结论与迁移边界

cyqlc 的邮件能力不能按“复制几个 FastAPI 接口”的方式搬到 MoldPilot。它包含四个层次：IMAP 连接和增量扫描、MIME/正文表格解析、业务关键词分类、解析结果留痕与人工确认。迁移时应拆成纯解析内核、后台邮箱采集器、MoldPilot Tool 适配层和 Skill 编排层。

目标形态：

~~~mermaid
flowchart LR
    A[管理员配置邮箱] --> B[mail_worker 后台采集]
    B --> C[IMAP SSL/STARTTLS]
    C --> D[UID/UIDVALIDITY 增量扫描]
    D --> E[邮件解析与限额校验]
    E --> F[发件人及关键词分类]
    F --> G[MailMessage/MailDocument 留痕]
    G --> H[FileObject 私有归档]
    H --> I[Agent Tool 查询/准备]
    I --> J[Skill 展示证据]
    J --> K[人工确认后进入 MoldPilot BPM 或领域 Tool]
~~~

必须保留：

- 仅使用 IMAP 读取，支持 SSL 和 STARTTLS，不允许明文登录。
- 支持 .xlsx、.csv、.tsv 附件；无附件时解析 Markdown/HTML 正文表格。
- 发件方白名单、主题/附件名/正文分类优先级、四类业务标签和重复摘要逻辑保持兼容。
- 单封邮件、单轮读取、正文表格、附件数量、归档总量等资源限制必须继续存在。
- 自动采集只产生待人工复核的资料或 Proposal，不自动确认、不自动审批、不自动改写 MoldPilot 正式业务表。

明确不迁移：

- 不复制 cyqlc 的 SQLite/PostgreSQL 表、FastAPI 路由和前端实现代码；在 MoldPilot 设置页重建同等配置能力，并复用 MoldPilot 的鉴权、审计和后台 worker。
- 不把 cyqlc 的 WEEKLY_KIT、NEW_PRODUCT、EXPORT_DAILY、PLAN_13W 直接解释成 MoldPilot 的项目、合同、采购或 ERP 事实。
- 不复用 cyqlc 的 imports.preview_upload 直接写入 MoldPilot 领域表。
- 不把邮件监听作为模型每次调用时临时启动的线程；轮询由独立后台 worker 执行。

## 2. 源项目能力盘点

| 源文件 | 现有职责 | 迁移处理 |
|---|---|---|
| cyqlc/source/backend/app/services/mail_monitor.py | IMAP 连接、游标、重试、白名单、分类、导入调用、处理台账 | 拆为 mail_monitor_service.py、mail_parser.py、mail_tools.py；不要整文件复制 |
| cyqlc/source/backend/app/services/mail_documents.py | 附件抽取、Markdown/HTML 表格转 CSV、解析限额 | 优先迁移为无数据库依赖的纯 Python 包 |
| cyqlc/source/backend/app/services/imports.py | 文件摘要、归档、Excel/CSV 解析、待确认导入批次 | 只复用解析规则和测试思路；归档改接 MoldPilot FileObject/object_storage |
| cyqlc/source/backend/app/schema.sql 中 mail_monitor_messages | 邮件处理元数据唯一键和历史查询 | 改为 MoldPilot Alembic domain migration |
| cyqlc/source/backend/app/services/configuration.py | .env 配置和秘密字段处理 | 改为 MoldPilot 配置模型/受控设置，不让 Tool 接收密码 |
| cyqlc/source/tests/test_mail_monitor.py | UID、重试、隔离、游标和连接测试 | 搬成 MoldPilot 单元测试，IMAP 使用 fake |
| cyqlc/source/tests/test_mail_samples.py | EML 和工作簿样例回归 | 保留为迁移验收 fixtures，不作为生产归档 |

关键源入口是 mail_monitor._poll_once_unlocked()、_process_message()、_email_documents() 和 mail_documents.extract_structured_documents()。迁移时保留输入输出语义，但去掉 cyqlc Principal、SQLite connection 和业务导入调用。

## 3. MoldPilot 现有扩展点

MoldPilot 已具备目标所需的封装机制：

- 工具注册、权限、JSON Schema 和执行分派集中在 backend/domain_packs/mold/tool_gateway.py。
- Skill 文件使用 backend/domain_packs/mold/skills/<layer>/<domain>/<key>/SKILL.md，当前本地 Skill 位于 skills/local/...。
- skill_paths() 要求每个 Skill 使用四级路径，并通过 layer/domain 生成路由词。
- backend/agent_core/tool_gateway.py 是通用门面；backend/app/mcp_api.py 通过 /internal/runs/{run_id}/mcp 暴露当前任务已授权的工具。
- 文件应进入 FileObject 和 object_storage，通过 RunFile 绑定到当前 Agent Run；不能把附件复制到随意目录后把路径暴露给模型。
- 数据库变更使用 MoldPilot 的 Alembic domain migration，不修改旧 ERP 数据库。
- 写入能力通常先生成 Proposal，再由登录本人确认；Tool 说明必须区分“准备建议”“已确认”“领域系统已执行”。

第一阶段不需要单独再做外部 MCP Server。它应成为 MoldPilot domain pack 内的本地 Tool，现有 run-bound MCP 会自动将其提供给 Agent worker。只有未来需要独立部署邮箱服务时，才增加外部 MCP 适配器。

## 4. 建议的目标目录

~~~text
MoldPilot/backend/domain_packs/mold/
├─ mail/
│  ├─ parser.py                 # MIME、附件、正文表格和分类
│  ├─ monitor.py                 # SSL/STARTTLS、SEARCH、FETCH、游标与大小限制
│  ├─ ledger.py                  # SQLAlchemy 台账、幂等写入与租约
│  └─ parser.py                  # MIME、附件、正文表格和分类
├─ tools/local/
│  └─ mail_tools.py              # query_* 与 prepare_* Tool
├─ skills/local/mail/
│  └─ mail_monitoring/
│     └─ SKILL.md
└─ alembic_domain/versions/
   └─ na0d0e000037_mail_monitoring.py
~~~

应用层独立入口为 `backend/app/mail_worker.py`。它从 `secret_ref` 解析受控密码，缺少密码时只记录 `CONFIG_ERROR`，不会尝试连接邮箱。

## 6.1 企业邮箱配置入口与操作流程

管理员登录 MoldPilot 后，打开右上角账号菜单 → **设置** → **企业邮箱**。此页面对应 cyqlc 的“系统配置 / 网易企业邮箱监听”，但配置保存到 MoldPilot 的 `mail_monitor_account`，不会改写 cyqlc 的数据库。

页面提供以下配置：

- 账户名称、邮箱账号、IMAP 服务器、端口、SSL/TLS 或 STARTTLS、邮箱文件夹。
- 密钥引用（例如 `env://MOLDPILOT_MAIL_PASSWORD`）。数据库只保存引用名；邮箱客户端授权码由 API 和 `mail_worker` 启动环境注入。
- 允许发件人/域名、业务关键词、轮询间隔和回溯天数。

保存配置不会自动启动监听。管理员可按顺序执行“测试连接” → “启动监听”；需要重新读取历史窗口时使用“重新回溯”，需要查看台账时使用“查看处理记录”。启动后由独立 `mail_worker` 按轮询间隔采集，前端状态展示最近轮询时间、游标和错误信息。

对应的 MoldPilot 管理接口为：

| 操作 | 接口 |
|---|---|
| 读取配置 | `GET /api/mail-monitor/config` |
| 保存配置 | `PUT /api/mail-monitor/config` |
| 测试连接 | `POST /api/mail-monitor/config/{id}/test` |
| 启动/停止 | `POST /api/mail-monitor/config/{id}/start` / `stop` |
| 重新回溯 | `POST /api/mail-monitor/config/{id}/rescan` |
| 处理记录 | `GET /api/mail-monitor/config/{id}/messages` |

`mail.read` 允许查看配置状态和处理记录，`mail.manage` 允许保存配置及控制监听；所有配置和控制动作写入 MoldPilot 审计日志。

需要在 tool_gateway.py 的 skill_paths() 路由表增加：

~~~python
("local", "mail"): ["邮件", "邮箱", "IMAP", "监听", "邮件解析", "附件识别", "邮件处理记录"]
~~~

## 5. 后台采集器设计

### 5.1 进程边界

新增 backend/app/mail_worker.py，由 MoldPilot 的服务启动脚本单独运行。不要在 FastAPI startup 中复制 cyqlc 的 daemon thread；多进程部署会造成重复轮询和重复归档。

每个邮箱账号通过数据库租约或 PostgreSQL FOR UPDATE SKIP LOCKED 保证同一时刻只有一个 worker 处理。每轮流程：

1. 领取启用的 mailbox account，写入 lease 和 heartbeat。
2. 读取配置，建立 SSL/STARTTLS IMAP 连接并以 readonly 模式选择 mailbox。
3. 读取 UIDVALIDITY 和 UIDNEXT，检测邮箱重建或 UIDNEXT 回退。
4. 先处理上轮失败且未隔离的 UID，再执行日期 + UID 范围的有界 SEARCH。
5. 用 RFC822.SIZE 预检，再按单封 100MB、单轮 200MB 上限 FETCH。
6. 调用纯解析器，写入邮件元数据和文档摘要；附件写入 MoldPilot 私有对象存储。
7. 对每个文档生成 mail_document 记录和可供 Agent 查看/准备的待复核状态。
8. 提交事务后释放租约；失败时记录错误码和重试时间，不把异常全文写入日志。

轮询仍是定时轮询，不要求 IMAP IDLE。建议复用 MoldPilot 已有的 lease/worker 设计，但不要共享 message_worker.py 的通知消费逻辑。

### 5.2 采集与解析分离

imap_client.py 只负责字节流和 IMAP 协议，不理解业务类型；parser.py 只接收 RFC822 bytes 或已经解析的 Message，返回结构化文档；monitor_service.py 负责重试、幂等和数据库状态。这样离线 EML 测试不需要邮箱连接，解析器也可被 Tool 直接调用。

解析器输出至少包含：

~~~json
{
  "message": {
    "message_id": "<...>",
    "subject": "周齐套计划",
    "sender": "planner@example.com",
    "source_sent_at": "2026-08-25T02:00:00+00:00"
  },
  "documents": [
    {
      "name": "周齐套.csv",
      "source": "attachment",
      "media_type": "text/csv",
      "sha256": "...",
      "business_type": "WEEKLY_KIT",
      "classification_basis": "邮件主题命中关键词"
    }
  ],
  "limits": {"message_bytes": 12345, "document_count": 1}
}
~~~

正文表格只能作为无支持附件时的 fallback。真正附件的分类不应使用整封正文，以免引用历史转发内容中的“13 周预测”等词误判。

## 6. Tool 设计

工具分为只读查询、管理员配置和资料复核三组。工具注册在 backend/domain_packs/mold/tool_gateway.py，Schema 使用 Pydantic model_json_schema()，执行函数在 mail_tools.py 中查询台账或生成 Proposal。

| Tool | 类型 | 权限 | 作用 | 是否直接写业务事实 |
|---|---|---|---|---|
| query_mail_monitor_status | 查询 | mail.read | 读取账号、连接、游标、最近轮询和错误状态 | 否 |
| query_mail_processing_history | 查询 | mail.read | 按日期、主题、发件人、结果、业务标签分页查询台账 | 否 |
| query_mail_message_detail | 查询 | mail.read | 按真实记录 ID 从邮箱读取正文摘要和附件元数据 | 否 |
| query_mail_document | 查询 | mail.read | 查看已归档附件的摘要、来源、哈希和解析结果 | 否 |
| prepare_mail_monitor_config | 准备 | mail.manage | 校验并生成邮箱配置 Proposal；密码只接收 secret reference | 否，需确认 |
| prepare_mail_monitor_rescan | 准备 | mail.manage | 清除指定账号游标并生成重新回溯 Proposal | 否，需确认 |
| prepare_mail_review | 准备 | mail.review | 将指定邮件/文档绑定到当前 Run，形成分类复核和后续处理建议 | 否，需确认 |
| prepare_mail_domain_handoff | 规划保留 | 目标领域权限 | 后续按业务对象实现显式交接；当前版本不注册此工具 | 否，需确认 |

不建议暴露模型直接调用 start_mail_monitor、stop_mail_monitor 或 poll_now。监听开关由管理员设置，采集由后台 worker 执行；Tool 只查询状态或准备变更建议。若产品确实要求聊天控制启停，应增加 prepare_mail_monitor_enable/disable，并走现有 Proposal/确认链。

Tool 返回遵循 MoldPilot 证据格式：

~~~json
{
  "data": {},
  "source": "moldpilot_mail_monitor",
  "as_of": "2026-09-28T08:00:00+08:00",
  "evidence_id": "<tool step id>",
  "limitations": ["只读取配置邮箱", "未执行领域导入或审批"]
}
~~~

Tool 不得接受或返回邮箱密码、客户端授权码、IMAP 原始认证响应、任意外部 URL 或任意本机文件路径。错误只返回稳定错误码，例如 MAIL_NOT_CONFIGURED、MAIL_IMAP_CONNECT_FAILED、MAIL_MESSAGE_TOO_LARGE、MAIL_UIDVALIDITY_CHANGED、MAIL_RETRY_EXHAUSTED。

## 7. Skill 设计

第一阶段建立一个本地 Skill：

~~~text
backend/domain_packs/mold/skills/local/mail/mail_monitoring/SKILL.md
~~~

建议注册：

~~~python
SKILLS["mail_monitoring"] = {
    "name": "邮件监听与解析",
    "tools": ["query_mail_monitor_status", "query_mail_processing_history"],
    "optional_tools": [
        "query_mail_message_detail", "query_mail_document",
        "prepare_mail_monitor_config", "prepare_mail_monitor_rescan",
        "prepare_mail_review",
    ],
    "activation_tools": ["query_mail_monitor_status", "query_mail_processing_history"],
    "activation_queries": ["邮件监听", "邮箱监听", "邮件解析", "邮件处理记录", "IMAP", "邮件附件"],
    "auto_activation_queries": ["邮件监听状态", "邮件处理记录", "最近邮件解析情况"],
    "requires_tool_evidence": True,
}
~~~

SKILL.md 应描述编排规则，而不是复制解析实现：

1. 先查询状态或处理记录，确认账号、邮箱文件夹、最近检查时间和错误。
2. 查看邮件时使用真实处理记录 ID；不能用 UID、Message-ID 或用户描述自行拼接数据库查询。
3. 查看正文/附件时展示来源、主题、发件人、发送时间、附件名、哈希、分类依据和限制；正文/原件不存在或 UIDVALIDITY 已变化时明确说明。
4. 关键词分类只能作为待复核建议；不能把邮件分类直接当作 MoldPilot 项目事实。
5. 进入项目、合同、工程联络、采购或审批流程时，先要求用户选择明确的项目/领域对象，再调用对应领域 Skill 的 prepare_* Tool。
6. 所有写入或跨领域交接先展示目标、来源、版本、哈希和影响，等待本人明确确认。
7. 失败、权限变化、摘要变化、候选不唯一或版本冲突时停止当前链路，重新查询，不自动猜测。

Skill 边界必须写清：邮件收到不等于业务已确认，解析成功不等于导入成功，Proposal 创建不等于 BPM 审批完成，BPM 审批完成也不等于外部 ERP 执行成功。

## 8. MoldPilot 数据模型建议

新增 domain migration，至少包含以下表。

### mail_monitor_account

- id、name、host、port、username、mailbox、security
- enabled、poll_interval_seconds、lookback_days、max_messages
- allowed_senders_json、keywords_json、default_factory_code（若仍需兼容）
- secret_ref，不保存明文密码；config_version、created_at、updated_at
- 唯一约束：host + username + mailbox

### mail_monitor_cursor

- account_id、uidvalidity、uidnext、search_cursor
- last_search_since、last_poll_at、last_success_at、last_error
- lease_id、lease_until、heartbeat_at
- 唯一约束：account_id + uidvalidity

### mail_message

- account_id、uidvalidity、imap_uid、message_id
- subject、sender、source_sent_at
- outcome、attempt_count、error_code、error_message
- business_types_json、classification_json、attachment_names_json
- first_processed_at、last_processed_at
- 唯一约束：account_id + uidvalidity + imap_uid

### mail_document

- message_id、file_id、name、source（attachment/body_table）
- media_type、sha256、byte_size、parser_version
- business_type、classification_basis、parse_status、parse_error
- 同一邮件下以 name + sha256 去重。

### 文件归档

复用 MoldPilot FileObject/object_storage。由于 FileObject 当前要求 owner_id 和 conversation_id，建议创建不可登录的系统服务用户，并为每个 mailbox account 创建系统会话；邮件文件写入该系统会话后，通过 reference_run_file 绑定到用户当前 Run。file_policy.py 增加 mail_document 关联检查，防止拥有 mail.read 的用户读取不在授权账号范围内的附件。

邮件原文不建议作为可下载文件长期保存；第一阶段只归档支持解析的附件和正文表格 CSV。正文详情可按 UID 临时读取，并受 200,000 字符输出上限保护。

## 9. 与 MoldPilot 领域业务的衔接

四个 cyqlc 业务标签在 MoldPilot 中先作为 external_business_type 保存，不直接映射为项目、合同、采购或 ERP 表。后续若确定某类邮件对应 MoldPilot 业务，应新增显式 adapter：

~~~text
mail_document
  -> prepare_mail_domain_handoff
  -> 选择 project_id / domain_object_id
  -> 冻结附件版本、哈希、分类证据
  -> 提交对应 Agent BPM Proposal
  -> 人工确认
  -> 目标领域 Tool 执行
~~~

禁止以下隐式行为：

- 仅凭主题中的项目号自动绑定项目。
- 仅凭发件人自动选择责任部门或供应商。
- 仅凭 NEW_PRODUCT 或 PLAN_13W 自动创建计划。
- 仅凭解析出的数量自动下单、发货、审批或回写 ERP。

## 10. 安全与资源限制

| 类别 | 建议值 |
|---|---:|
| 单封原始邮件 | 100MB |
| 单轮读取总量 | 200MB |
| 正文解析字符数 | 12MB |
| 正文表格行数 | 100,000 |
| 正文表格列数 | 512 |
| 单元格字符数 | 256KB |
| 单行字符数 | 4MB |
| 邮件附件数 | 128 |
| 失败重试 | 3 次后隔离 |
| 处理详情正文返回 | 200,000 字符 |

迁移时还应补上源实现暴露出的两个要求：

- 白名单不能只检查 From 头；如果网易企业邮箱能提供认证结果，应记录并优先校验 envelope/DKIM/SPF。
- 主监听路径必须真正执行附件数量限制，不能只在离线 mail_documents helper 中定义常量。

密码、授权码、IMAP 原始响应和完整邮件正文不能进入 Agent prompt、审计事件或普通日志。日志只保留账号标识、UID、稳定错误码和摘要。

## 11. 分阶段实施计划

### 阶段 A：解析内核迁移（已完成）

- 从 cyqlc 拆出 parser.py，去除数据库、Principal、FastAPI 依赖。
- 搬迁 EML/工作簿样例和分类/限额测试。
- 验证四类业务分类、Markdown/HTML 表格、重复附件、超限和损坏输入。

### 阶段 B：MoldPilot 归档与台账（已完成首批实现）

- 增加 Alembic domain migration 和 SQLAlchemy 模型。
- 文件原件当前归档到受控 archive_root，并在 `mail_message.detail_json` 留存摘要路径；接入 FileObject/RunFile 是下一步生产化工作。
- `monitor.py`、`ledger.py` 已实现租约、UID 游标、大小限制、哈希归档和失败记账。
- 已新增独立 `mail_worker.py`，默认不打开真实邮箱。

### 阶段 C：Tool/Skill 暴露（已完成首批实现）

- 在 tool_gateway.py 注册工具、权限、Schema 和执行分派。
- 创建 skills/local/mail/mail_monitoring/SKILL.md。
- 通过 run-bound MCP tools/list 和 tools/call 验证工具可见性、参数校验和权限过滤。
- 已开放查询、配置、重扫和人工复核；配置与动作均走 Proposal，跨领域交接仍待领域适配器。

### 阶段 D：领域交接

- 为每一种真正有需求的邮件类型单独定义 domain adapter。
- 绑定明确 project_id、对象版本、附件版本和来源证据。
- 接入现有 MoldPilot BPM，不创建第二套审批引擎。

### 阶段 E：灰度切换

- cyqlc 保持只读或关闭自动导入，MoldPilot 使用独立测试邮箱/文件夹。
- 并行比较 UID、分类、附件哈希、失败状态和台账结果。
- 通过连续多个轮次差异核对后，再切换生产邮箱监听。
- 保留回滚开关：停止 MoldPilot worker，不删除邮件台账和对象存储文件。

## 12. 验收标准

1. MoldPilot 在没有真实邮箱凭据时可完成 fake IMAP、EML 和工作簿测试。
2. tools/list 只返回当前用户被授权的邮件工具；未授权用户调用得到稳定 TOOL_FORBIDDEN。
3. Tool Schema 拒绝密码、任意 URL、任意路径和超大参数。
4. 相同 account + UIDVALIDITY + UID 不会重复处理；相同附件哈希不会重复归档。
5. UIDVALIDITY 变化、UIDNEXT 回退、SEARCH 超限、FETCH 超限和三次失败隔离都有可查询记录。
6. 列表查询不返回正文；详情读取受账号范围、UIDVALIDITY、Message-ID 和正文长度限制。
7. 邮件解析只产生 mail_message/mail_document 和待复核 Proposal，不直接改变 MoldPilot 正式业务对象。
8. 人工确认前后分别有 Tool evidence、Proposal、确认回执和领域执行回执；不能把其中任何一步提前宣称为业务完成。
9. 真实测试通过后，才允许在生产配置中启用 mail_worker；启用前完成 secret、对象存储、数据库备份和日志脱敏检查。

## 13. 需要在开发前确认的产品决策

- MoldPilot 首期是否只需要“邮件资料接收与解析”，还是已经确定要把四类 cyqlc 计划邮件映射到某个 MoldPilot 业务对象。
- 邮件附件是否允许长期归档，还是仅保存摘要并在需要时从 IMAP 重新读取。
- 是否需要多个邮箱账号/文件夹，还是先支持一个 mailbox account。
- 邮箱密码由本机环境变量、密钥服务还是现有配置管理能力托管。
- 监听产生的待复核 Proposal 由谁审批，是否沿用现有通用 BPM。
- 是否要求真实网易邮箱联调；若要求，应先准备独立测试文件夹，不能直接用生产 INBOX 做迁移验收。

## 参考实现位置

- cyqlc 监听：[mail_monitor.py](D:/mold-agent/cyqlc/source/backend/app/services/mail_monitor.py)
- cyqlc 正文解析：[mail_documents.py](D:/mold-agent/cyqlc/source/backend/app/services/mail_documents.py)
- cyqlc 邮件监听测试：[test_mail_monitor.py](D:/mold-agent/cyqlc/source/tests/test_mail_monitor.py)
- MoldPilot 工具注册：[tool_gateway.py](D:/mold-agent/MoldPilot/backend/domain_packs/mold/tool_gateway.py)
- MoldPilot 本地 Skill 示例：[document_engineering_contact_intake/SKILL.md](D:/mold-agent/MoldPilot/backend/domain_packs/mold/skills/local/document/document_engineering_contact_intake/SKILL.md)
- MoldPilot run-bound MCP：[mcp_api.py](D:/mold-agent/MoldPilot/backend/app/mcp_api.py)
- MoldPilot 私有文件模型：[file_models.py](D:/mold-agent/MoldPilot/backend/agent_core/file_models.py)
