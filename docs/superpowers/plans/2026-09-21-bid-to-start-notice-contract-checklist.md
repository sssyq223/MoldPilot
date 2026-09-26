# 中标到开工通知及合同核算清单关联执行计划

> 面向 AI 工作者：在当前主工作区使用 executing-plans 内联执行，每项按固定 Tool、Skill、BPM 和人工确认边界实施。用户禁止子智能体、worktree、提交、迁移、前端构建和浏览器测试。本计划仅供评审，当前不修改业务代码、不执行正式业务动作。

**目标：** 中标邮件进入系统后自动生成内部开工通知单草稿；人工确认内部承接方向后，由受控工具并行下发给责任部门；部门反馈不互相阻塞，最后由项目部确认整体是否承接，只有项目部最终确认承接或整套委外后，才允许将正式合同和模具核算清单关联到开工通知单，并进入项目执行。

**业务主线：** 中标接收 → 中标匹配 → 开工通知草稿 → 初步承接方向确认 → 开工通知并行下发 → 各部门独立反馈 → 项目部最终承接确认 → 合同及核算清单正式关联 → 项目执行。

**架构依据：** `C:\Users\86187\Downloads\模具项目智能工作台技术开发文档_V3.6.docx`。V3.6 规定 `inbound_record`、报价与承接、`start_notice`、合同版本和文件绑定分别建档；ERP 只读，Agent 项目在 `agent_db` 保存本项目新增事实；Skill 只能组合已发布 Tool，正式决定由 BPM 和人工入口保存。

**技术栈：** FastAPI/Pydantic、SQLAlchemy/PostgreSQL、现有 Agent Harness、Tool Gateway、Skill Registry、BPM、Vue3 工作台和文件版本服务。

## 执行状态（2026-09-21）

- [ ] 仅完成需求和技术方案分析，尚未修改代码。
- [ ] 尚未新增数据库表、路由、Tool、Skill 或 BPM 模板。
- [ ] 尚未上传、下发、承接或关联任何正式业务资料。
- [ ] 本计划中的接口名、状态名和字段需在实现前与现有 contracts 目录及 V3.6 规格逐项核对。

## 全局约束

- 中标邮件是流程入口；合同和模具核算清单不是中标入口。
- Skill 可以自动创建草稿和提出建议，不能代替内部承接决定、部门承接或正式审批。
- `start_notice` 草稿可以在承接确认前存在；状态变化继续使用同一个 ID，不重复建单。
- 部门通知必须按同一开工通知并行下发；一个部门拒绝或退回不得阻止通知传递给其他部门。
- 项目部最终确认是合同和核算清单正式关联的硬门槛；部门拒绝只进入项目部最终复核，不自动终止其他部门流转。
- 没有项目部最终确认的“承接”或“整套委外”，不能把初步选择、部门反馈或通知下发结果当作正式承接。
- 提前收到的合同或核算清单只能保存为待关联附件，不能作为正式合同、正式核算资料或执行依据。
- ERP 只做受控只读查询，不改 ERP 代码、业务表、权限或数据库；本项目只写 `agent_db`。
- 文件必须绑定精确的 `file_version_id`，不能只保存文件名或对象存储路径。
- 正式写入命令使用 `Idempotency-Key`、`rowVersion`/`revision` 和 `operation_id`；未知回执先查原操作，不重复下发。
- 不运行前端构建或浏览器测试；只运行必要的后端 pytest。不得执行数据库迁移；如缺字段，先生成临时 SQL 供评审，不直接迁移。
- 不读取或打印任何真实凭据、合同正文或受限金额；测试使用合成数据和隔离数据库。

## 文件职责

