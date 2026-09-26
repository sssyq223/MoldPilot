# 上传识别工程联络单并触发 Skill/Tool 流程实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度；本计划不要求新增按钮、传统业务页面或直接业务写库 API。

**目标：** 用户上传工程联络单 PDF/图片后，由文档识别结果触发工程联络 Skill，使用现有 `prepare_contact_*` Tool 逐级生成 Proposal；只有本人确认后才创建联络单、关联原件和建立责任事项。

**架构：** 上传仍由现有文件入口处理，OCR/文本模型只产生带来源块和置信度的分类、字段候选。文档确认事件激活 `engineering_contact_collaboration` Skill；Skill 只编排查询 Tool 和 `prepare_*` Tool，不直接写 `ContactCase`。工程联络单创建、原件关联、责任事项、分派、反馈、方案、复验和关闭均沿用现有人工确认链。

**技术栈：** FastAPI、SQLAlchemy、Pydantic、现有 PaddleOCR loopback 服务、现有文档模型、Tool Gateway、Skill 注册契约、Agent Run/Step/Proposal/HumanIntent。

**规格：** 本计划即为经确认的设计；现有约束见 `docs/ENGINEERING_CONTACT_COLLABORATION.md`、`docs/REQUIREMENTS_TRACEABILITY.md` 和 `docs/DEVELOPMENT_STATUS.md`。

## 全局约束

- 不新增传统 ERP 式菜单或独立工程联络业务页面；上传结果在现有会话文档状态和 Proposal 卡中继续办理。
- 不调用 ERP HTTP、MCP、登录令牌、远程数据库或远程文件目录；项目候选只读取本地 Agent 数据库。
- `query_*` 只读；`prepare_*` 只生成 Proposal；任何业务写入必须经过本人确认。
- 分类为工程联络单不能直接等于创建工程联络单；分类确认、创建 Proposal、Proposal 确认分别保留证据。
- OCR 不由视觉大模型直接代替；扫描页使用现有 PaddleOCR，文字层优先，文档模型只处理文字块及来源元数据。
- 当前本机数据库为 `mb0d0e000016`；本计划不执行数据库迁移，不把 `mb017` 本地设变表混入本流程。
- 原始上传文件、识别页、来源块、分类快照和人工修改均需可追溯；不得把 API Key、Token、完整提示词或敏感凭据写入审计事件。
- 低置信度、项目多候选、字段冲突、责任部门不明确或无法确认办理模式时必须停在人工核对，不得自动创建或派发。

## 现状与根因

当前链路已经具备：

- `DocumentIntake`、`DocumentIntakeFile`、`DocumentOcrJob`、`DocumentRecognizedPage`、`DocumentExtractedField` 和 `AuditEvent` 文档事实；
- PDF 文本层/PaddleOCR 页面识别与文档模型分类；
- `query_uploaded_files`、文档接收 Tool 和文档状态展示；
- `engineering_contact_collaboration` Skill 以及 `query_contact_*`、`prepare_contact_*` Tool；
- 工程联络单、协作事项、方案、复验和关闭的确认链。

当前不能端到端触发的根因：

1. 文档类型集合只有 `BID_NOTICE`、`CUSTOMER_START_NOTICE`、`SALES_CONTRACT`、`MOLD_DRAWING`、`OTHER`，没有工程联络单类型。
2. 自动上传处理只从 PDF 创建文档接收批次；图片没有进入同一 OCR 任务链。
3. 预分类完成事件只保存通用分类快照，没有工程联络字段抽取契约。
4. 文档确认入口只服务合同/中标分类，未把确认结果转成工程联络 Skill/Tool Proposal。
5. 工程联络原件关联、联络单创建和责任事项是多个独立 Tool，不能用一个自动写库动作替代。

## 数据与门禁设计

### 1. 不新增业务表的 MVP

复用已有结构：

- 文档接收与文件版本：`DocumentIntake`、`DocumentIntakeFile`；
- OCR 页面和来源块：`DocumentRecognizedPage`；
- 字段候选：`DocumentExtractedField`；
- 分类、确认、Proposal 触发证据：`AuditEvent`；
- 工程联络业务事实：现有 `ContactCase`、`ContactTask`、`ContactRecord`、`ContactResolution` 和附件表。

由于现有 `DocumentIntakeFile` 的数据库约束只接受既有文档类型，`ENGINEERING_CONTACT` 不直接写入 `suggested_type`/`confirmed_type`。工程联络分类和确认快照存入已有 `AuditEvent.detail`，接口读取时以事件快照为准；旧合同/中标字段继续保持原语义。这样不需要执行新迁移。

