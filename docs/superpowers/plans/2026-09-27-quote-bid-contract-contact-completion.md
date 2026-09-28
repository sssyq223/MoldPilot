# 报价、中标、合同与工程联络单完善实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 executing-plans 逐任务实现此计划。项目规则禁止开启或使用子智能体。步骤使用复选框（`- [ ]`）跟踪进度。

**目标：** 在不增加传统 ERP 菜单、不复制 ERP 实时台账的前提下，把报价、中标及邮件分类、销售合同、图二工程联络单完善为四条可从统一工作台真实办理、可人工确认、可审计、可端到端验收的业务闭环。

**架构：** 继续使用现有 Vue 工作台、FastAPI、PostgreSQL、Agent Harness、Skill、Tool、HumanIntent 和 Agent BPM。报价、中标、合同、工程联络单保持独立业务对象，通过项目、模具、附件版本和已确认映射建立关系；查询可以自动执行，任何正式写入、关系确认、审批提交和关闭动作必须经过本人确认。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy、Pydantic、PostgreSQL、Redis、Vue 3、TypeScript、PaddleOCR、OpenAI 兼容文本模型、Agent BPM。

**规格：**

- `C:\Users\86187\Downloads\模具项目全流程管理系统需求规格说明书_V1.1.docx`
- `docs/PRODUCT_CONTRACT.md`
- `docs/ENGINEERING_CONTACT_COLLABORATION.md`
- `docs/BPM_AND_EXCEPTION_CONTRACT.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/DEVELOPMENT_STATUS.md`

## 全局约束

- 仅覆盖 FR-006～FR-032、FR-078～FR-090 及直接支撑这些需求的权限、附件、BPM、通知和审计能力。
- 不新增报价、合同、工程联络单等常驻 ERP 菜单；人工填写使用会话内 Proposal 和按需右侧工作区。
- ERP 现有数据只能经已确认的数据读取边界使用；禁止 Agent 直写 ERP 数据库、复制 ERP 实时台账或调用 ERP 原审批流。
- 当前项目口径不调用、不联调原 ERP HTTP 接口；业务允许读取当前数据库中已有来源数据。
- 报价、中标接收、承接决定、正式开工、合同、工程联络单分别保存，不互相冒充。
- 客户订单号、项目号、客户模号、内部模具号、物料号和合同号保持独立字段语义。
- 多候选、无匹配、数据冲突和来源失败必须进入人工处理，禁止模型自动合并或补造业务事实。
- 扫描文件由 PaddleOCR 提取文字、坐标和置信度；文本模型只接收文字，不直接读取 PDF 或图片。
- 所有正式写操作都采用 Proposal → HumanIntent → 重新校验版本/权限 → 领域写入 → 权威回执。
- 方案审批不等于业务执行成功；工程联络单审批不等于整改完成或关闭。
- 不执行正式数据库迁移。若实现确需数据库字段，只提交迁移源并在隔离测试环境验证；正式库变更必须另行批准。
- 不运行前端构建和内置浏览器；前端页面由潘总在工作台人工验收。
- 只运行本计划列出的必要定向 `pytest`；不以全量测试超时作为局部功能通过证据。
- 临时测试 PDF、DOCX、日志和一次性脚本在验收完成后删除；正式自动化回归测试源码保留。
- 每个任务单独提交，提交前运行对应定向测试和 `git diff --check`。

---

# 一、目标业务闭环

```text
报价资料上传
→ 人工补齐评估表单
→ 报价版本审批并生效
→ 中标文件自动预分类
→ 人工确认中标类型和字段
→ 创建唯一管理员开工草稿
→ 人工决定承接/整套委外/拒绝
→ 承接后选择或建立项目、填写内部模具编号
→ 合同上传、OCR、字段来源复核
→ 合同与项目/模具/开工决定形成候选
→ 人工确认合同映射并提交审批
→ 项目执行中上传图二工程联络单
→ 人工确认联络单和影响范围
→ 批量创建责任事项
→ 方案审批、执行反馈、独立复验
→ 合同/计划/费用影响落实
→ 人工关闭工程联络单
```

# 二、文件结构与职责

## 商务域

- `backend/domain_packs/mold/erp/commercial/quotation_models.py`：报价接收、版本、资料和客户反馈数据。
- `backend/domain_packs/mold/tools/erp/commercial/quotation_tools.py`：报价表单 Proposal、报价版本和反馈办理。
- `backend/domain_packs/mold/tools/erp/commercial/quote_evaluation_tools.py`：报价历史、版本差异和承接上下文查询。
- `backend/domain_packs/mold/erp/commercial/document_classification.py`：中标、合同、工程联络文件的确定性分类证据。
- `backend/domain_packs/mold/erp/commercial/document_workflow.py`：文档类型确认后的可信事件。
- `backend/domain_packs/mold/erp/commercial/bid_start_workflow.py`：中标确认事件到开工草稿的幂等消费。
- `backend/domain_packs/mold/erp/commercial/admin_start_workflow.py`：承接、委外、拒绝、项目选择和内部模具编号流程。
- `backend/domain_packs/mold/tools/erp/commercial/admin_start_notice_tools.py`：管理员开工草稿查询及 Proposal。
- `backend/domain_packs/mold/erp/commercial/contract_intake.py`：合同文档接收、OCR 任务和分类确认。
- `backend/domain_packs/mold/erp/commercial/contract_documents.py`：合同候选字段、来源块和复核。
- `backend/domain_packs/mold/erp/commercial/contract_match_workflow.py`：合同与开工决定、项目、模具的候选关系。
- `backend/domain_packs/mold/tools/erp/commercial/contract_intake_tools.py`：合同复核及登记 Proposal。
- `backend/domain_packs/mold/tools/erp/commercial/contract_tools.py`：合同版本、付款节点、替代/追加和历史结算归属。

## 工程联络域

- `backend/domain_packs/mold/erp/change/local_change_models.py`：本地设变承接、客户模号历史和本地关联。
- `backend/domain_packs/mold/tools/local/change_intake_tools.py`：本地设变承接 Proposal。
- `backend/domain_packs/mold/erp/change/contact_models.py`：工程联络单、责任事项和过程记录。
- `backend/domain_packs/mold/erp/change/contacts.py`：创建、批量事项、反馈和查询投影。
- `backend/domain_packs/mold/erp/change/contact_lifecycle.py`：方案、复验、验收负责人和关闭门禁。
- `backend/domain_packs/mold/tools/erp/change/contact_tools.py`：工程联络 Proposal、表单选项和 HumanIntent。
- `backend/domain_packs/mold/tools/erp/change/change_intake_tools.py`：设变影响上下文查询。