- `backend/domain_packs/mold/manifest.py`：上传事件分类和业务入口装配；中标资料进入 `inbound_record`，不能把所有附件都送入合同 OCR。
- `backend/domain_packs/mold/tool_gateway.py`：工具目录、Skill 依赖、权限、确认策略、effect、调用审计和状态门禁。
- `backend/domain_packs/mold/erp/commercial/document_intake_tools.py`：文件接收、文件分类和文档批次工具复用或扩展。
- `backend/domain_packs/mold/erp/commercial/ocr_provider.py`：保留页级 OCR 结果；分类器必须扩展为“类型 + 业务事件 + 证据 + 冲突”，不能只依赖前三页和单一置信度。
- `backend/domain_packs/mold/erp/commercial/contract_intake_tools.py`：正式合同识别、字段确认和合同候选流程；须增加开工通知关联检查。
- `backend/domain_packs/mold/erp/core/domain_models.py`：本地 `inbound_record`、承接、开工、合同及关联模型的领域定义，按实际现有模型复用。
- `backend/domain_packs/mold/erp/core/domain_api.py`：项目、模具及本地业务对象的查询和确定性校验入口。
- `backend/domain_packs/mold/erp_adapter.py`：仅封装 ERP 只读项目/模具查询；不承接本流程的本地写入。
- `contracts/`：OpenAPI、Tool Schema、Skill manifest、BPM DSL、事件和错误码的共同协议来源。
- `backend/domain_packs/mold/skills/`：新增 Skill 的说明、适用范围、分支、缺资料处理、确认点和完成标准。
- `backend/domain_packs/mold/tests/` 或现有 `tests/`：承接、下发、部门承接、文件门禁、幂等、权限和恢复测试。

## 业务对象和关系

```text
inbound_record
    ↓ 中标来源及文件
match_decision
    ↓ 报价/历史模具/客户资料匹配
quotation / quotation_version
    ↓
acceptance_decision
    ↓ 人工承接决定
start_notice 草稿
    ↓ 人工确认后正式下发
start_notice_department_ack × N
    ↓ 部门反馈汇总及项目部最终决定
正式合同 contract / contract_version
模具核算清单 checklist_file_version
    ↓
project / mold / 后续计划任务
```

主关联规则：

- 中标邮件和中标附件首先绑定 `inbound_record_id`。
- 开工通知草稿和正式开工通知使用同一个 `start_notice_id`。
- 部门承接记录使用 `start_notice_id + department_id + revision`。
- 正式合同和模具核算清单最终都绑定 `start_notice_id`；合同另有 `contract_version_id`，核算清单另有 `file_version_id`。
- 项目使用 `project.start_notice_id` 反向追溯来源。
- 客户模号、内部模号、项目号、合同号和订单号分别保存语义，不因值相同而合并字段。

## 任务 0：中标 PDF 精确识别与 Skill 触发门禁

中标 PDF 是业务流程入口，但“文档中出现中标二字”不等于该文件就是中标通知。合同、报价单或客户开工通知中也可能引用“中标”，因此必须先完成文档类型和业务事件的双重识别，再允许触发 `bid_to_start_notice` Skill。本任务是任务 1 的前置门禁，当前仅纳入执行方案，不直接修改业务代码。

