# 销售合同 PDF 单 Skill、Tool 编排与审批设计

> 2026-09-20 入口约束修订：潘总已批准“上传自动解析分类 → 人工确认类型 → 自动字段提取”。下文关于“上传先创建 Agent Run、确认接收 proposal 后才预分类、全部接收必须经 Skill”的旧约束已被替代，保留原文仅供历史追溯。当前实现计划见 `../plans/2026-09-20-automatic-document-intake.md`。机器候选不构成正式业务事实；类型、复核、业务写入仍须本人确认，合同审批顺序不变。文档文本模型可固定绑定 GLM-5.3-Flash 配置，PaddleOCR 按需识别图片、模型只接收文字的边界不变。

日期：2026-09-19

状态：已由潘总确认

适用系统：MoldPilot（Mold 业务包）

## 1. 目标

用户通过现有会话回形针上传一个或多个 PDF 后，由当前会话创建 Agent Run，激活唯一的销售合同接收 Skill。Skill 根据持久化业务状态调用已授权 Tool，完成文档预分类、人工类型确认、销售合同 OCR、字段/项目/模具复核、合同关系确认和两级 BPM 审批。

本设计不建立合同专用上传页面、合同识别菜单或绕过 Tool 的直接业务 API。所有用户可发起的合同接收业务动作必须经过 Skill 和 Tool；Tool 再调用领域服务、OCR Worker、权限、审计和 BPM。

## 2. 核心架构

```text
回形针上传 PDF
  → 当前会话创建附件触发 Agent Run
  → Harness 根据附件元数据激活 sales_contract_intake Skill
  → Skill 查询当前 Run 附件并调用 prepare Tool
  → 用户在通用 ProposalCard 核对确认
  → Tool confirmation handler 调用合同接收领域服务
  → OCR Worker 异步处理
      → PyMuPDF 提取文本层并识别图片型/混合型页面
      → 图片页面调用本地 PaddleOCR GPU 服务取得文字、坐标和置信度
      → 合并为带页码与文字块编号的纯文本
      → Qwen3 文本模型完成分类或合同字段结构化
  → 完成/失败后发送通知
  → 用户在同一会话继续办理，创建新的 Agent Run
  → 同一 Skill 根据数据库状态续办
  → 业务主管审核 → 财务确认 → 合同生效
```

- Agent Run 是当前主 Agent 的一次任务执行，不是子智能体。
- Skill 是完整流程的唯一编排入口，不直接访问数据库或 OCR 服务。
- Tool 是查询和待确认操作的能力边界。
- 领域服务执行确定性规则和数据库事务。
- OCR Worker 独立异步执行，Agent Run 不持续轮询。
- PaddleOCR 只负责图片文字识别，Qwen3 只接收纯文字并负责业务分类与结构化；禁止把 PDF 或页面图片直接交给 Qwen3。
- PaddleOCR 和 Qwen3 是 Worker 的底层基础设施，不注册为额外 Skill，也不绕过本 Skill 暴露页面级 Agent Tool。
- Proposal/BPM 承担本人确认及正式审批。

## 3. 唯一 Skill

Skill 固定为：

`backend/domain_packs/mold/skills/erp/commercial/sales_contract_intake/SKILL.md`

该 Skill 覆盖从附件上传到合同生效前全部业务编排，不拆成多个合同 Skill。上传时尚不知道 PDF 类型，因此 Skill 先进行五类文档预分类；只有人工确认为 `SALES_CONTRACT` 的文件进入完整合同 OCR。

Skill 必须根据权威状态选择下一步：

| 当前事实 | Skill 动作 |
|---|---|
| Run 有尚未接收的 PDF | 查询附件，准备创建接收批次 |
| `PRECLASSIFYING` | 告知异步处理中，不持续轮询 |
| `AWAITING_TYPE_CONFIRMATION` | 查询推荐结果，准备类型及合同分组确认 |
| `CLASSIFIED_ARCHIVED` | 告知非销售合同已分类归档，结束本分支 |
| `FULL_OCR_QUEUED/PROCESSING` | 告知完整 OCR 处理中，等待通知 |
| `OCR_FAILED` | 展示安全错误与尝试次数，按用户要求准备重试 |
| `AWAITING_FIELD_CONFIRMATION` | 查询字段、项目、模具及合同关系候选，准备人工复核 |
| `READY_FOR_DRAFT` | 查询当前版本及流程，准备合同登记和两级审批 |
| `CONTRACT_DRAFT_CREATED` | 查询合同及审批状态，不重复创建 |