## Skill、注册和工作台

- `backend/domain_packs/mold/tool_gateway.py`：Tool/Skill 注册、权限和场景激活。
- `backend/domain_packs/mold/proposal_handlers.py`：Proposal action 与确认 handler 注册。
- `backend/domain_packs/mold/skills/erp/commercial/*/SKILL.md`：报价、中标、合同办理编排。
- `backend/domain_packs/mold/skills/local/document/document_engineering_contact_intake/SKILL.md`：工程联络文件入口。
- `backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md`：工程联络办理编排。
- `web/src/domain-packs/mold/components/DocumentActivity.vue`：文档分类、状态和字段复核入口。
- `web/src/domain-packs/mold/components/DocumentFieldsDialog.vue`：合同字段及来源复核。
- `web/src/domain-packs/mold/components/EngineeringContactTaskDialog.vue`：工程联络批量责任事项表单。
- `web/src/components/ProposalCard.vue`：通用确认卡和处理结果。
- `web/src/domain-packs/mold/uiText.ts`：业务名称、状态和审计文案。

## 验证

- 报价：`tests/test_quotation_tools.py`、`tests/test_quote_evaluation_tools.py`、`tests/test_quote_tools.py`。
- 中标：`tests/test_document_classification.py`、`tests/test_bid_intake_tools.py`、`tests/test_bid_start_consumer.py`、`tests/test_admin_start_*.py`。
- 合同：`tests/test_contract_ocr.py`、`tests/test_contract_intake_review.py`、`tests/test_contract_intake_proposal.py`、`tests/test_contract_tools.py`。
- 工程联络：`tests/test_engineering_contact_*.py`、`tests/test_contact_*.py`、`tests/test_local_change_*.py`。
- 跨模块：创建 `tests/test_commercial_change_acceptance.py`，只保存正式可重复执行的验收回归。

## 需求到任务的覆盖关系

| 需求范围 | 覆盖任务 | 交付重点 |
|---|---|---|
| FR-006～FR-008 | 任务 2、3 | 报价资料接收、内部/委外评估字段、依据和人工表单 |
| FR-009～FR-012 | 任务 4、13、15 | 加工方式版本、报价版本、客户反馈、历史查询和承接引用 |
| FR-013～FR-016 | 任务 5、6、10、15 | 客户分类、中标/开工资料用途、人工确认和客户规则记录 |
| FR-017～FR-019 | 任务 5、6 | 报价/历史模具候选、承接/拒单、同一中标记录连续修订 |
| FR-020～FR-025 | 任务 6、8、13、15 | 客户开工条件、正式开工状态、部门交接和合同晚到关联 |
| FR-026～FR-029 | 任务 7、8、9 | 合同原件、字段来源、客户编号映射、合同与开工独立关系 |
| FR-030～FR-032 | 任务 9、12、15 | 合同替代/追加、历史结算归属和资金时间核对 |
| FR-078～FR-081 | 任务 10、12 | 设变分类、收费/合同区分、原模具号复用和首次外部模具分流 |
| FR-082～FR-084 | 任务 10、11、12 | 工程联络单字段、附件、影响对象、过程记录和成本线索 |
| FR-085～FR-087 | 任务 1、11、12 | 责任事项、方案审批、执行反馈、复验和受影响任务处置 |
| FR-088～FR-090 | 任务 12、13、14、15 | 跨域对象关联、计划/合同/费用 Proposal、关闭门禁和审计 |

---

# 三、实施任务

### 任务 1：冻结现有基线并收口工程联络批量事项增量

**文件：**

- 修改：`backend/domain_packs/mold/tools/erp/change/contact_tools.py`
- 修改：`backend/domain_packs/mold/erp/change/contacts.py`
- 修改：`backend/app/agent_resume.py`
- 修改：`web/src/domain-packs/mold/components/EngineeringContactTaskDialog.vue`
- 修改：`web/src/domain-packs/mold/components/ContactPanel.vue`
- 测试：`tests/test_engineering_contact_task_proposals.py`
- 测试：`tests/test_engineering_contact_task_batch_confirm.py`
- 测试：`tests/test_engineering_contact_task_skill_continuation.py`

- [ ] **步骤 1：补充失败测试，固定空表单与最终表单的边界**

```python
def test_form_tasks_seed_does_not_create_tasks(db, user, online_case, run):
    proposal = prepare_form_tasks_seed(db, user, online_case, run)
    assert proposal["display"]["form_status"] == "NEEDS_HUMAN_SELECTION"
    assert count_contact_tasks(db, online_case.id) == 0


def test_revised_form_creates_all_tasks_atomically(db, user, online_case, form_payload):
    receipt = confirm_revised_form(db, user, online_case, form_payload)
    assert receipt["status"] == "CONFIRMED"
    assert task_statuses(db, online_case.id) == ["ASSIGNED", "ASSIGNED"]
```

- [ ] **步骤 2：运行测试并确认旧实现不能同时满足两个断言**

运行：

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_engineering_contact_task_proposals.py tests/test_engineering_contact_task_batch_confirm.py tests/test_engineering_contact_task_skill_continuation.py -q
```

预期：至少一个新增断言失败，失败原因明确落在空表单续办、原子创建或 Skill 续办路径。

- [ ] **步骤 3：完成最小实现**

实现要求：

```python
# 初始 Proposal
{"kind": "contact", "action": "form_tasks", "input": {"form": None}}

# 最终确认前必须固定
immutable = ("kind", "action", "case_id", "task_id")
assert revised["case_id"] == original["case_id"]
assert revised["input"]["revision"] == original["input"]["revision"]
```

`contacts.add_form_tasks` 必须在同一事务中完成全部人员资格校验，再批量写入；任一人员失效时保持零新增事项。

- [ ] **步骤 4：运行定向测试确认通过**

运行同步骤 2，预期全部通过。

- [ ] **步骤 5：检查差异并提交**

```bash
git diff --check
git add backend/domain_packs/mold/tools/erp/change/contact_tools.py backend/domain_packs/mold/erp/change/contacts.py backend/app/agent_resume.py web/src/domain-packs/mold/components/EngineeringContactTaskDialog.vue web/src/domain-packs/mold/components/ContactPanel.vue tests/test_engineering_contact_task_proposals.py tests/test_engineering_contact_task_batch_confirm.py tests/test_engineering_contact_task_skill_continuation.py
git commit -m "feat: complete engineering contact batch task flow"
```

### 任务 2：建立四模块统一验收夹具和证据口径

**文件：**

- 创建：`tests/commercial_change_db.py`
- 创建：`tests/test_commercial_change_acceptance.py`
- 修改：`tests/conftest.py`
- 修改：`docs/REQUIREMENTS_TRACEABILITY.md`

- [ ] **步骤 1：编写失败的端到端夹具测试**

```python
def test_fixture_contains_one_linked_business_chain(commercial_change_fixture):
    data = commercial_change_fixture
    assert data.quotation.project_id == data.project.id
    assert data.bid_notice.customer_company == data.customer.name
    assert data.contract.project_id == data.project.id
    assert data.contact.project_id == data.project.id
    assert data.contact.mold_number == data.internal_mold_number