- [ ] 上传时先保存原始文件、`file_version_id`、SHA256、上传人、上传时间和来源元数据，创建 `inbound_record`；不能根据文件名直接判定中标。
- [ ] 复用现有 PDF/OCR 识别链，保留页码和文字块；前 3 页可作为快速判断，但快速判断没有足够正向证据时必须回退到全文识别，不能因为中标信息出现在第 4 页以后而误判。
- [ ] 识别结果同时保存 `document_type` 和 `event_type`：`BID_NOTICE`、`CUSTOMER_START_NOTICE`、`SALES_CONTRACT`、`OTHER`，以及 `BID_WON`、`CUSTOMER_START`、`CONTRACT_SIGNED`、`UNKNOWN`。
- [ ] 建立确定性正向证据规则：至少命中一组中标语义（如中标通知、中标结果、中选、项目中标、恭喜贵司中标、确认承接）和一组项目上下文（客户、项目号、客户模号、订单号、金额或交期）。
- [ ] 建立来源证据规则：保存发件人、邮件主题、接收时间、正式通知抬头、签章或客户平台来源等可追溯信息；只有截图转 PDF 时，来源元数据缺失应降低结果等级并优先转人工复核。
- [ ] 建立反向冲突规则：合同编号、甲乙方、付款条款和签订日期优先指向销售合同；开工时间、内部审批、部门承接和开工通知字样优先指向客户开工通知；只有价格表或报价明细而没有中标结论时不能判定为中标。
- [ ] 模型只负责语义归一和候选判断，必须返回页码、证据摘录、冲突类型、抽取字段和置信度；不得只返回一个类型和数字置信度。
- [ ] 生成三态结果：`CONFIRMED`、`NEEDS_REVIEW`、`REJECTED`。只有规则证据充分、模型判断一致、无强冲突且结果为 `BID_NOTICE + BID_WON + CONFIRMED` 时，才允许自动触发中标 Skill。
- [ ] 不使用单一 `confidence >= 0.8` 作为自动触发条件；自动触发阈值、证据组数、冲突扣分和人工复核比例必须通过合成样本集校准，并在 `classifier_version` 中固定。
- [ ] 证据不足、扫描质量差、模型与规则冲突、一个 PDF 混合中标通知和合同、或无法确认来源时进入 `NEEDS_REVIEW`；人工确认前不得生成开工通知草稿，也不得调用承接或合同工具。
- [ ] 合同、客户开工通知、纯报价单、图纸或其他资料进入对应文档类型流程，不触发中标 Skill。已出现“中标”字样但文档主体属于合同的文件，必须保持为合同类型。
- [ ] 识别结果提供可审阅的弹窗：显示类型、业务事件、置信度、证据页码、关键摘录、抽取的客户/项目/模具号和冲突原因，支持“确认是中标信息”“不是中标信息”“重新识别”。
- [ ] 人工确认动作追加不可变分类确认记录，保存 `actor`、`principal`、分类版本、证据快照、时间和 `operation_id`；人工确认后才允许进入任务 1 的中标接收草稿。
- [ ] 对同一 `file_version_id`、SHA256 和来源事件使用幂等键；重复上传只返回原识别结果，不重复创建 `inbound_record`、中标事件或开工通知草稿。
- [ ] OCR 失败、模型超时、模型输出非法 JSON、规则与模型冲突或事件发布失败时，保留原文件和分类中间结果，进入可重试的 `NEEDS_REVIEW`；重试前先查询原 `operation_id`，不能重复产生业务副作用。

建议的识别结果最少包含以下字段：

```json
{
  "document_type": "BID_NOTICE",
  "event_type": "BID_WON",
  "decision": "CONFIRMED",
  "confidence": 0.96,
  "evidence": [{"page": 1, "text": "恭喜贵司中标……", "rule": "AWARD_RESULT"}],
  "conflicts": [],
  "extracted": {
    "customer_name": "",
    "project_name": "",
    "customer_mold_number": "",
    "amount": "",
    "currency": "CNY"
  },
  "classifier_version": "bid-classifier-v1",
  "needs_human_confirmation": false
}
```

识别通过后的调用链固定为：

```text
PDF 上传 → OCR 全文识别 → 规则证据 → 模型语义判断 → 冲突校验
→ BID_NOTICE/BID_WON/CONFIRMED → 触发 bid_to_start_notice
→ 生成内部开工通知草稿 → 人工确认承接方向
```

### 任务 0 的接口和数据契约

以下接口先登记为本地协议草案，实施前必须与现有 `contracts/`、文件版本服务和权限命名逐项核对；接口名称不是对现有代码的假设性调用。

```text
POST /files/{file_version_id}/classify-document
GET  /files/{file_version_id}/document-classification
POST /files/{file_version_id}/document-classification/confirm
POST /inbound-records/{id}/prepare-bid-intake
```

`classify-document` 只执行识别，不创建承接、开工通知或合同；请求至少包含 `file_version_id`、`source_kind`、可选的邮件主题/发件人/接收时间和 `Idempotency-Key`。返回 `classification_id`、`document_type`、`event_type`、`decision`、`confidence`、证据页码与摘录、冲突、抽取字段、分类器版本和是否需要人工确认。