### 2. 触发状态

```text
ATTACHMENT_UPLOADED
  -> DOCUMENT_PRECLASSIFYING
  -> ENGINEERING_CONTACT_CANDIDATE
  -> HUMAN_CLASSIFICATION_CONFIRMED
  -> CONTACT_CREATE_PROPOSAL_READY
  -> CONTACT_CONFIRMED
  -> ATTACHMENT_LINK_PROPOSAL_READY
  -> CONTACT_TASK_PROPOSAL_READY
```

任何一步出现低置信度、字段冲突、项目候选不唯一、文件重复或授权变化，都进入 `NEEDS_REVIEW`，不得进入下一自动编排步骤。

### 3. 工程联络分类字段

分类快照必须包含：

- `document_type = ENGINEERING_CONTACT`；
- `classifier_version`、`confidence`、`decision`；
- 页码、区块 ID、摘录和坐标组成的证据块；
- `project_ref`、`customer_ref`、`customer_name`、`mold_number`、`product_ref`；
- `title`、`description`、`current_stage`、`problem_source`、`change_type`、`urgency`、`category`；
- `mode` 候选：`ONLINE` 或 `HISTORY`；
- 字段级置信度、冲突和未识别字段；
- 原始文件版本 ID 和 SHA256。

缺少 `project_ref` 不直接失败，而是调用本地项目候选查询；没有唯一候选时停在人工作业。

## 文件清单

### 修改

- `backend/domain_packs/mold/manifest.py`：将工程联络候选文件接入已有上传后文档接收入口；不改变普通附件和合同处理边界。
- `backend/domain_packs/mold/erp/commercial/contract_intake_models.py`：仅在确有必要时扩展代码层常量；不把新类型写入旧数据库 CheckConstraint，工程联络分类事实使用审计快照。
- `backend/domain_packs/mold/erp/commercial/document_classification.py`：增加工程联络规则、冲突处理和类型模型输出；保持未知类型为人工复核，不把模型高置信度当作业务确认。
- `backend/domain_packs/mold/erp/commercial/ocr_provider.py`：扩展分类输出契约和文档模型提示/解析白名单，保留来源证据、置信度和字段候选；不接受模型输出中的业务确认状态。
- `backend/app/document_worker.py`：在预分类完成事件中保存工程联络分类快照；对旧 `suggested_type` 只写既有类型，对新类型不触发旧合同字段流程。
- `backend/domain_packs/mold/erp/commercial/document_workflow_api.py`：增加只读分类证据读取和“工程联络类型确认”入口；确认只记录事件，不直接创建 `ContactCase`。
- `backend/domain_packs/mold/erp/commercial/document_workflow.py`：增加工程联络分类确认的幂等事件记录和 Skill/Tool 触发入口；重复确认不得重复生成 Proposal。
- `backend/domain_packs/mold/tools/erp/commercial/document_intake_tools.py`：增加查询分类证据和准备工程联络文档确认 Proposal 的 Tool 契约；不直接执行工程联络写入。
- `backend/domain_packs/mold/tools/erp/change/contact_tools.py`：复用 `prepare_contact_create`/`prepare_contact_attach`；如需从识别快照填充参数，只增加受控输入适配，不改变既有创建确认链。
- `backend/domain_packs/mold/tool_gateway.py`：注册工程联络文档 Skill、激活条件、Tool 白名单、Schema 路由和权限映射；上传触发只开放查询和准备动作。
- `backend/domain_packs/mold/proposal_handlers.py`：登记文档分类确认、工程联络创建/附件/事项 Proposal 的确认处理范围，禁止未登记 Tool 进入 HumanIntent。
- `backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md`：补充“上传分类确认后进入 Proposal”的编排步骤和停止条件。
- `backend/domain_packs/mold/skills/local/document_engineering_contact_intake/SKILL.md`：新增文档入口 Skill，负责识别结果核对、项目候选、模式确认和后续 Skill 激活，不直接写业务事实。
- `web/src/domain-packs/mold/components/DocumentActivity.vue`：在现有后台文档状态中展示工程联络候选、字段证据、项目候选、线上/历史模式和确认卡；不新增独立业务页面。
- `web/src/domain-packs/mold/uiText.ts`：增加工程联络文档类型、字段、状态和审计文案。
- `docs/ENGINEERING_CONTACT_COLLABORATION.md`：补充上传识别入口、双重确认、失败恢复和附件关联规则。
- `docs/REQUIREMENTS_TRACEABILITY.md`：补充上传识别到工程联络 Skill/Tool 的追溯矩阵。
- `docs/DEVELOPMENT_STATUS.md`：记录实现范围和未完成的真实样本/页面验收。