```

夹具必须使用脱敏合成数据，包含一个客户、一个报价资料包、一个已确认中标通知、一个项目、一个内部模具编号、一份销售合同和一张工程联络单。

- [ ] **步骤 2：运行测试确认夹具尚不存在**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_commercial_change_acceptance.py::test_fixture_contains_one_linked_business_chain -q
```

预期：FAIL，提示 `commercial_change_fixture` 未定义。

- [ ] **步骤 3：实现正式可复用夹具**

`tests/commercial_change_db.py` 只负责创建业务事实，不模拟模型自然语言。每个记录返回真实 ID、版本、文件哈希和人工确认人，确保后续任务可引用同一条链路。

- [ ] **步骤 4：运行单测确认通过**

运行同步骤 2，预期 PASS。

- [ ] **步骤 5：提交**

```bash
git add tests/commercial_change_db.py tests/test_commercial_change_acceptance.py tests/conftest.py docs/REQUIREMENTS_TRACEABILITY.md
git commit -m "test: add commercial change acceptance fixture"
```

### 任务 3：完善报价资料到报价版本的会话内人工表单

**文件：**

- 修改：`backend/domain_packs/mold/tools/erp/commercial/quotation_tools.py`
- 修改：`backend/domain_packs/mold/proposal_handlers.py`
- 修改：`backend/domain_packs/mold/tool_gateway.py`
- 修改：`backend/domain_packs/mold/skills/erp/commercial/quote_evaluation_review/SKILL.md`
- 创建：`web/src/domain-packs/mold/components/QuotationDialog.vue`
- 修改：`web/src/App.vue`
- 修改：`web/src/domain-packs/mold/uiText.ts`
- 测试：`tests/test_quotation_tools.py`
- 测试：`tests/test_harness_explicit_tool_activation.py`

- [ ] **步骤 1：编写失败测试，要求初始报价 Proposal 不依赖模型补齐全部字段**

```python
def test_quote_seed_uses_current_run_files_without_writing(db, user, project, run, quote_file):
    evidence = execute(db, user, "prepare_quotation_form", {
        "project_id": project.id,
        "project_version": project.row_version,
        "file_ids": [quote_file.id],
    }, run=run)
    assert evidence["proposal"]["display"]["form_status"] == "NEEDS_HUMAN_SELECTION"
    assert quotation_count(db, project.id) == 0
```

- [ ] **步骤 2：运行测试确认新工具尚未注册**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_quotation_tools.py tests/test_harness_explicit_tool_activation.py -q
```

预期：FAIL，提示 `prepare_quotation_form` 不存在或未注册。

- [ ] **步骤 3：实现种子 Proposal 和受控表单接口**

新增输入只接受真实项目、项目版本和当前 Run 文件：

```python
class QuotationFormSeedInput(StrictModel):
    project_id: str
    project_version: int
    file_ids: list[str] = Field(min_length=1, max_length=20)
```

表单提交后转换为现有 `QuotationProposalInput`，继续复用 `preview_quotation` 和原审批提交逻辑，不创建第二套报价服务。

- [ ] **步骤 4：实现会话内报价弹窗**

`QuotationDialog.vue` 展示内部加工/整套委外分支；内部加工要求成本、工艺和工期，整套委外要求供应商报价、交付日期和要求。弹窗只生成 HumanIntent，不直接调用业务写接口。

- [ ] **步骤 5：运行定向测试**

运行步骤 2 命令，预期全部通过。

- [ ] **步骤 6：提交**

```bash
git add backend/domain_packs/mold/tools/erp/commercial/quotation_tools.py backend/domain_packs/mold/proposal_handlers.py backend/domain_packs/mold/tool_gateway.py backend/domain_packs/mold/skills/erp/commercial/quote_evaluation_review/SKILL.md web/src/domain-packs/mold/components/QuotationDialog.vue web/src/App.vue web/src/domain-packs/mold/uiText.ts tests/test_quotation_tools.py tests/test_harness_explicit_tool_activation.py
git commit -m "feat: add controlled quotation form workflow"
```

### 任务 4：完善报价版本对比、客户反馈和承接门禁

**文件：**

- 修改：`backend/domain_packs/mold/tools/erp/commercial/quote_evaluation_tools.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/quotation_tools.py`
- 修改：`backend/domain_packs/mold/erp/core/domains.py`
- 修改：`backend/domain_packs/mold/skills/erp/commercial/quote_acceptance_review/SKILL.md`
- 测试：`tests/test_quote_evaluation_tools.py`
- 测试：`tests/test_quotation_tools.py`
- 测试：`tests/test_quote_tools.py`

- [ ] **步骤 1：编写失败测试，固定版本差异和承接引用**

```python
def test_quote_context_compares_current_and_previous_versions(db, quote_v1, quote_v2, user):
    result = query_quote_context(db, user, quote_v2.project_id)
    assert result["current_version"]["version"] == 2
    assert result["comparison"]["quoted_amount"] == {"before": "100000.00", "after": "108000.00"}


def test_acceptance_rejects_stale_quote_reference(db, user, quote_v1, quote_v2):
    with pytest.raises(DomainError, match="当前有效报价"):
        prepare_acceptance(db, user, quotation_id=quote_v1.id)
```

- [ ] **步骤 2：运行测试确认差异投影尚不完整**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_quote_evaluation_tools.py tests/test_quotation_tools.py tests/test_quote_tools.py -q
```

- [ ] **步骤 3：实现只读版本对比**

对比字段固定为：报价金额、承诺交期、收款条件、初步加工方式、成本金额、工期、供应商报价和供应商交付日期。对比结果只读，不写回历史版本。

- [ ] **步骤 4：收紧反馈和承接门禁**

客户反馈必须引用有效报价版本；承接必须引用当前 `EFFECTIVE` 版本；报价替换后旧版本不能继续产生新承接 Proposal。

- [ ] **步骤 5：运行定向测试并提交**