`document-classification/confirm` 是人工动作，只接受 `BID_NOTICE`、`CUSTOMER_START_NOTICE`、`SALES_CONTRACT` 或 `OTHER` 等登记类型，并要求提交当前 `classification_id`、`rowVersion`、确认理由和 `Idempotency-Key`。确认中标后才能发布 `bid_notice.confirmed` 事件；事件必须携带 `file_version_id`、`classification_id`、`inbound_record_id`、证据快照和 `operation_id`。

`prepare-bid-intake` 只能消费已确认的 `BID_NOTICE + BID_WON`，复用现有中标接收草稿工具，返回中标接收版本和待补字段，不直接承接、拒单、下发开工通知或建立正式合同。

分类记录建议采用追加式版本，至少保存：

```text
classification_id
file_version_id / content_sha256
inbound_record_id
document_type / event_type / decision
rule_evidence_json / model_evidence_json / conflicts_json
extracted_fields_json
classifier_version / ocr_version
confirmed_by / confirmed_at / confirmation_reason
operation_id / created_at
```

分类状态与 `inbound_record` 状态必须分开：文件可以已经上传，但分类仍然是 `PENDING`；分类为 `NEEDS_REVIEW` 时，不能把 `inbound_record` 推进到 `MATCHING`，更不能触发中标 Skill。只有 `CONFIRMED` 后才能进入中标匹配。

### 任务 0 的实现顺序

1. 先确认当前 PDF/OCR、文件版本、`inbound_record` 和现有文档分类表的真实字段与写入边界。
2. 编写纯函数规则识别器，先输出证据和冲突，不执行任何业务写入。
3. 扩展模型分类结果，使其返回页码、摘录、业务事件、冲突和字段，而不是只有类型与置信度。
4. 在规则与模型结果汇合处实现三态决策和幂等校验。
5. 接入分类查询和人工确认接口，确认事件只发布一次。
6. 将文件上传后的自动处理从“所有 PDF 直接进入合同 intake”调整为“先分类，再按类型分流”；中标确认才进入 `bid_to_start_notice`。
7. 使用合成 PDF 和合成 OCR 文本完成后端测试，再由潘总验收前端弹窗和上传体验。

## 状态和事件

### `inbound_record`

`RECEIVED → CLASSIFYING → CLASSIFIED → MATCHING → MATCHED → CONSUMED`，分类证据不足、冲突或拒绝时进入 `NEEDS_REVIEW`；重复来源复用原记录。`CLASSIFIED` 只有在 `BID_NOTICE + BID_WON + CONFIRMED` 或其他明确文档类型已确认后才能进入后续分流。

### `acceptance_decision`

`DRAFT → SUBMITTED → APPROVED` 或 `REJECTED`。拒单保留原因和依据，不创建可执行任务。

### `start_notice`

`DRAFT → WAITING_ACCEPTANCE → READY_TO_ISSUE → ISSUED_WAITING_DEPARTMENT_ACK → DEPARTMENT_ACTIONS_IN_PROGRESS → PROJECT_FINAL_REVIEW → PROJECT_ACCEPTED / PROJECT_FULL_OUTSOURCE_ACCEPTED / PROJECT_REJECTED → READY_FOR_ASSOCIATION → EXECUTION_READY`。

部门拒绝或退回时，部门任务自身进入 `REJECTED` 或 `RETURNED`，开工通知总体状态进入 `PROJECT_FINAL_REVIEW`，但其他部门的 `PENDING` 任务继续可接收、承接和记录。部门反馈收齐或达到项目部可复核条件后，由项目部提交最终决定。退回、取消、暂停、终止必须有独立原因和审计事件，不能用通用 `PATCH status` 绕过命令校验。

### 文件关联

- 合同或核算清单在门槛未满足时：`UPLOADED_PENDING_ASSOCIATION`。
- 项目部最终确认承接或整套委外后：进入 `READY_FOR_ASSOCIATION`，生成绑定提案。
- 人工确认后：`FORMALLY_LINKED`。
- 文件版本改变后：旧绑定保留，新版本重新校验，不覆盖历史。