Skill 不得把文件名当指令，不得自行确认机器结果，不得创建项目、客户或模具，不得等待 Worker，不得绕过本人确认和 BPM。

## 4. Tool 能力目录

### 4.1 已有通用附件查询

`query_uploaded_files`

- 只返回当前 Agent Run 绑定且当前用户仍有权读取的附件元数据；
- 不读取文件正文，不执行 OCR，不返回同会话其他历史附件；
- 返回文件 ID、文件名、MIME、大小和 SHA-256。

### 4.2 文档接收工具

`prepare_document_intake`

- 输入当前 Run 中待处理 PDF 的文件 ID；
- 生成创建接收批次的 proposal；
- 本人确认后校验所有权、会话、MIME、数量和幂等键，并排队预分类；
- 不直接创建合同。

`query_document_intake`

- 按 intake ID 查询，或查询当前会话最近可见的接收批次；
- 返回状态、版本、文件推荐类型、置信度、OCR 状态、重复 SHA 提示和安全错误；
- 只读。

`prepare_document_type_confirmation`

- 输入 intake ID、预期版本、每个文件的确认类型和合同分组；
- proposal 展示推荐值与确认值差异；
- 本人确认后只为销售合同文件创建完整 OCR 作业；
- 非销售合同只分类归档。

`prepare_document_ocr_retry`

- 只允许失败且未被其他有效租约处理的 OCR 作业；
- proposal 展示失败阶段、尝试次数和重试范围；
- 本人确认后重新排队，不同步调用 OCR 服务。

### 4.3 合同复核与审批工具

`query_sales_contract_intake`

- 返回 OCR 原值、标准化候选、置信度、来源文件和页码；
- 返回当前用户可见的项目候选、项目客户关系、项目内模具候选、合同关系候选和两级流程选项；
- 不自动选项目、模具或合同关系。

`prepare_sales_contract_intake_review`

- 输入分组 ID/版本、项目 ID/版本、字段确认策略及修正值、全部模具映射、合同关系；
- proposal 固化本次确认的全部字段快照，并展示原值、候选值和最终值；
- 本人确认后调用领域服务写入人工确认值；
- 客户冲突、项目版本变化、缺少模具映射或付款超额必须阻断。

`prepare_sales_contract_from_intake`

- 只接受 `READY_FOR_DRAFT`；
- 重新校验分组、项目、文件哈希、模具、合同关系、权限和流程版本；
- proposal 经本人确认后原子创建合同草稿并提交 `SALES_SUPERVISOR → FINANCE_OWNER`；
- 两级全部通过后合同才能生效。

## 5. Agent Run 生命周期

### 5.1 初始上传 Run

- 一次上传批次只创建一个附件触发 Run；多个 PDF 一并绑定。
- Harness 依据服务端可信的 `run_trigger=ATTACHMENT_UPLOAD` 和 `application/pdf` 元数据激活 Skill。
- 激活不依赖固定提示词、文件名或 OCR 内容。
- 非 PDF 不激活本 Skill。

### 5.2 Proposal 确认恢复

- prepare Tool 只生成 proposal，不直接写业务数据。
- 用户通过通用 ProposalCard 确认后，原 Run 按现有机制恢复并取得权威回执。
- 取消 proposal 不产生接收、分类、复核或合同记录。

### 5.3 OCR 异步边界

- 接收或类型确认的回执只表示作业已排队。
- Agent Run 随后结束，不等待、不轮询 OCR。
- Worker 领取作业后按固定管线执行 `PyMuPDF → PaddleOCR（按需）→ Qwen3 文本结构化 → Schema 校验 → 候选持久化`。
- 纯文本页直接使用可信文本层；纯图片页整页渲染后 OCR；图文混合页提取文本层并对大面积图片区域 OCR，再按坐标合并去重。
- PaddleOCR 成功而 Qwen3 失败时保留不可变页面识别结果；重试优先复用该结果，不重复 OCR。
- Worker 保存候选并发送完成/失败通知；通知不得声称合同已创建或生效。