```bash
git diff --check
git add backend/domain_packs/mold/tools/erp/commercial/quote_evaluation_tools.py backend/domain_packs/mold/tools/erp/commercial/quotation_tools.py backend/domain_packs/mold/erp/core/domains.py backend/domain_packs/mold/skills/erp/commercial/quote_acceptance_review/SKILL.md tests/test_quote_evaluation_tools.py tests/test_quotation_tools.py tests/test_quote_tools.py
git commit -m "feat: enforce effective quotation chain"
```

### 任务 5：统一中标确认事件到管理员开工草稿的唯一链路

**文件：**

- 修改：`backend/domain_packs/mold/erp/commercial/document_workflow.py`
- 修改：`backend/domain_packs/mold/erp/commercial/bid_start_workflow.py`
- 修改：`backend/domain_packs/mold/skills/erp/commercial/bid_to_start_notice/orchestrator.py`
- 修改：`backend/domain_packs/mold/skills/erp/commercial/bid_to_start_notice/SKILL.md`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/bid_start_tools.py`
- 测试：`tests/test_bid_event_identity.py`
- 测试：`tests/test_bid_start_consumer.py`
- 测试：`tests/test_bid_to_start_db_integration.py`

- [ ] **步骤 1：编写失败测试，要求同一已确认文件只创建一个管理员草稿**

```python
def test_confirmed_bid_event_creates_one_admin_start_draft(db, user, confirmed_bid_file):
    publish_bid_confirmed(db, user, confirmed_bid_file)
    publish_bid_confirmed(db, user, confirmed_bid_file)
    drafts = admin_start_drafts_for_file(db, confirmed_bid_file.id)
    assert len(drafts) == 1
    assert drafts[0].project_id is None
```

- [ ] **步骤 2：运行测试确认重复事件或项目依赖问题**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_bid_event_identity.py tests/test_bid_start_consumer.py tests/test_bid_to_start_db_integration.py -q
```

- [ ] **步骤 3：实现稳定事件身份和幂等消费**

事件键固定由 `intake_file_id + confirmed_type_version + content_sha256` 计算。中标确认阶段不要求项目 ID，不创建项目，不决定承接方式。

- [ ] **步骤 4：删除并阻断重复入口语义**

旧 `BidIntakeCase` 只保留兼容读取时，所有新中标文档进入 `AdminStartNoticeDraft` 主链；Skill 不再要求用户重复发送聊天指令。

- [ ] **步骤 5：运行定向测试并提交**

```bash
git add backend/domain_packs/mold/erp/commercial/document_workflow.py backend/domain_packs/mold/erp/commercial/bid_start_workflow.py backend/domain_packs/mold/skills/erp/commercial/bid_to_start_notice/orchestrator.py backend/domain_packs/mold/skills/erp/commercial/bid_to_start_notice/SKILL.md backend/domain_packs/mold/tools/erp/commercial/bid_start_tools.py tests/test_bid_event_identity.py tests/test_bid_start_consumer.py tests/test_bid_to_start_db_integration.py
git commit -m "fix: unify confirmed bid intake workflow"
```

### 任务 6：完善中标字段候选、承接决定和承接后项目选择

**文件：**

- 修改：`backend/domain_packs/mold/erp/commercial/bid_field_candidates.py`
- 修改：`backend/domain_packs/mold/erp/commercial/admin_start_workflow.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/admin_start_notice_tools.py`
- 修改：`backend/domain_packs/mold/erp/core/business_matching.py`
- 修改：`backend/domain_packs/mold/skills/erp/commercial/bid_intake_review/SKILL.md`
- 测试：`tests/test_bid_field_candidates.py`
- 测试：`tests/test_admin_start_candidates.py`
- 测试：`tests/test_admin_start_post_accept_project.py`
- 测试：`tests/test_admin_start_manual_mold_reference.py`
- 测试：`tests/test_admin_start_proposal_confirmation.py`

- [ ] **步骤 1：编写失败测试，固定正确顺序**

```python
def test_bid_can_be_accepted_before_project_selection(db, admin, draft):
    receipt = confirm_admin_start_decision(db, admin, draft, decision="INTERNAL")
    assert receipt["decision"] == "INTERNAL"
    assert receipt["project_id"] is None


def test_project_is_selected_only_after_acceptance(db, admin, accepted_draft, project):
    receipt = confirm_post_accept_project(db, admin, accepted_draft, project.id)
    assert receipt["project_id"] == project.id
```

- [ ] **步骤 2：运行定向测试确认顺序和候选来源**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_bid_field_candidates.py tests/test_admin_start_candidates.py tests/test_admin_start_post_accept_project.py tests/test_admin_start_manual_mold_reference.py tests/test_admin_start_proposal_confirmation.py -q
```

- [ ] **步骤 3：实现字段候选来源校验**

每个候选必须包含 `source_file_id`、`page`、`block_id`、`excerpt`、`confidence`。候选摘录必须是实际页面文字子串；不满足时拒绝候选而不是降级为模型自由文本。

- [ ] **步骤 4：实现承接后项目和模具线索办理**

承接前项目为空合法；承接后才开放项目候选和内部模具编号填写。内部模具编号是后续合同匹配线索，不强制当作 `ProjectMold.id`。

- [ ] **步骤 5：运行测试并提交**

```bash
git add backend/domain_packs/mold/erp/commercial/bid_field_candidates.py backend/domain_packs/mold/erp/commercial/admin_start_workflow.py backend/domain_packs/mold/tools/erp/commercial/admin_start_notice_tools.py backend/domain_packs/mold/erp/core/business_matching.py backend/domain_packs/mold/skills/erp/commercial/bid_intake_review/SKILL.md tests/test_bid_field_candidates.py tests/test_admin_start_candidates.py tests/test_admin_start_post_accept_project.py tests/test_admin_start_manual_mold_reference.py tests/test_admin_start_proposal_confirmation.py
git commit -m "feat: complete bid acceptance and project selection"
```

### 任务 7：完善合同 OCR 字段来源复核和人工修订

**文件：**

- 修改：`backend/domain_packs/mold/erp/commercial/ocr_provider.py`
- 修改：`backend/domain_packs/mold/erp/commercial/contract_documents.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/document_intake_tools.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/contract_intake_tools.py`
- 修改：`web/src/domain-packs/mold/components/DocumentFieldsDialog.vue`
- 修改：`web/src/domain-packs/mold/components/DocumentActivity.vue`
- 测试：`tests/test_contract_ocr.py`
- 测试：`tests/test_contract_intake_review.py`
- 测试：`tests/test_document_adaptive_batches.py`

- [ ] **步骤 1：编写失败测试，拒绝伪造来源摘录**

```python
def test_contract_candidate_excerpt_must_exist_in_source_block(extracted_page):
    candidate = field_candidate("contract_number", "HT-001", excerpt="不存在的原文")
    with pytest.raises(DomainError, match="来源"):
        validate_contract_candidate(candidate, extracted_page)