## 任务 1：中标接收和自动生成开工通知草稿

- [ ] 编写 `bid_to_start_notice` Skill 规格：目标、触发事件、输入、必需 Tool、缺资料分支、最大步骤、完成依据和结果模板。
- [ ] 固定 `bid_to_start_notice` 的触发前置：仅消费 `bid_notice.confirmed`，验证 `classification_id`、`file_version_id`、`inbound_record_id`、证据快照和幂等键；没有分类确认事件时只返回缺项。
- [ ] 明确中标邮件、客户附件和人工上传资料的文件分类，创建或复用 `inbound_record`，保存来源 ID、哈希、上传人和时间。
- [ ] 实现或复用 `query_quotation_candidates`、`query_historical_mold_candidates`、`query_customer_context` 等只读 Tool。
- [ ] 实现 `prepare_inbound_match`：返回候选、来源、匹配理由、冲突和待人工字段，不写正式承接结果。
- [ ] 实现 `prepare_start_notice`：根据匹配后的中标资料生成 `start_notice` 草稿，保存客户、项目名称、客户模号、金额、加工方式、交期、附件和来源。
- [ ] 只有任务 0 产生 `BID_NOTICE + BID_WON + CONFIRMED`，或人工确认 `NEEDS_REVIEW` 为中标后，才触发草稿 Skill；不触发合同 Skill、核算清单 Skill、项目执行或部门下发。
- [ ] 同一来源或相同哈希重复到达时返回已有 `inbound_record`，不重复生成有效开工通知草稿。

## 任务 2：人工承接确认和开工通知确认

- [ ] 建立 `quote_acceptance` BPM 模板：业务提交 → 成本/技术/项目评估 → 总经理或授权人承接、整套委外或拒单决定。
- [ ] 实现 `query_acceptance_context`：展示报价版本、中标资料、历史模具候选、缺失字段和风险。
- [ ] 实现 `prepare_quote_acceptance`：只生成审批材料和决定提案。
- [ ] 人工确认使用专用确认入口；Agent、普通 Tool 和模型不能伪造人工确认令牌。
- [ ] 承接通过后，原 `start_notice_id` 更新为 `READY_TO_ISSUE`；拒单进入 `REJECTED`，保留原始资料和审计。
- [ ] 承接决定、加工方式和适用部门配置写入同一业务版本，后续变更必须新建修订并重新校验。

## 任务 3：下发内部开工通知

- [ ] 实现 `validate_start_notice_issue`：检查承接决定、客户开工依据、项目负责人、加工方式、版本和必需字段。
- [ ] 实现 `issue_internal_start_notice`，对应本地命令 `POST /start-notices/{id}/issue`；写入 `operation_id`、快照、下达人、时间和审计。
- [ ] 该 Tool effect 标记为正式业务提交，必须人工确认；Skill 只能提出调用并等待确认。
- [ ] 下发后按加工方式生成责任部门承接任务，不把通知发送成功误判为部门已经承接。
- [ ] 下发失败、回执未知或版本冲突时，保留明确状态，先查询原 operation，不重复下发。

## 任务 4：部门承接