### 5.4 跨 Run 续办

- 用户收到通知后在原会话继续办理，创建新的 Agent Run。
- 新 Run 激活同一 Skill，通过查询 Tool 读取数据库状态后续办。
- Skill 不依赖旧 Run 的模型输出作为业务事实；ID、版本和状态均重新查询。

## 6. 文档与合同规则

### 6.1 文档类型

固定为：

- `BID_NOTICE`
- `CUSTOMER_START_NOTICE`
- `SALES_CONTRACT`
- `MOLD_DRAWING`
- `OTHER`

推荐类型只是候选。用户必须通过 proposal 确认或修正，系统不得静默采用推荐值。

### 6.2 OCR 与人工确认

- 文本 PDF 优先读取内嵌文本；纯图片 PDF 逐页渲染后调用 PaddleOCR；图文混合 PDF 合并文本层和图片区域 OCR 并去重；页面以 300 DPI 为目标渲染，大画幅输入按比例限制在低于 OCR 服务最大像素的安全范围内；
- PaddleOCR 返回逐页文字块、坐标、方向和置信度，Qwen3 只接收带页码与文字块编号的纯文本；
- Qwen3 输出必须引用来源文字块，无法回溯的字段不得入库；
- 机器原值、标准化候选值和人工确认值分别保存；
- 每个字段保留文件 ID、文件名、页码、文字块编号及可用坐标；
- OCR 服务原始响应、PDF 原文、页面图片和模型原始响应不得写日志；
- 印章、签名和低置信度文字不得由模型猜测，必须形成警告并交人工核对；
- 完整 OCR 仅用于人工确认后的销售合同文件。

### 6.3 项目、客户和模具

- 项目必须已存在，唯一候选也必须人工确认；
- 目标项目必须已有 `project_profile.customer_id`；
- OCR 客户与项目客户冲突时阻断；
- OCR 不创建客户、项目或模具；
- 每条合同模具明细必须关联目标项目内正式模具后才能提交审批。

### 6.4 合同关系和财务

- `DUPLICATE`：只关联已有合同，不创建新合同或 BPM；
- `REVISION`、`REPLACEMENT`：新合同生效时关闭直接目标；
- `SUPPLEMENT`：与原合同并行有效；
- 历史回款保留原合同关联，不迁移、不覆盖；
- 当前应收按当前有效合同计算，已收金额可跨版本族汇总；
- 付款节点合计超过合同金额必须阻断，其他差异进入财务可见警告。

## 7. 持久化和 Worker

保留既有：

- `file_object`、`run_file`；
- `document_intake`、`document_intake_file`、`document_ocr_job`；
- `document_extracted_field`、`contract_intake_group`、`contract_intake_mold_match`；
- 统一不可变 `contract_attachment`（含 OCR `intake_file_id` 与文档角色）、`contract_mold_line`、`contract_relation`；
- `business_subject`、`contract_detail`、`payment_stage`；
- 专用 OCR Worker、租约、重试和幂等控制。

新增 `document_recognized_page`，按文件、页码、页面内容哈希和识别管线版本不可变保存：

- 来源类型 `TEXT_LAYER`、`PADDLE_OCR` 或 `HYBRID`；
- 规范化页面文字及带稳定编号的文字块 JSON；
- 平均置信度、OCR 引擎/模型版本、文本哈希和创建时间；
- 同一引擎版本的重试复用既有结果，引擎升级生成新版本，不覆盖历史结果。

PaddleOCR 使用独立本地 Docker 服务：GPU Profile 为默认，CPU Profile 为人工切换的备用模式。服务只监听 `127.0.0.1:18081`，一次处理一页，使用独立 Token，不连接数据库、不调用 Qwen3、不持久化 PDF 或图片、不记录识别正文。RTX 3050 4GB 使用 PP-OCRv5 轻量检测/识别模型、批次大小 1，不加载 PP-Structure。

这些是 Tool 调用后由异步任务使用的底层实现，不是用户可绕过 Tool 的第二入口。唯一 `sales_contract_intake` Skill 仍只调用既定 8 个 Agent Tool。