```

- [ ] **步骤 2：运行合同 OCR 定向测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_contract_ocr.py tests/test_contract_intake_review.py tests/test_document_adaptive_batches.py -q
```

- [ ] **步骤 3：完善字段来源校验**

合同号、项目号、订单号、客户模号、金额、币种、签订日期、交付日期、付款节点的候选都必须引用可信页码和文字块。人工修改值保留机器原值、人工值、修改人和修改时间。

- [ ] **步骤 4：完善字段复核工作区**

字段弹窗逐项显示机器值、标准化值、页码、原文摘录和人工值；未确认字段不得进入合同登记 Proposal。

- [ ] **步骤 5：运行定向测试并提交**

```bash
git add backend/domain_packs/mold/erp/commercial/ocr_provider.py backend/domain_packs/mold/erp/commercial/contract_documents.py backend/domain_packs/mold/tools/erp/commercial/document_intake_tools.py backend/domain_packs/mold/tools/erp/commercial/contract_intake_tools.py web/src/domain-packs/mold/components/DocumentFieldsDialog.vue web/src/domain-packs/mold/components/DocumentActivity.vue tests/test_contract_ocr.py tests/test_contract_intake_review.py tests/test_document_adaptive_batches.py
git commit -m "feat: strengthen contract field source review"
```

### 任务 8：完善合同与开工决定、项目、模具的匹配确认

**文件：**

- 修改：`backend/domain_packs/mold/erp/commercial/contract_match_workflow.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/admin_start_notice_tools.py`
- 修改：`backend/domain_packs/mold/erp/commercial/post_start_binding.py`
- 修改：`backend/domain_packs/mold/skills/erp/commercial/sales_contract_intake/SKILL.md`
- 测试：`tests/test_admin_start_contract_match.py`
- 测试：`tests/test_contract_intake_proposal.py`
- 测试：`tests/test_post_start_binding_service.py`
- 测试：`tests/test_post_start_binding_state.py`

- [ ] **步骤 1：编写失败测试，要求匹配只生成候选**

```python
def test_contract_completion_creates_candidate_without_binding(db, reviewed_contract, start_decision):
    candidates = build_contract_match_candidates(db, reviewed_contract)
    assert candidates[0]["start_decision_id"] == start_decision.id
    assert confirmed_binding_count(db, reviewed_contract.id) == 0
```

- [ ] **步骤 2：运行测试确认不存在自动正式绑定**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_admin_start_contract_match.py tests/test_contract_intake_proposal.py tests/test_post_start_binding_service.py tests/test_post_start_binding_state.py -q
```

- [ ] **步骤 3：实现候选评分和人工确认门禁**

评分只允许使用客户、项目编号、订单号、内部模具编号线索、客户模号和已确认承接决定。合同号相似或模号相似不得单独形成自动正式关系。

- [ ] **步骤 4：确认时重新校验版本**

确认必须重新读取合同复核版本、开工决定版本、项目版本和内部模具线索版本；任一变化返回 `VERSION_CONFLICT`。

- [ ] **步骤 5：运行测试并提交**

```bash
git add backend/domain_packs/mold/erp/commercial/contract_match_workflow.py backend/domain_packs/mold/tools/erp/commercial/admin_start_notice_tools.py backend/domain_packs/mold/erp/commercial/post_start_binding.py backend/domain_packs/mold/skills/erp/commercial/sales_contract_intake/SKILL.md tests/test_admin_start_contract_match.py tests/test_contract_intake_proposal.py tests/test_post_start_binding_service.py tests/test_post_start_binding_state.py
git commit -m "feat: confirm contract start bindings safely"
```

### 任务 9：完善合同生效、替代/追加和历史结算一致性

**文件：**

- 修改：`backend/domain_packs/mold/tools/erp/commercial/contract_tools.py`
- 修改：`backend/domain_packs/mold/erp/commercial/contract_relations.py`
- 修改：`backend/domain_packs/mold/erp/commercial/contract_terms.py`
- 修改：`backend/domain_packs/mold/skills/erp/commercial/contract_context_review/SKILL.md`
- 测试：`tests/test_contract_tools.py`
- 测试：`tests/test_finance_context_tools.py`

- [ ] **步骤 1：编写失败测试，覆盖替代和追加的结算边界**

```python
def test_replacement_allocates_each_historical_receipt_once(db, old_contract, new_contract, receipt):
    allocate(db, receipt.id, new_contract.id, "首付款")
    with pytest.raises(DomainError, match="重复"):
        allocate(db, receipt.id, new_contract.id, "尾款")


def test_addition_does_not_absorb_main_contract_receipts(db, main_contract, addition, receipt):
    context = query_contract_context(db, addition.project_id)
    assert context["addition"]["settled_amount"] == "0.00"
```

- [ ] **步骤 2：运行合同和财务定向测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_contract_tools.py tests/test_finance_context_tools.py -q
```

- [ ] **步骤 3：收紧合同版本状态机**

`ORIGINAL` 不带前序；`REPLACEMENT` 必须完整分配前序链历史实收实付；`ADDITION` 必须关联前序但不迁移历史结算。只有 BPM 生效时才能关闭被替代合同。

- [ ] **步骤 4：增加资金时间只读核对证据**

输出节点日期完整性、每日流入/流出、累计净额和首个资金缺口；不从自由文本付款条件推断日期或金额。

- [ ] **步骤 5：运行测试并提交**

```bash
git add backend/domain_packs/mold/tools/erp/commercial/contract_tools.py backend/domain_packs/mold/erp/commercial/contract_relations.py backend/domain_packs/mold/erp/commercial/contract_terms.py backend/domain_packs/mold/skills/erp/commercial/contract_context_review/SKILL.md tests/test_contract_tools.py tests/test_finance_context_tools.py
git commit -m "fix: enforce contract settlement integrity"
```

### 任务 10：完善工程联络文件上传、分类和创建 Proposal

**文件：**