- [ ] 根据 `execution_mode` 和已确认业务规则生成必需部门清单；内部加工、整套委外和局部委外不能混用部门集合。
- [ ] 实现 `query_start_notice_department_tasks`，返回当前部门、责任人/候选席位、版本和状态。
- [ ] 实现 `prepare_department_acknowledgement`，展示部门承接对象、范围、附件和前置条件。
- [ ] 实现 `confirm_department_acknowledgement`，由部门授权人员人工确认或退回，保存 `actor`、`principal`、意见、版本和时间。
- [ ] 下发时为所有适用部门一次性创建独立承接任务；部门任务之间默认没有串行依赖，不能等待上一个部门结果后才通知下一个部门。
- [ ] 某部门拒绝或退回时，只更新该部门任务并生成原因、通知和审计事件；其他部门的 `PENDING` 任务继续流转，不被自动取消或隐藏。
- [ ] 开工通知总体状态在部门反馈进行中保持 `DEPARTMENT_ACTIONS_IN_PROGRESS`；存在拒绝/退回时进入 `PROJECT_FINAL_REVIEW`，该状态不阻断其他部门承接。
- [ ] 实现 `prepare_project_final_acceptance`：汇总全部部门反馈、拒绝原因、补充条件和当前开工通知版本，提交项目部最终复核。
- [ ] 实现 `confirm_project_final_acceptance`：仅项目部有权限人员可以最终选择 `承接`、`整套委外` 或 `拒绝`，保存最终理由、条件、人员、版本和时间。
- [ ] 项目部最终选择 `承接` 或 `整套委外` 后生成对应最终事件和 `READY_FOR_ASSOCIATION`；选择 `拒绝` 后进入 `PROJECT_REJECTED`，禁止正式关联合同和核算清单。
- [ ] 项目部最终决定不能覆盖部门原始拒绝意见；必须保留部门反馈，并记录项目部对该意见的处理或接受条件。
- [ ] 同一部门同一版本重复确认返回原结果，不生成第二条有效承接记录。

## 任务 5：合同后置关联

- [ ] 合同文件上传时绑定当前可选的 `start_notice_id` 或进入待关联附件，不允许全局按合同编号猜测业务对象。
- [ ] 实现 `sales_contract_intake` Skill：分类合同、解析合同字段、读取开工通知和承接快照、生成合同版本草稿。
- [ ] 实现 `validate_contract_association_gate`：要求项目部最终决定为 `PROJECT_ACCEPTED` 或 `PROJECT_FULL_OUTSOURCE_ACCEPTED`，开工通知版本一致且当前用户有合同权限；不得用初步承接或部门通知结果替代项目部最终决定。
- [ ] 实现 `prepare_contract_version`：处理正式合同、替代合同和补充合同，保留历史版本和金额关系。
- [ ] 合同正式绑定使用 `start_notice_id + contract_version_id`，不覆盖中标资料或报价原值。
- [ ] 合同字段与中标资料、开工通知、客户订单不一致时，生成差异清单并转人工确认。

## 任务 6：模具核算清单后置关联

- [ ] 实现 `mold_cost_checklist_reconciliation` Skill，触发条件为文件分类为核算清单且开工通知已达到 `READY_FOR_ASSOCIATION`。
- [ ] 使用固定后端 XLSX 解析器读取 `合同信息`、`合同模具明细`，不允许模型执行任意 Python、shell 或动态写入 SQL。
- [ ] 解析并校验合同编号、客户、项目编码、内部模具号、客户模号、零件号、工序号、组价分组和合计金额。
- [ ] 组价只出现在分组首行时，按明确分组规则归集，空值不能直接当作零金额。
- [ ] 实现 `validate_checklist_association_gate`：核算清单必须对应项目部最终确认承接或整套委外的 `start_notice_id`；部门拒绝/退回必须已经由项目部在最终决定中明确处理。
- [ ] 实现 `prepare_checklist_binding`：展示核算清单与合同、开工通知和模具的差异。
- [ ] 人工确认后以精确 `file_version_id` 建立正式关联；保留解析快照、哈希、来源和确认记录。
- [ ] 核算清单编号与开工通知内部模具号不一致时阻断，不创建新模具、不自动改绑其他项目。

## 任务 7：Skill、Tool 和能力权限