### 创建

- `backend/domain_packs/mold/skills/local/document_engineering_contact_intake/SKILL.md`：上传识别入口 Skill 契约。
- `tests/test_engineering_contact_document_classification.py`：分类、证据、冲突和未知类型测试。
- `tests/test_engineering_contact_document_tools.py`：文档查询、分类确认 Proposal、版本冲突和幂等测试。
- `tests/test_engineering_contact_skill_activation.py`：上传触发、Skill 激活、工具白名单和禁止直接写入测试。
- `tests/test_engineering_contact_orchestration.py`：创建 Proposal、原件关联 Proposal、线上事项 Proposal 和历史补录分支测试。
- `tests/test_engineering_contact_document_no_erp.py`：静态检查不调用 ERP HTTP/MCP/远程数据库/远程目录。

### 不修改

- 既有 `backend/domain_packs/mold/erp/change/contact_models.py`、`contacts.py`、`contact_lifecycle.py` 的业务状态和确认门禁；只通过现有 Tool 调用。
- 既有 ERP 工程联络源文件之外的远程工程联络实现；本需求不复制、不改写 ERP 流程。
- 既有中标、销售合同和开工通知业务事件语义；工程联络类型不得进入 `BID_NOTICE` 事件。

## 执行任务

### 任务 1：锁定文档入口和不迁移数据边界

**目标：** 让工程联络识别使用现有文档接收事实和审计快照，不引入未批准数据库结构。

- [ ] 编写失败测试：验证工程联络分类不会写入旧 `suggested_type`/`confirmed_type`，而是保存在完成/确认事件快照中。
- [ ] 编写失败测试：验证重复文件 SHA256、重复确认操作 ID 和重复事件不会重复触发后续流程。
- [ ] 修改 `manifest.py` 和文档接收入口，使 PDF 与受支持图片进入同一接收批次；不让普通非文档附件自动进入 OCR。
- [ ] 修改 `contract_intake_models.py` 的代码层常量和序列化逻辑，但保留旧数据库约束兼容路径。
- [ ] 运行 `pytest --noconftest tests/test_engineering_contact_document_tools.py -q`，确认初始失败指向缺少入口行为。
- [ ] 运行文档接收和启动预检测试，确认不调用迁移。

### 任务 2：扩展 OCR/分类 Tool 契约

**目标：** 用现有文字层/PaddleOCR/文档模型生成工程联络分类候选和可定位字段，不让模型直接产生业务确认。

- [ ] 编写分类正例测试：清晰的 PDF、扫描 PDF、单页图片、多页图片和中英文混合材料均输出 `ENGINEERING_CONTACT` 候选或明确 `OTHER/NEEDS_REVIEW`。
- [ ] 编写分类反例测试：销售合同、中标通知、客户开工通知和模具图纸不被识别为工程联络单。
- [ ] 编写来源证据测试：每个关键字段至少关联页码、区块 ID、摘录/坐标和置信度；缺来源时拒绝作为可确认字段。
- [ ] 修改 `document_classification.py`、`ocr_provider.py` 和 `document_worker.py`，加入工程联络分类输出、字段白名单和事件快照。
- [ ] 将扫描页识别固定为 PaddleOCR，文档模型只读取文字块；限制单批页数、模型调用次数和超时沿用现有策略。
- [ ] 运行分类定向回归和 `compileall`。

### 任务 3：建立“分类确认”Skill/Tool 门禁

**目标：** 用户确认“这是工程联络单”后才允许进入工程联络编排；确认本身不创建联络单。

- [ ] 编写状态测试：未完成 OCR、低置信度、分类冲突、过期 row version 和非本人文件不能确认。
- [ ] 编写幂等测试：相同 `Idempotency-Key` 返回同一确认事件，不重复生成后续 Proposal 触发记录。
- [ ] 修改 `document_intake_tools.py` 增加只读分类结果 Tool 和工程联络分类确认 Proposal Tool。
- [ ] 修改 `document_workflow_api.py`/`document_workflow.py`，把确认写成 `document.classification.confirmed` 和专用工程联络事件；事件只保存安全快照。
- [ ] 为 `document_engineering_contact_intake` Skill 注册激活条件：`ATTACHMENT_UPLOAD` 后仅在分类快照为工程联络候选时激活。
- [ ] 配置 Skill 可用 Tool 集合：分类读取、项目候选读取、`prepare_contact_create`、`prepare_contact_attach`、`prepare_contact_task`；不授予直接写库能力。
- [ ] 运行 Skill/Tool Registry、Proposal Handler 和确认链测试。