- 修改：`backend/domain_packs/mold/erp/commercial/document_classification.py`
- 修改：`backend/domain_packs/mold/erp/commercial/contract_intake.py`
- 修改：`backend/domain_packs/mold/erp/commercial/document_workflow_api.py`
- 修改：`backend/domain_packs/mold/skills/local/document/document_engineering_contact_intake/SKILL.md`
- 修改：`backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md`
- 修改：`backend/app/agent_resume.py`
- 测试：`tests/test_engineering_contact_document_classification.py`
- 测试：`tests/test_engineering_contact_document_tools.py`
- 测试：`tests/test_engineering_contact_skill_activation.py`
- 测试：`tests/test_docx_document_intake.py`

- [ ] **步骤 1：编写失败测试，固定上传后的唯一续办路径**

```python
def test_confirmed_engineering_contact_activates_create_proposal_once(db, contact_document):
    confirm_document_type(db, contact_document, "ENGINEERING_CONTACT")
    runs = trusted_runs_for_document(db, contact_document.id)
    assert len(runs) == 1
    assert runs[0].active_skill == "document_engineering_contact_intake"
```

- [ ] **步骤 2：运行文档入口定向测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_engineering_contact_document_classification.py tests/test_engineering_contact_document_tools.py tests/test_engineering_contact_skill_activation.py tests/test_docx_document_intake.py -q
```

- [ ] **步骤 3：实现来源和格式边界**

PDF/PNG/JPG 使用文本层或 PaddleOCR；DOCX 使用本机 Word COM 转临时 PDF，再复用同一页面和来源块流程。转换文件仅作为派生证据，不替代原件。

- [ ] **步骤 4：实现创建、附件关联和批量表单续办**

确认类型后，Skill 顺序固定为：`query_document_intake` → `prepare_contact_create` → 本人确认 → `prepare_contact_attach` → 本人确认 → `prepare_contact_form_tasks`。

- [ ] **步骤 5：运行测试并提交**

```bash
git add backend/domain_packs/mold/erp/commercial/document_classification.py backend/domain_packs/mold/erp/commercial/contract_intake.py backend/domain_packs/mold/erp/commercial/document_workflow_api.py backend/domain_packs/mold/skills/local/document/document_engineering_contact_intake/SKILL.md backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md backend/app/agent_resume.py tests/test_engineering_contact_document_classification.py tests/test_engineering_contact_document_tools.py tests/test_engineering_contact_skill_activation.py tests/test_docx_document_intake.py
git commit -m "feat: complete engineering contact document intake"
```

### 任务 11：完善工程联络方案、反馈、复验和关闭门禁

**文件：**

- 修改：`backend/domain_packs/mold/erp/change/contact_lifecycle.py`
- 修改：`backend/domain_packs/mold/erp/change/contacts.py`
- 修改：`backend/domain_packs/mold/tools/erp/change/contact_tools.py`
- 修改：`backend/domain_packs/mold/proposal_handlers.py`
- 修改：`web/src/domain-packs/mold/components/ContactPanel.vue`
- 测试：`tests/test_contact_lifecycle.py`
- 测试：`tests/test_contact_impact.py`
- 测试：`tests/test_engineering_contact_confirmation.py`

- [ ] **步骤 1：编写失败测试，禁止方案审批后直接关闭**

```python
def test_approved_resolution_cannot_close_before_feedback_and_review(db, approved_contact, user):
    with pytest.raises(DomainError, match="复验|反馈"):
        close_contact(db, user, approved_contact.id)


def test_assignee_cannot_review_own_feedback(db, assigned_task, assignee):
    submit_feedback(db, assignee, assigned_task.id)
    with pytest.raises(DomainError, match="独立复验"):
        review_task(db, assignee, assigned_task.id, passed=True)
```

- [ ] **步骤 2：运行生命周期测试确认门禁**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_contact_lifecycle.py tests/test_contact_impact.py tests/test_engineering_contact_confirmation.py -q
```

- [ ] **步骤 3：实现关闭检查清单**

关闭必须同时满足：最新方案有效、全部有效事项已有反馈、每项适用复验已合格、处理人与复验人分离、没有开放影响事项、没有未落实的节点/费用/合同事项。

- [ ] **步骤 4：完善工作台状态展示**

`ContactPanel.vue` 只读展示“待分派、待反馈、待方案审批、待复验、复验不合格、可关闭”及对应阻断原因；关闭仍通过 ProposalCard 确认。

- [ ] **步骤 5：运行测试并提交**

```bash
git add backend/domain_packs/mold/erp/change/contact_lifecycle.py backend/domain_packs/mold/erp/change/contacts.py backend/domain_packs/mold/tools/erp/change/contact_tools.py backend/domain_packs/mold/proposal_handlers.py web/src/domain-packs/mold/components/ContactPanel.vue tests/test_contact_lifecycle.py tests/test_contact_impact.py tests/test_engineering_contact_confirmation.py
git commit -m "fix: enforce engineering contact closure gates"
```

### 任务 12：完善工程联络单对计划、合同和费用的受控影响 Proposal

**文件：**

- 修改：`backend/domain_packs/mold/tools/erp/change/change_intake_tools.py`
- 修改：`backend/domain_packs/mold/erp/change/contact_lifecycle.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/contract_tools.py`
- 修改：`backend/app/plan_tools.py`
- 修改：`backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md`
- 测试：`tests/test_change_intake_tools.py`
- 测试：`tests/test_contact_impact.py`
- 测试：`tests/test_plan_tools.py`
- 测试：`tests/test_contract_tools.py`

- [ ] **步骤 1：编写失败测试，确保影响只生成建议**

```python
def test_contact_impact_prepares_plan_change_without_mutating_plan(db, approved_contact, plan):
    proposal = prepare_contact_plan_change(db, approved_contact.id)
    assert proposal["kind"] == "project_plan_change"
    assert current_plan_version(db, plan.project_id) == plan.version


def test_contact_contract_impact_requires_separate_contract_proposal(db, approved_contact, contract):
    result = query_change_intake_context(db, approved_contact.id)
    assert result["contract_impact"]["requires_confirmation"] is True
    assert contract.amount == Decimal("100000.00")
```