- [ ] 为每个 Skill 建立不可变 manifest：`skill_id`、version、entrypoint、input_schema、required_tools、requested_permissions、依赖版本、最大步骤和包哈希。
- [ ] `bid_to_start_notice` 只请求中标、报价、历史模具和开工草稿权限。
- [ ] `quote_acceptance_review` 请求承接评估和 BPM 提交权限，但不拥有最终人工确认权限。
- [ ] `internal_start_notice_issue` 请求开工下发权限，必须命中人工确认策略。
- [ ] `department_start_notice_ack` 只允许当前部门授权人员处理本部门任务。
- [ ] `classify_uploaded_pdf` 只负责 OCR、规则证据、模型判断和分类结果，不直接创建承接、合同或开工通知。
- [ ] `query_pdf_classification` 只读返回分类结果、证据页码、摘录、冲突和抽取字段。
- [ ] `confirm_pdf_document_type` 记录人工确认；`prepare_bid_intake_draft` 必须校验已确认的 `BID_NOTICE + BID_WON`，不能仅凭模型建议调用。
- [ ] `sales_contract_intake` 和 `mold_cost_checklist_reconciliation` 只在关联门槛满足后加载完整分支。
- [ ] 缺少必需 Tool、业务权限、对象范围或当前状态时，Skill 不启动完整流程，返回结构化缺项。
- [ ] 工具清单、前端能力展示和网关最终校验使用同一能力解析版本；撤权后新步骤立即阻断。

## 任务 8：协议、接口和审计

- [ ] 在 `contracts/` 中登记 Tool Schema、Skill manifest、BPM DSL、事件信封和错误码。
- [ ] 新增或冻结本地业务接口，至少包括：

```text
POST /inbound-records/{id}/match
POST /quotations/{id}/submit
POST /start-notices/{id}/issue
GET  /start-notices/{id}/department-tasks
POST /start-notices/{id}/department-acks
POST /start-notices/{id}/department-ack-resolutions
POST /start-notices/{id}/project-final-decision
POST /contracts/{id}/versions
POST /start-notices/{id}/contract-bindings
POST /start-notices/{id}/checklist-bindings
POST /files/uploads
POST /files/{id}/complete
POST /files/{file_version_id}/classify-document
GET  /files/{file_version_id}/document-classification
POST /files/{file_version_id}/document-classification/confirm
POST /inbound-records/{id}/prepare-bid-intake
```

- [ ] 所有创建、提交、下发、承接、正式绑定和重试命令使用版本校验、幂等键和 `operation_id`。
- [ ] 记录 `skill_version`、`tool_version`、run、step、参数哈希、来源文件版本、操作者、被代表身份和最终业务回执。
- [ ] 合同、核算清单、开工通知和部门承接均支持前后版本追溯，历史文件不被无痕覆盖。
- [ ] 文件下载和预览继承业务对象、项目、部门和字段权限，不因已知 object key 绕过权限。

## 任务 9：测试和验收

- [ ] 中标邮件创建唯一 `inbound_record`，重复邮件不重复生成记录。
- [ ] PDF 上传先进入 `CLASSIFYING`，不能因为是 PDF 就直接进入销售合同 intake；分类失败、模型不可用或 OCR 为空时进入 `NEEDS_REVIEW`，不触发 Skill。
- [ ] 原生文字 PDF、扫描 PDF 和截图转 PDF均经过同一识别链；中标证据在后页时能够回退全文识别，来源元数据缺失时进入人工复核。
- [ ] 真正的中标通知同时满足中标语义和项目上下文证据后，才进入 `BID_NOTICE/BID_WON/CONFIRMED` 并触发一次 Skill。
- [ ] 合同正文中出现“中标”、客户开工通知、纯报价单、图纸和多文档混合 PDF 不得自动触发中标 Skill；冲突或证据不足进入 `NEEDS_REVIEW`。
- [ ] 分类结果展示页码、证据摘录、冲突和抽取字段；人工确认后保留不可变分类审计记录。
- [ ] 分类接口重复请求、事件重复投递、Skill 重试和文件重复上传均保持幂等，只产生一个确认事件和一个开工通知草稿。
- [ ] `bid_notice.confirmed` 缺少证据快照、分类版本、文件版本或操作 ID 时被网关拒绝，不能靠前端参数绕过。
- [ ] 多报价、多历史模具候选进入人工选择，Skill 不自动猜测。
- [ ] 自动 Skill 只生成开工通知草稿，不自动承接、不自动下发。
- [ ] 人工拒单后不生成部门承接任务，不允许关联正式合同或核算清单。
- [ ] 人工承接确认后可以下发开工通知，未知回执不会重复下发。
- [ ] 一个部门拒绝时，其他部门仍然收到通知并可以继续反馈；拒绝原因被保留并进入项目部最终复核。
- [ ] 项目部最终选择拒绝时，合同和核算清单正式关联被阻断。
- [ ] 项目部最终选择承接或整套委外后，合同可以生成版本草稿并通过关联门禁。
- [ ] 项目部最终选择承接或整套委外后，核算清单可以解析并生成绑定提案。
- [ ] 合同或核算清单提前上传时只能进入待关联状态，不能成为正式业务依据。
- [ ] 合同编号、客户、项目编码、内部模具号或金额冲突时转人工。
- [ ] 替代合同和补充合同保留历史版本，不重复累计金额。
- [ ] 不同用户、不同项目、不同部门不能读取或操作无权开工通知及附件。
- [ ] Skill 缺 Tool、被禁用、版本失效或权限撤销时不执行后续动作。
- [ ] Agent 重启、事件重复、工具成功但回执丢失时，按原 step/operation 恢复，不重复产生业务副作用。