### 任务 4：实现识别字段到 `prepare_contact_create` 的受控编排

**目标：** 将 OCR 候选转换为现有工程联络创建 Proposal，不创建业务事实。

- [ ] 编写项目匹配测试：唯一项目候选可生成 Proposal；零候选、多候选、版本变化均停在人工选择。
- [ ] 编写字段校验测试：客户、模具、产品、标题、描述、当前环节、问题来源、变更类别、紧急程度和办理模式缺失时不生成创建 Proposal。
- [ ] 编写模式测试：`HISTORY` 只允许历史过程记录；`ONLINE` 才允许后续责任事项编排。
- [ ] 修改工程联络入口 Skill，使其把字段候选、来源证据、项目版本、附件版本作为 `prepare_contact_create` 输入/展示依据。
- [ ] 修改 `prepare_contact_create` 的展示内容，使用户能看到“原始附件、识别字段、人工修正字段、项目选择、来源块”。
- [ ] 保持创建 Proposal 的确认策略为本人确认；模型或 Worker 不持有确认挑战。
- [ ] 运行 `tests/test_engineering_contact_orchestration.py`，确认未确认前 `contact_case` 数量不变。

### 任务 5：实现原件关联与后续责任事项编排

**目标：** 创建联络单后保留原件版本，并按 Skill/Tool 继续推动协作，不自动派发。

- [ ] 编写附件关联测试：只允许当前用户可见、当前会话、原始 SHA256 未变化的文件；附件撤权或版本变化时拒绝。
- [ ] 编写责任事项测试：只有 `ONLINE` 联络单可以生成 `prepare_contact_task`；责任部门、影响对象、计划动作、交期/金额影响和事实来源不完整时拒绝。
- [ ] 修改工程联络 Skill：创建确认成功后查询新联络单版本，再生成附件关联 Proposal；附件确认后才生成责任事项 Proposal。
- [ ] 对涉及 ERP 的影响只保存本地来源引用和人工依据，不调用 ERP，不伪造 ERP 回执。
- [ ] 保持分派、反馈、方案、复验、关闭分别走既有 `prepare_contact_*` Tool 和确认链。
- [ ] 运行既有工程联络回归加新增编排测试。

### 任务 6：接入现有会话文档状态展示

**目标：** 不做新页面，只让用户在上传所在会话看到识别候选、证据和下一步 Proposal。

- [ ] 编写前端状态映射测试/纯函数测试：`ENGINEERING_CONTACT_CANDIDATE`、`NEEDS_REVIEW`、`CONTACT_CREATE_PROPOSAL_READY`、`CONTACT_CONFIRMED` 均有明确文案。
- [ ] 修改 `DocumentActivity.vue` 展示分类证据、字段来源、项目候选和“确认工程联络单”动作；动作只调用确认 Proposal，不直接创建。
- [ ] 增加原件预览/来源块跳转和字段人工修改展示；修改后重新计算 Proposal 哈希。
- [ ] 在确认创建后展示现有 Proposal 卡和联络单工作区链接；不新增工程联络菜单。
- [ ] 修改 `uiText.ts`、Skill 文案和错误码映射。
- [ ] 按项目约束不运行前端构建、不使用浏览器测试；仅运行静态 TypeScript/文本契约检查。

### 任务 7：生成测试 PDF 并执行真实上传闭环

**目标：** 用脱敏合成工程联络单 PDF 在当前运行环境中走通真实上传链路，不能用单元测试或后台健康检查替代。