- [ ] **步骤 2：运行跨域定向测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_change_intake_tools.py tests/test_contact_impact.py tests/test_plan_tools.py tests/test_contract_tools.py -q
```

- [ ] **步骤 3：实现跨域 Proposal 映射**

影响动作只允许准备以下既有 Proposal：项目计划变更、合同替代/追加、财务核对事项、采购/制造责任事项。不能从工程联络确认直接改写计划、合同、付款或 ERP 执行结果。

- [ ] **步骤 4：将跨域回执纳入关闭门禁**

每个适用影响保存目标 Proposal/业务回执 ID 和状态。只有已确认且达到各自业务终态的影响项，才可计入工程联络关闭条件。

- [ ] **步骤 5：运行测试并提交**

```bash
git add backend/domain_packs/mold/tools/erp/change/change_intake_tools.py backend/domain_packs/mold/erp/change/contact_lifecycle.py backend/domain_packs/mold/tools/erp/commercial/contract_tools.py backend/app/plan_tools.py backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md tests/test_change_intake_tools.py tests/test_contact_impact.py tests/test_plan_tools.py tests/test_contract_tools.py
git commit -m "feat: connect engineering impacts through proposals"
```

### 任务 13：增加四模块统一只读协调入口

**文件：**

- 创建：`backend/domain_packs/mold/tools/agent/commercial/commercial_change_context.py`
- 创建：`backend/domain_packs/mold/skills/agent/commercial/commercial_change_coordination/SKILL.md`
- 修改：`backend/domain_packs/mold/tool_gateway.py`
- 修改：`backend/domain_packs/mold/harness_policy.py`
- 修改：`web/src/domain-packs/mold/uiText.ts`
- 测试：`tests/test_commercial_change_acceptance.py`
- 测试：`tests/test_harness_explicit_tool_activation.py`

- [ ] **步骤 1：编写失败测试，要求首轮只开放协调查询**

```python
def test_commercial_change_skill_opens_only_coordinator_first(gateway, user):
    tools = activate("报价中标合同设变进度", user=user)
    assert tools == ["query_commercial_change_context"]
```

- [ ] **步骤 2：运行测试确认协调工具未注册**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_commercial_change_acceptance.py tests/test_harness_explicit_tool_activation.py -q
```

- [ ] **步骤 3：实现只读协调投影**

返回四段摘要：报价、中标/承接、合同、工程联络；每段包含 `status`、`facts`、`blockers`、`next_capability` 和来源 ID。协调器只调用现有查询服务，不复制业务规则，不执行写操作。

- [ ] **步骤 4：实现按需展开**

模型根据 `next_capability` 每轮只展开一个阶段 Skill。用户只问进度时不开放任何 `prepare_*`；用户明确办理时才进入对应专用 Skill。

- [ ] **步骤 5：运行测试并提交**

```bash
git add backend/domain_packs/mold/tools/agent/commercial/commercial_change_context.py backend/domain_packs/mold/skills/agent/commercial/commercial_change_coordination/SKILL.md backend/domain_packs/mold/tool_gateway.py backend/domain_packs/mold/harness_policy.py web/src/domain-packs/mold/uiText.ts tests/test_commercial_change_acceptance.py tests/test_harness_explicit_tool_activation.py
git commit -m "feat: add commercial change coordination context"
```

### 任务 14：完成权限、幂等、审计和运行预检门禁

**文件：**

- 修改：`backend/domain_packs/mold/authorization.py`
- 修改：`backend/domain_packs/mold/tool_gateway.py`
- 修改：`backend/domain_packs/mold/notification_policy.py`
- 修改：`scripts/check_runtime.py`
- 修改：`tests/test_local_scope_guard.py`
- 修改：`tests/test_governance_context_tools.py`
- 修改：`tests/test_runtime_preflight.py`
- 修改：`tests/test_commercial_change_acceptance.py`

- [ ] **步骤 1：编写失败测试，覆盖四模块权限撤销和重复确认**

```python
@pytest.mark.parametrize("action", [
    "quotation.execute", "admin_start_notice.execute", "contract.execute", "contact.execute",
])
def test_security_version_change_invalidates_prepared_intent(db, user, prepared_intent, action):
    revoke_business_permission(db, user)
    with pytest.raises(DomainError, match="授权已变化"):
        confirm_intent(db, user, action, prepared_intent)


def test_duplicate_confirmation_returns_same_receipt_without_duplicate_rows(db, intent):
    first = confirm(intent)
    second = confirm(intent)
    assert first == second
```