## 任务 10：交付门禁

- [ ] 先完成协议、领域服务、BPM 和 Tool Schema，再实现 Skill 编排；Skill 不直接写库。
- [ ] 运行必要的后端 pytest，保存真实测试输出和失败恢复证据。
- [ ] 静态检查现有文件、编码、未提交改动和协议差异，不覆盖用户已有修改。
- [ ] 不执行数据库迁移、不连接真实 ERP 写接口、不向客户或部门发送真实通知。
- [ ] 前端页面由潘总自行验收；计划只验证后端状态、接口和工具协议，不用源码替代页面验收。
- [ ] 实现完成后单独报告：已实现、测试通过、未验证的真实通知/部门承接、需要人工确认的业务参数。

## 实施批次和阶段完成定义

### 批次 A：中标 PDF 识别基础

- [ ] 完成文件版本、OCR 页级文本、来源元数据和分类记录的协议核对。
- [ ] 完成规则识别、模型输出校验、三态决策、证据快照和幂等处理。
- [ ] 完成分类查询、人工确认和失败重试接口；确认事件尚未接入正式承接或合同写入。
- [ ] 用合成中标通知、合同、开工通知、报价单、图纸和混合 PDF 完成后端测试。

### 批次 B：中标接收和开工通知草稿

- [ ] 仅消费已经确认的 `bid_notice.confirmed`，完成报价、历史模具和客户资料匹配。
- [ ] 自动生成同一 `start_notice_id` 的内部开工通知草稿，人工确认前不下发、不创建部门任务。

### 批次 C：人工承接和部门并行承接

- [ ] 完成承接、整套委外、拒单的人工弹窗和 BPM 记录。
- [ ] 一次性创建所有适用部门任务；任一部门拒绝不取消其他部门任务。
- [ ] 完成项目部最终承接确认，形成唯一最终状态和审计事件。

### 批次 D：合同和核算清单后置关联

- [ ] 只有项目部最终确认承接或整套委外后，合同和核算清单才进入正式关联提案。
- [ ] 完成合同版本、XLSX 核算清单、文件版本、开工通知和项目模具的差异校验。
- [ ] 完成正式绑定、替代版本、重复上传、冲突和恢复测试。

每个批次完成后必须单独提交：协议变更、后端测试结果、未验证项、人工验收点和回滚/恢复方式；前一个批次未通过，不进入下一个批次。

## 待确认的业务参数

- [ ] 哪些部门属于内部加工的必需承接部门。
- [ ] 哪些部门属于整套委外的必需承接部门。
- [ ] 部门承接是逐部门确认还是允许指定责任人代表确认。
- [ ] 合同和核算清单提前到达时是否允许保存，还是直接拒收。
- [ ] 正式开工通知下发后，客户回复由哪个角色记录以及是否需要附件。
- [ ] 内部模具号在承接确认时生成，还是在开工通知草稿阶段预分配。
- [ ] 合同与核算清单正式关联后，是否还需要项目负责人再次确认。