- [ ] 创建一次性脱敏合成 PDF，至少包含项目编号、客户、模具号、当前环节、问题来源、变更类别、紧急程度、标题、问题描述、责任域和“线上办理”文字；同时保留一份固定 SHA256 和生成日志。
- [ ] 通过当前工作台真实上传该 PDF，不直接插库、不调用内部写库函数、不伪造 OCR 完成事件。
- [ ] 验证文件进入当前会话，文档 Worker 成功领取，文字层/PaddleOCR、分类 Tool 和来源块均有结果。
- [ ] 验证分类候选展示为工程联络单或明确进入人工复核；确认分类后由 Skill 激活并生成 `prepare_contact_create` Proposal。
- [ ] 验证 Proposal 展示项目候选、识别字段、来源页码/区块和原始文件版本；未点击本人确认前，数据库中不得出现新增 `ContactCase`。
- [ ] 点击本人确认，验证工程联络单创建成功；随后验证原始 PDF 通过 `prepare_contact_attach` 关联成功。
- [ ] 继续用 Skill/Tool 生成一个 `prepare_contact_task` 责任事项 Proposal，确认后验证任务状态、责任部门和附件证据。
- [ ] 记录每个阶段的 Run/Step/Tool/Proposal/AuditEvent ID、状态、错误码和修复前后结果。
- [ ] 发现任何失败时，按“复现 → 定位根因 → 最小修复 → 定向回归 → 重新上传同一测试 PDF”循环，直到完整链路再次通过。
- [ ] 测试完成后删除一次性 PDF、生成日志、临时上传副本和临时脚本；保留正式自动化回归测试源码及脱敏断言，不保留真实业务资料。

### 任务 8：全链路安全、无 ERP 和验收测试

**目标：** 证明上传识别能够进入 Skill/Tool 链，同时不会越过确认和 ERP 边界。

- [ ] 增加无 ERP 静态测试：新增代码不得导入 ERP HTTP/MCP 客户端、远程 DB URL、远程文件目录或 ERP 登录令牌。
- [ ] 增加 Proposal 门禁测试：分类未确认、项目未选择、附件变更、权限变化、版本冲突和重复确认均不写入 `ContactCase`/`ContactTask`。
- [ ] 增加完整正向测试：上传 → OCR → 工程联络候选 → 分类确认 → 项目确认 → 创建 Proposal → 本人确认 → 原件关联 Proposal → 线上事项 Proposal。
- [ ] 增加历史补录测试：上传 → 确认为工程联络单 → 选择 `HISTORY` → 创建/关联历史材料，但不能创建线上任务。
- [ ] 增加失败恢复测试：OCR 失败、分类为 OTHER、字段来源缺失、模型超时、重复上传、事件重放和 Worker 重启均保持原事实不变。
- [ ] 运行本地定向回归、Tool Gateway/Harness/Skill Registry 回归、`compileall` 和 `git diff --check`。
- [ ] 只读运行 `scripts/check_runtime.py`；不执行 Alembic、不更新正式库、不运行前端构建、不联调 ERP。
- [ ] 只有任务 7 的真实测试 PDF 上传闭环、问题修复回归和清理全部完成，才能宣布本功能验收完成。

## 验收标准

1. 上传 PDF/图片后可在现有会话看到工程联络候选及来源证据。
2. 误识别、低置信度和多项目候选会停在人工核对，不自动创建。
3. 用户确认分类后，系统通过 Skill 调用 Tool 生成工程联络创建 Proposal。
4. Proposal 未确认前，`ContactCase`、附件关联和 `ContactTask` 均不增加。
5. 用户确认后可创建工程联络单，并能通过 Tool 关联原始文件。
6. `ONLINE` 可继续生成责任事项 Proposal；`HISTORY` 不得自动派发线上任务。
7. 分派、反馈、方案、复验和关闭仍分别经过既有 Tool/确认链。
8. 同一文件、同一分类确认和同一 Proposal 重放不重复写入。
9. 全流程不调用 ERP HTTP/MCP/远程数据库或远程文件目录。
10. 在未执行数据库迁移的 `mb0d0e000016` 本机库上，既有服务启动预检仍通过。
11. 脱敏合成测试 PDF 已真实上传并走完 OCR、分类、Skill、Tool Proposal、本人确认、联络单、附件和责任事项闭环；所有中途问题均已修复并复测通过。

## 计划自检

- 已覆盖上传入口、OCR、分类、分类确认、Skill 激活、项目匹配、创建 Proposal、附件关联、责任事项、生命周期、前端状态、无 ERP 和回归验证。
- 未增加 `mb017` 迁移依赖；工程联络分类使用现有审计快照承载，避免当前数据库版本再次不匹配。
- 未把分类候选、OCR 结果或模型输出当作业务确认；所有业务写入仍由本人确认 Proposal 完成。
- 未把上传识别流程改成独立传统 API 或 ERP 菜单；入口、编排和动作均保持 Skill/Tool 边界。