- [ ] **步骤 2：运行安全和预检测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_local_scope_guard.py tests/test_governance_context_tools.py tests/test_runtime_preflight.py tests/test_commercial_change_acceptance.py -q
```

- [ ] **步骤 3：补齐统一门禁**

四类 Proposal 均校验当前用户、会话、Run、资源版本、授权指纹、文件所有权和业务范围。通知投递和读取再次校验业务权限；通知 payload 不包含合同正文、OCR 原文或工程联络附件内容。

- [ ] **步骤 4：更新只读启动预检**

预检检查四模块必需表和列；缺少工程联络本地设变表时只隐藏对应 Tool 并返回明确原因，不自动迁移、不盖章、不建表。

- [ ] **步骤 5：运行测试并提交**

```bash
git add backend/domain_packs/mold/authorization.py backend/domain_packs/mold/tool_gateway.py backend/domain_packs/mold/notification_policy.py scripts/check_runtime.py tests/test_local_scope_guard.py tests/test_governance_context_tools.py tests/test_runtime_preflight.py tests/test_commercial_change_acceptance.py
git commit -m "test: enforce commercial change runtime guards"
```

### 任务 15：完成真实工作台纵向验收和文档追溯

**文件：**

- 修改：`docs/DEVELOPMENT_STATUS.md`
- 修改：`docs/REQUIREMENTS_TRACEABILITY.md`
- 修改：`docs/ENGINEERING_CONTACT_COLLABORATION.md`
- 修改：`README.md`
- 保留：`tests/test_commercial_change_acceptance.py`
- 临时：`.local/acceptance/quote_bid_contract_contact/*`

- [ ] **步骤 1：运行四模块定向回归**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_quotation_tools.py tests/test_quote_evaluation_tools.py tests/test_bid_event_identity.py tests/test_bid_start_consumer.py tests/test_admin_start_candidates.py tests/test_admin_start_contract_match.py tests/test_contract_ocr.py tests/test_contract_intake_review.py tests/test_contract_tools.py tests/test_engineering_contact_document_classification.py tests/test_engineering_contact_task_proposals.py tests/test_engineering_contact_task_batch_confirm.py tests/test_contact_lifecycle.py tests/test_contact_impact.py tests/test_commercial_change_acceptance.py -q
```

预期：全部通过；输出中无 ERROR、FAILED 或数据库连接指向正式远端地址。

- [ ] **步骤 2：准备脱敏验收材料**

材料固定包含：

1. 一份报价资料包；
2. 一份中标通知；
3. 一份文本型销售合同；
4. 一份扫描型销售合同；
5. 一份图二样式工程联络单；
6. 一份包含两个责任部门的工程联络事项表。

每份材料记录 SHA-256、页数、预期文档类型、预期字段和预期业务关联。

- [ ] **步骤 3：由潘总在当前工作台完成真实人工验收**

按以下顺序操作：

```text
上传报价资料 → 填写报价表单 → 确认 Proposal → 审批生效
上传中标通知 → 确认类型 → 确认承接 → 选择项目和内部模具编号
上传合同 → 复核字段来源 → 确认合同匹配 → 提交审批
上传工程联络单 → 确认创建 → 关联附件 → 批量填写责任事项
处理人反馈 → 独立复验 → 落实跨域影响 → 确认关闭
```

- [ ] **步骤 4：后台核验权威结果**

逐项核对数据库中只有一条有效业务事实、版本连续、附件哈希一致、HumanIntent 已处理、审计和 Outbox 完整、无重复任务、无自动绑定和无越权读取。

- [ ] **步骤 5：清理临时验收产物**

删除 `.local/acceptance/quote_bid_contract_contact/` 下的脱敏临时文件和日志；保留正式测试源码。不得删除审计、正式业务记录和失败复现记录。

- [ ] **步骤 6：更新需求追踪状态**

只将具备“代码实现 + 自动化测试 + 工作台人工操作 + 后台权威核验”四类证据的 FR 更新为已验证；其余保持原状态。文档记录执行人、日期、材料哈希和结果。

- [ ] **步骤 7：检查差异并提交**

```bash
git diff --check
git add docs/DEVELOPMENT_STATUS.md docs/REQUIREMENTS_TRACEABILITY.md docs/ENGINEERING_CONTACT_COLLABORATION.md README.md tests/test_commercial_change_acceptance.py
git commit -m "docs: record commercial change acceptance evidence"
```

---

# 四、阶段门禁与里程碑

| 里程碑 | 覆盖任务 | 进入条件 | 完成证据 |
|---|---:|---|---|
| M1 工程联络现有增量收口 | 1～2 | 当前工作区差异已核对 | 批量事项测试通过、统一验收夹具可用 |
| M2 报价闭环 | 3～4 | M1 完成 | 上传资料、人工表单、版本审批、反馈和承接引用可重复执行 |
| M3 中标到开工草稿闭环 | 5～6 | M2 有有效报价样本 | 中标确认事件幂等、承接前无项目、承接后选择项目 |
| M4 合同闭环 | 7～9 | M3 有已确认承接决定 | OCR 来源可核对、人工匹配、合同版本和历史结算一致 |
| M5 工程联络闭环 | 10～12 | M4 有有效项目和合同样本 | 上传、创建、批量事项、方案、反馈、复验、跨域影响和关闭完整 |
| M6 统一协调与验收 | 13～15 | M2～M5 完成 | 协调 Skill、权限门禁、定向回归和工作台人工验收证据齐备 |

# 五、验收矩阵

| 场景 | 验收动作 | 通过条件 |
|---|---|---|
| 报价首版 | 上传资料并提交报价 | 确认前零写入；审批后形成唯一有效 V1 |
| 报价改版 | 基于 V1 提交 V2 | 版本连续；V1 保留；对比字段正确 |
| 客户反馈 | 登记同一来源两次 | 第二次阻断；不自动承接 |
| 中标确认 | 同一文件重复发布确认事件 | 只生成一个管理员草稿 |
| 承接顺序 | 承接前不选择项目 | 可确认承接；承接后才开放项目选择 |
| 多候选 | 同模号命中多个项目 | 只展示候选，不自动合并 |
| 合同 OCR | 修改一个识别字段 | 保留机器值、人工值和原文来源 |
| 合同匹配 | 合同编号相似但项目不一致 | 不自动绑定，转人工处理 |
| 替代合同 | 替代含历史回款的合同 | 历史回款只归集一次，旧合同历史保留 |
| 追加合同 | 追加合同关联主合同 | 两份合同独立有效，主合同回款不被吸收 |
| 联络单上传 | 确认 ENGINEERING_CONTACT | 唯一 Skill Run，创建 Proposal 不直接写正式单据 |
| 批量事项 | 两部门两责任人 | 确认前零任务，确认后一次创建两条 ASSIGNED |
| 方案审批 | 方案审批完成但未反馈 | 联络单仍不可关闭 |
| 独立复验 | 处理人尝试复验自己的反馈 | 明确阻断 |
| 跨域影响 | 设变影响合同和计划 | 只生成两个独立 Proposal，不直接改业务事实 |
| 权限撤销 | Proposal 后撤销权限 | 旧 HumanIntent 失效，无越权写入 |
| 重复确认 | 重复提交同一 HumanIntent | 返回同一权威回执，不重复建单 |

# 六、回滚与异常恢复

- 报价：审批未生效时保留草稿或退回记录；不覆盖上一有效版本。
- 中标：重复事件按稳定事件键幂等；失败事件保留可重试状态，不重复创建草稿。
- 合同：OCR 失败只重试失败批次；已完成页面缓存复用；人工确认版本变化时重新复核。
- 工程联络：批量事项任一校验失败时事务整体回滚；已反馈事项不能撤销删除，只能追加更正和复验。
- HumanIntent：版本或授权变化后失效，用户重新查询并生成新 Proposal。
- 通知：Outbox/Inbox 按事件和用户去重；投递失败不改变业务主事务的权威状态。
- 数据库：本计划不在正式库执行迁移。代码发布前由 `scripts/check_runtime.py` 只读检查结构，缺少结构时阻止或隐藏对应能力。

# 七、交付物

1. 报价会话内结构化表单和版本对比。
2. 中标确认事件到管理员开工草稿的唯一幂等链路。
3. 承接前无项目、承接后选择项目和内部模具编号的正确流程。
4. 合同字段来源复核、人工修订和安全匹配。
5. 合同替代/追加及历史结算一致性门禁。
6. 图二工程联络单上传、创建、批量责任事项、方案、反馈、复验和关闭闭环。
7. 工程联络对计划、合同和费用的独立 Proposal 联动。
8. 四模块统一只读协调 Skill/Tool。
9. 权限、幂等、审计、通知和只读启动预检。
10. 正式自动化回归测试和真实工作台验收证据。

# 八、执行纪律

- 严格按任务 1～15 顺序实施；每个任务通过定向测试后再进入下一任务。
- 每个任务一个独立 commit，禁止把多个里程碑混在同一提交中。
- 发现现有实现与本计划冲突时，先更新本计划并说明影响，再修改代码。
- 任何真实数据库结构变化、正式库写入或 ERP 联调必须单独获得潘总批准。
- 前端源码完成后由潘总自行执行前端构建和页面验收；实施者不得运行前端构建或内置浏览器。