## 8. 删除双轨实现

Tool/Skill 链路完成并通过回归后删除：

- `contract_intake_api.py` 及其 Router 注册；
- `/api/document-intakes*`、`/api/contract-intakes*` 直接业务入口；
- `DomainUploadFlow.vue`；
- `ContractIntakePanel.vue`；
- `contractIntake.ts` 及其前端测试；
- “合同识别”专用工作区页签和跳转；
- 仅验证上述直接入口的测试。

必须保留：通用文件 API、通用 Agent Run API、通用 proposal/human-action API、领域服务、Worker、权限、审计、模型和迁移。

## 9. Harness 要求

- Skill 支持声明附件触发条件；
- Skill 激活后必须加载完整 `SKILL.md`，不能只显示名称；
- 激活状态及工具集合写入 Run checkpoint，崩溃恢复后保持一致；
- 附件触发不能绕过用户能力授权和 Tool 权限；
- 普通 Run 不因历史附件自动激活本 Skill；
- 不使用文件名、合同样例或固定提示词过拟合路由；
- 模型异常时优先排查附件上下文、Skill 激活、工具暴露、Schema、checkpoint 和协议。

## 10. 权限、并发和审计

- 所有 Tool 先校验 Capability 和当前 Grant；
- prepare Tool 的确认阶段重新校验权限、版本和 proposal 哈希；
- intake、项目、模具、文件、流程均使用当前版本或当前哈希；
- 权限变化、项目变化、文件变化或流程变化使旧 proposal 失效；
- 重复确认不重复排队、不重复复核、不重复创建合同；
- 合同文件关联后统一受 `sales_contract.read` 控制，上传者不得旁路；
- 审计不得保存 PDF 原文或 OCR 原始响应。

固定审计动作：

- `document.intake.created`
- `document.type.confirmed`
- `document.ocr.completed`
- `document.ocr.failed`
- `document.ocr.retry_queued`
- `contract.intake.reviewed`
- `contract.document.linked`
- `contract.relationship.confirmed`

## 11. 验收场景

至少覆盖：

1. 多 PDF 上传只创建一个附件触发 Run；
2. PDF 附件激活唯一 Skill，普通 Run 不激活；
3. 激活后完整 Skill 指令进入模型上下文；
4. 当前 Run 只能查询本次绑定附件；
5. intake proposal 未确认时不排队；
6. 人工类型确认只为销售合同排队完整 OCR；
7. 非销售合同只分类归档；
8. OCR Worker 异步执行，Run 不持续轮询；
9. OCR 完成后的新 Run 可从数据库状态续办；
10. OCR 原值、候选值、人工值及来源可追溯；
11. 唯一、多项目、无项目和无权限候选；
12. 客户关系缺失或冲突阻断；
13. 全部模具映射门禁；
14. 重复、修订、补充和替代语义；
15. 业务主管通过但财务未通过时不生效；
16. 财务通过后生效但不产生实际回款；
17. OCR 重试幂等且需要本人确认；
18. 撤权后上传者无法读取合同 PDF；
19. 专用页面和直接 intake API 已删除；
20. 上传到生效的端到端路径全部通过 Tool 和 Skill；
21. 纯文本、纯图片和图文混合 PDF 均形成可回溯页面文字；
22. PaddleOCR GPU 健康检查报告 CUDA、设备和模型版本；GPU 不可用时明确失败，不静默切 CPU；
23. PaddleOCR 成功而 Qwen3 失败后的重试复用既有页面识别结果；
24. Qwen3 只收到文字，不收到 PDF、PNG 或 base64 图片。

## 12. 成功标准

- 整套流程由一个 Skill 编排多个 Tool；
- 用户侧只有通用上传、会话、通知和确认卡；
- 不存在合同专用菜单、复核页面或直接写 API；
- 异步 OCR 不占用长时间 Agent Run；
- 图片型 PDF 由本地 PaddleOCR GPU 服务识别，识别文字再交给 Qwen3 文本模型；
- 新 Run 能在同一会话按持久化状态续办；
- 所有正式数据可追溯到原 PDF、OCR 候选、人工确认和审批；
- 目标库、真实 OCR、角色配置和用户验收未完成前不得宣称正式交付完成。
