# 工程变更申请联络单办理事项 Proposal 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。本项目禁止使用子代理；本计划在当前工作区内执行，不创建 Git worktree。

**目标：** 在工程联络单附件关联确认后，由 Skill 自动生成批量办理事项 Proposal，并自动弹出覆盖图片字段的人工选择弹窗，确认后通过 Tool 批量创建相关单位办理事项。

**架构：** 复用现有工程联络 `ContactCase`、`ContactTask`、Proposal、HumanIntent 和 Agent Run 续办机制。新增 `prepare_contact_form_tasks` 作为表单批量事项 Tool；前端只负责填写和提交受控 Proposal 修订，不直接调用业务写入 API。完整纸质表单以不可变 `ContactRecord.detail`/审计快照保存，不执行数据库迁移。

**技术栈：** FastAPI、SQLAlchemy、Pydantic、Vue 3、TypeScript、现有 Agent Tool Gateway、HumanIntent/Proposal 确认链、pytest。

**规格：** `docs/superpowers/specs/2026-09-27-engineering-contact-task-form-proposal-design.md`

## 全局约束

- 工程联络单已存在时不得再次创建 `ContactCase`。
- 附件关联确认后只自动准备 `prepare_contact_form_tasks` Proposal；本人确认前不得创建 `ContactTask`。
- `HISTORY` 模式不得创建线上办理事项。
- 完整表单字段、来源文件版本、Proposal 哈希和人工修改值必须可追溯。
- 完成类型使用 `NORMAL`/`URGENT`/`CRITICAL`，显示为“一般/急件/特急件”。
- 变更类别使用图片中的多选项：客户变更、设计异常、组立异常、加工异常、外协不良、降低成本、制程改善、其它。
- 部门和具体责任人必须来自当前本地数据库及权限校验，不能由模型生成或静默绑定。
- OCR 候选只作为初始值，不能替代人工确认；签名和审批结果不能由 OCR 或模型伪造。
- 不调用 ERP HTTP、MCP、ERP 登录令牌、远程数据库或远程文件目录。
- 不执行数据库迁移；不得改动当前工作区已有的三个未提交源文件和 `NUL` 文件。
- 不运行前端构建、不使用浏览器测试；仅运行必要的 pytest、Python 编译检查、`git diff --check` 和静态文本契约检查。
- 每个正式写入仍必须通过 Skill → Tool Proposal → HumanIntent → 本人确认。

## 文件清单与职责

### 修改

- `backend/domain_packs/mold/erp/change/contacts.py`
  - 增加表单批量事项输入模型、候选人员校验、批量创建事务和表单快照记录。
- `backend/domain_packs/mold/tools/erp/change/contact_tools.py`
  - 注册 `form_tasks` Tool 输入、预览、Proposal 修订、候选选项和确认执行。
- `backend/domain_packs/mold/proposal_handlers.py`
  - 将 `prepare_contact_form_tasks` 加入 `contact.execute` Proposal handler 白名单。
- `backend/domain_packs/mold/tool_gateway.py`
  - 注册 Tool 描述、参数 Schema 和文档工程联络 Skill 的可选工具。
- `backend/app/agent_resume.py`
  - 把 `prepare_contact_attach` 后续动作改为 `prepare_contact_form_tasks`。
- `backend/domain_packs/mold/skills/local/document/document_engineering_contact_intake/SKILL.md`
  - 明确附件关联确认后自动生成批量表单事项 Proposal。
- `backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md`
  - 明确批量事项、线上/历史模式、单位和人员选择门禁。
- `backend/domain_packs/mold/erp/change/contact_lifecycle.py`
  - 在联络单详情中读取并展示完整表单快照摘要，保持既有方案/反馈/复验门禁。
- `web/src/App.vue`
  - 识别待处理批量表单 Proposal，自动挂载弹窗并处理确认、取消和刷新。
- `web/src/domain-packs/mold/uiText.ts`
  - 增加图片字段、枚举和错误文案映射。

### 创建

- `web/src/domain-packs/mold/components/EngineeringContactTaskDialog.vue`
  - 工程变更申请联络单办理事项大尺寸弹窗。
- `tests/test_engineering_contact_task_proposals.py`
  - 输入契约、预览、候选、修订和确认门禁测试。
- `tests/test_engineering_contact_task_batch_confirm.py`
  - 批量创建、事务回滚、幂等和表单快照测试。
- `tests/test_engineering_contact_task_skill_continuation.py`
  - 附件确认后 Skill/Run 续办动作测试。

### 不修改

- `backend/agent_core/migration_runtime.py`
- `backend/domain_packs/mold/erp/core/business_matching.py`
- `scripts/check_runtime.py`
- `NUL`
- 现有数据库迁移文件和正式数据库结构。
- 普通 `prepare_contact_task`、反馈、方案、复验和关闭的既有语义。

---

### 任务 1：建立批量表单事项输入契约和失败测试

**文件：**
- 创建：`tests/test_engineering_contact_task_proposals.py`
- 修改：`backend/domain_packs/mold/erp/change/contacts.py`
- 参考：`backend/domain_packs/mold/erp/change/contacts.py` 中的 `TaskInput`、`replay`、`append`、`add_task`

- [ ] **步骤 1：编写输入契约失败测试**

在新测试文件中建立测试辅助方法，复用 `tests/test_contacts.py` 的 `create`、`operation`、`grant` 和 `tests/test_bpm_assignments.py` 的 `group`。先写以下测试：

```python

def form_payload(case, department_id, assignee_id=None, **overrides):
    return {
        "case_id": case["id"],
        "revision": case["revision"],
        "form": {
            "responsible_department_id": department_id,
            "application_date": "2026-09-27",
            "completion_date": "2026-10-02",
            "completion_type": "URGENT",
            "change_categories": ["DESIGN_ISSUE", "PROCESS_IMPROVEMENT"],
            "change_description": "核对工程变更内容",
            "countermeasure": "完成图纸、加工和质量复核",
            "related_units": [{
                "department_id": department_id,
                "assignee_id": assignee_id,
                "completion_date": "2026-09-29",
                "work_content": "完成图纸核对",
                "hours": "4.00",
                "amount": "0.00",
                "currency": "CNY",
                "remark": "保留复核记录",
            }],
            "pricing_note": "内部工艺评估",
            "total_amount": "0.00",
            "currency": "CNY",
            **overrides,
        },
    }


def test_form_task_input_requires_categories_units_and_valid_completion_type(...):
    ...
```

测试必须覆盖：自动触发时 `form=None` 可以生成空表单 Proposal；最终确认时 `form=None` 被拒绝；空变更类别、空相关单位、错误完成类型、未来申请日期、金额无币种、工时为负数、相关单位没有作业内容、相关单位没有完成日期。

- [ ] **步骤 2：运行测试确认当前失败**

运行：

```bash
cd mold-agent
pytest -q tests/test_engineering_contact_task_proposals.py -k 'input or completion or categories'
```

预期：FAIL，原因是 `FormTaskBatchInput`、`EngineeringContactFormInput` 和相关校验尚未存在。

- [ ] **步骤 3：实现最小 Pydantic 输入模型**

在 `contacts.py` 的 `TaskInput` 后新增以下明确类型：

```python
class ContactUnitInput(StrictModel):
    department_id: str = Field(min_length=1, max_length=36)
    assignee_id: str = Field(min_length=1, max_length=36)
    completion_date: date
    work_content: str = Field(min_length=1, max_length=4000)
    hours: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    remark: str = Field(default="", max_length=1000)


class EngineeringContactFormInput(StrictModel):
    customer_ref: str = Field(min_length=1, max_length=200)
    customer_name: str = Field(min_length=1, max_length=200)
    product_name: str = Field(min_length=1, max_length=200)
    mold_number: str = Field(min_length=1, max_length=100)
    product_ref: str = Field(min_length=1, max_length=200)
    responsible_department_id: str = Field(min_length=1, max_length=36)
    application_date: date
    completion_date: date
    completion_type: Literal["NORMAL", "URGENT", "CRITICAL"]
    change_categories: list[Literal[
        "CUSTOMER_CHANGE", "DESIGN_ISSUE", "ASSEMBLY_ISSUE", "MACHINING_ISSUE",
        "OUTSOURCE_DEFECT", "COST_REDUCTION", "PROCESS_IMPROVEMENT", "OTHER",
    ]] = Field(min_length=1, max_length=8)
    change_description: str = Field(min_length=1, max_length=10000)
    countermeasure: str = Field(min_length=1, max_length=10000)
    related_units: list[ContactUnitInput] = Field(min_length=1, max_length=30)
    pricing_note: str = Field(default="", max_length=2000)
    total_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class FormTaskBatchInput(Mutation):
    # 自动触发阶段允许为空；弹窗重新准备时必须提供完整表单。
    form: EngineeringContactFormInput | None = None
```

增加字段非空、申请日期不晚于当前日期、完成日期不早于申请日期、单位日期不晚于总完成日期、类别去重和金额/币种一致性校验。`assignee_id` 首版设为必填，符合“供人选择具体责任人”的要求；没有合格人员时由候选接口阻断提交。

- [ ] **步骤 4：运行输入测试确认通过**

运行：

```bash
pytest -q tests/test_engineering_contact_task_proposals.py -k 'input or completion or categories'
```

预期：PASS。

- [ ] **步骤 5：提交本任务**

```bash
git add tests/test_engineering_contact_task_proposals.py backend/domain_packs/mold/erp/change/contacts.py
git commit -m "feat: add engineering contact form task schema"
```

---

### 任务 2：实现批量办理事项服务和表单快照

**文件：**
- 修改：`backend/domain_packs/mold/erp/change/contacts.py`
- 创建：`tests/test_engineering_contact_task_batch_confirm.py`
- 参考：`contacts.py` 的 `department`、`assignee_eligible`、`replay`、`append`、`add_task`

- [ ] **步骤 1：编写未确认不写入和确认批量写入测试**

测试必须直接调用现有 Proposal 流程或领域服务，验证：

```python

def test_form_task_confirmation_creates_all_units_and_snapshot(client, data, monkeypatch):
    # 1. 创建 ONLINE ContactCase
    # 2. 准备包含两个相关单位的 form_tasks Proposal
    # 3. 确认前 ContactTask 数量为 0
    # 4. 本人确认后 ContactTask 数量为 2
    # 5. 每个 task 的 department、assignee、title、amount 与表单一致
    # 6. ContactRecord 或审计快照包含 form_version、change_categories、related_units、pricing
    ...
```

另写：

- 第二个单位人员失去权限时整个批量操作回滚，不能创建第一个单位后半途失败；
- Case 为 `HISTORY` 时返回 `HISTORY_NO_DISPATCH`；
- Case revision 变化时返回 `VERSION_CONFLICT`；
- 关闭 Case 时返回 `CONTACT_CLOSED`；
- 同一 `request_key` 重复确认只返回原结果，不增加任务；
- 同一 `request_key` 使用不同表单返回 `IDEMPOTENCY_CONFLICT`。

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
pytest -q tests/test_engineering_contact_task_batch_confirm.py
```

预期：FAIL，原因是 `add_form_tasks` 和表单快照逻辑尚未存在。

- [ ] **步骤 3：实现批量服务**

在 `contacts.py` 新增 `form_task_preview`、`add_form_tasks` 和快照辅助函数：

```python
FORM_SNAPSHOT_VERSION = "engineering-change-contact-v1"


def add_form_tasks(cid: str, data: FormTaskBatchInput, user, db):
    if data.form is None:
        raise DomainError("FORM_INCOMPLETE", "请先补齐工程变更申请联络单表单", 409)
    case = load(db, user, cid, True)
    require(db, user, "coordinate", case)
    if case.mode != "ONLINE":
        raise DomainError("HISTORY_NO_DISPATCH", "历史补录不能派发线上任务", 409)
    digest, done = replay(db, user, case, data, "FORM_TASKS_CREATED")
    if done:
        return serialize(db, case, True, user)

    primary = department(db, data.form.responsible_department_id)
    if not data.form.related_units:
        raise DomainError("TASK_REQUIRED", "至少选择一个相关单位", 409)

    rows = []
    for unit in data.form.related_units:
        group = department(db, unit.department_id)
        person = db.get(m.User, unit.assignee_id)
        if not assignee_eligible(db, person, group, case):
            raise DomainError("ASSIGNEE_UNAVAILABLE", "存在无权或已停用的处理人", 403)
        rows.append((group, person, unit))

    snapshot = form_snapshot(case, data.form, rows, digest)
    tasks = []
    for group, person, unit in rows:
        task = m.ContactTask(
            case_id=case.id,
            department_id=group.id,
            title=unit.work_content,
            created_by=user.id,
            assignee_id=person.id,
            status="ASSIGNED",
            affected_type="OTHER",
            affected_ref=f"engineering-contact-form:{case.id}",
            impact_description=unit.remark or data.form.change_description,
            planned_action="CONTINUE",
            delivery_impact_days=max(0, (unit.completion_date - data.form.application_date).days),
            estimated_amount=unit.amount,
            currency=unit.currency,
            source_system="MANUAL",
            source_ref="ENGINEERING_CONTACT_FORM",
            source_as_of=now(),
        )
        db.add(task)
        tasks.append((task, group, person, unit))
    db.flush()
    return append(db, user, case, data, "FORM_TASKS_CREATED", digest,
                  {"form_snapshot": snapshot, "task_ids": [task.id for task, *_ in tasks]},
                  recipients=[person.id for _, _, person, _ in tasks])
```

实现要求：

- 在全部部门、人员和金额校验完成前不得 `db.add` 任何任务，保证批量事务原子性；
- `task.title` 使用作业内容，表单原文完整内容只放入快照；
- `status` 为 `ASSIGNED`，因为弹窗已选择具体责任人；
- 使用现有通知/审计 `append` 机制；
- 表单快照中保存 `FORM_SNAPSHOT_VERSION`、Case revision、相关单位完整行、图片字段、来源文件引用（由 Tool 传入安全来源摘要）和 Proposal 哈希；不得保存完整 PDF 正文、Token 或提示词。

- [ ] **步骤 4：实现只读表单快照展示**

在 `serialize(..., details=True, user=...)` 中增加：

```python
result["engineering_contact_form"] = latest_form_snapshot(db, c, user)
```

只返回当前用户有权限读取的快照；不把快照中的处理方案或隐藏字段暴露给无权限人员。`ContactPanel.vue` 本任务暂不改布局，只保证 API 数据可被后续弹窗和工作区读取。

- [ ] **步骤 5：运行批量服务测试**

运行：

```bash
pytest -q tests/test_engineering_contact_task_batch_confirm.py
```

预期：PASS。

- [ ] **步骤 6：提交本任务**

```bash
git add tests/test_engineering_contact_task_batch_confirm.py backend/domain_packs/mold/erp/change/contacts.py
git commit -m "feat: create engineering contact tasks in batch"
```

---

### 任务 3：注册 Tool、Proposal 预览、候选和表单修订确认

**文件：**
- 修改：`backend/domain_packs/mold/tools/erp/change/contact_tools.py`
- 修改：`backend/domain_packs/mold/proposal_handlers.py`
- 修改：`backend/domain_packs/mold/tool_gateway.py`
- 修改：`backend/domain_packs/mold/manifest.py`（仅在现有路由注册需要时）
- 修改：`tests/test_engineering_contact_task_proposals.py`

- [ ] **步骤 1：编写 Tool Proposal 失败测试**

测试以下行为：

```python

def test_prepare_form_tasks_returns_inert_proposal_and_does_not_write(client, data, monkeypatch):
    # 调用 internal run Tool: prepare_contact_form_tasks
    # 断言 proposal.action == "form_tasks"
    # 断言 proposal.confirmation_policy.requires_human_confirmation is True
    # 断言 ContactTask 数量仍为 0
    ...


def test_form_options_return_only_authorized_departments_and_people(...):
    # 取消某部门或某人员 contact.read/respond 权限
    # GET form-options 后断言其不在结果中
    ...
```

还要覆盖：Tool 未登记参数被拒绝、非当前 Run 的 Case 被拒绝、旧 revision 被拒绝、HISTORY Case 被拒绝、没有可选责任人时返回明确候选错误。

- [ ] **步骤 2：运行失败测试**

运行：

```bash
pytest -q tests/test_engineering_contact_task_proposals.py -k 'proposal or options or authorization'
```

预期：FAIL，原因是 `form_tasks` 未登记、候选路由不存在。

- [ ] **步骤 3：加入 Tool 输入和 SPECS**

在 `contact_tools.py` 中加入：

```python
class FormIntentInput(StrictModel):
    input: c.FormTaskBatchInput


def require_complete_form(data: c.FormTaskBatchInput):
    if data.form is None:
        raise DomainError("FORM_INCOMPLETE", "请先在工程变更申请联络单弹窗中补齐字段", 409)
    return data.form


SPECS.update({
    "form_tasks": (c.FormTaskBatchInput, "coordinate", "建立工程变更联络办理事项"),
})
```

扩展 `schema()`、`parse()`、`preview()` 和 `execute_tool()`：

- `form_tasks` 只允许当前 Case 发起人办理；
- `form=None` 只允许自动触发表单草稿，preview 返回 `form_status="NEEDS_HUMAN_SELECTION"`；
- preview 展示图片字段的中文标签、相关单位明细、责任人、完成日期、工时、金额、计价和“本人确认后才创建事项”；
- `form=None` 不得进入 HumanIntent 确认执行；
- 返回 Proposal 的 `kind="contact"`、`action="form_tasks"`；
- 完整输入由 `require_complete_form()` 和 `FormTaskBatchInput` 再次严格校验。

- [ ] **步骤 4：加入候选接口**

在 `contact_tools.py` 增加：

```python
@router.get("/api/contact-proposals/{step_id}/form-options")
def form_options(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    proposal = source(db, user, step_id)
    if proposal.get("action") != "form_tasks":
        raise DomainError("TOOL_FORBIDDEN", "当前 Proposal 不是工程变更事项表单", 403)
    return form_candidate_options(db, user, proposal)
```

`form_candidate_options()` 必须：

- 重新校验 Step、Run、用户、权限和 Case revision；
- 返回当前有权限的启用部门；
- 每个部门只返回同时具备成员资格、`contact.read` 和 `contact.respond` 的有效人员；
- 返回完成类型、变更类别和图片单位的静态选项；
- 不返回隐藏项目、无权限人员或完整附件正文。

- [ ] **步骤 5：加入受控 Proposal 修订接口**

新增：

```python
@router.post("/api/contact-proposals/{step_id}/form-intent")
def form_intent(step_id: str, data: FormIntentInput, ...):
    proposal = source(db, user, step_id)
    revised = revise_form_proposal(db, user, step_id, proposal, data.input)
    payload = {
        "step_id": step_id,
        "proposal_hash": content_hash(revised),
        "proposal_override": revised,
    }
    result = create_intent(db, user, "contact.execute", step_id, payload)
    result["display"] = revised["display"]
    result["confirmation_policy"] = revised["confirmation_policy"]
    db.commit()
    return result
```

`revise_form_proposal()` 只能允许修改 `form_tasks` 的表单输入，不能修改 `case_id`、用户身份、Run、工具名、原始附件 ID 或权限范围；它要重新调用 `parse`、`preview` 并产生新的内容哈希。

扩展 `source()`/`validate_intent()`：

- 只有由后端创建的 `proposal_override` 才能参与确认；
- `proposal_override` 的哈希和当前 Case revision 必须重新核对；
- 确认后调用 `c.add_form_tasks`，不调用直接 `/api/contacts` 写入接口。

- [ ] **步骤 6：登记 Proposal handler 和 Tool Gateway**

在 `proposal_handlers.py` 的 `contact.execute` 白名单增加：

```python
"prepare_contact_form_tasks",
```

在 `tool_gateway.py`：

- `TOOLS` 增加 `prepare_contact_form_tasks` 描述；
- `contact_collaboration_review` 的 `optional_tools` 增加该 Tool；
- `document_engineering_contact_intake` 的 `optional_tools` 增加该 Tool；
- `activation_tools` 保持查询工具和现有创建/附件门禁，不授予直接写库能力；
- Tool 类型标记为 operation；
- 任何 ToolSearch 结果都必须指向真实 `prepare_contact_form_tasks` 名称。

- [ ] **步骤 7：运行 Tool 和 Proposal 测试**

运行：

```bash
pytest -q tests/test_engineering_contact_task_proposals.py
pytest -q tests/test_contact_proposals.py -k 'proposal or task'
pytest -q tests/test_local_capability_registry.py tests/test_local_change_contact_orchestration.py
```

预期：PASS，且既有 `prepare_contact_task` 测试不回归。

- [ ] **步骤 8：提交本任务**

```bash
git add backend/domain_packs/mold/tools/erp/change/contact_tools.py backend/domain_packs/mold/proposal_handlers.py backend/domain_packs/mold/tool_gateway.py backend/domain_packs/mold/manifest.py tests/test_engineering_contact_task_proposals.py
git commit -m "feat: add engineering contact form task proposal"
```

---

### 任务 4：把附件关联后的 Skill 续办切换到批量表单 Proposal

**文件：**
- 修改：`backend/app/agent_resume.py`
- 修改：`backend/domain_packs/mold/skills/local/document/document_engineering_contact_intake/SKILL.md`
- 修改：`backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md`
- 创建：`tests/test_engineering_contact_task_skill_continuation.py`

- [ ] **步骤 1：编写续办失败测试**

测试确认 `prepare_contact_attach` 后的恢复指令明确要求：

```python

def test_attach_confirmation_continues_with_form_tasks_tool(...):
    # 构造已生成 prepare_contact_attach 的 Run/Step
    # 调用 queue_after_proposal_decision(..., "approved", receipt)
    # 断言 checkpoint 中 continuation == "form_tasks"
    # 断言生成的下一阶段指令包含 prepare_contact_form_tasks
    # 断言不再要求直接调用 prepare_contact_task
    ...
```

同时测试 `HISTORY` 分支文字包含“只补录线下过程，不创建线上责任事项”。

- [ ] **步骤 2：运行失败测试**

运行：

```bash
pytest -q tests/test_engineering_contact_task_skill_continuation.py
```

预期：FAIL，当前续办映射仍指向 `task`。

- [ ] **步骤 3：修改 Agent Run 续办映射**

把：

```python
_CONTACT_DOCUMENT_CONTINUATIONS = {
    "prepare_contact_create": "attach",
    "prepare_contact_attach": "task",
}
```

改为：

```python
_CONTACT_DOCUMENT_CONTINUATIONS = {
    "prepare_contact_create": "attach",
    "prepare_contact_attach": "form_tasks",
}
```

同时将附件确认后的系统指令改为：

```text
原始附件已关联；现在继续工程联络文档 Skill：查询联络单当前版本，若办理模式为 ONLINE，仅准备 prepare_contact_form_tasks Proposal。该 Proposal 由弹窗供本人选择责任部门、责任人、完成日期、完成类型、变更类别和工艺评估；不得直接调用业务写入接口。HISTORY 模式只允许补录线下事实。
```

- [ ] **步骤 4：修改两个 Skill 文档**

在文档入口 Skill 中把步骤 6 改成：

```text
附件关联确认后，ONLINE 使用 prepare_contact_form_tasks 生成完整工程变更申请联络单办理事项 Proposal；HISTORY 不生成线上责任事项。
```

在协作 Skill 中明确：

- 文档来源的 Case 已存在时不再次调用 `prepare_contact_create`；
- 批量表单 Tool 只准备 Proposal；
- 单事项 `prepare_contact_task` 仅保留给普通会话追加事项；
- 完成类型、变更类别、责任单位、责任人和工艺评估必须由本人确认。

- [ ] **步骤 5：运行续办测试**

运行：

```bash
pytest -q tests/test_engineering_contact_task_skill_continuation.py
```

预期：PASS。

- [ ] **步骤 6：提交本任务**

```bash
git add backend/app/agent_resume.py backend/domain_packs/mold/skills/local/document/document_engineering_contact_intake/SKILL.md backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md tests/test_engineering_contact_task_skill_continuation.py
git commit -m "feat: continue engineering contact with form task proposal"
```

---

### 任务 5：实现工程变更事项选择弹窗

**文件：**
- 创建：`web/src/domain-packs/mold/components/EngineeringContactTaskDialog.vue`
- 修改：`web/src/App.vue`
- 修改：`web/src/domain-packs/mold/uiText.ts`
- 不运行前端构建

- [ ] **步骤 1：定义弹窗数据和交互纯函数测试**

在现有前端测试约定下，先增加不依赖浏览器的纯函数/文本契约测试，至少验证：

- 完成类型映射为“一般/急件/特急件”；
- 变更类别包含图片八项；
- 表单提交 payload 包含 `case_id`、`revision`、`form.related_units` 和 `form.change_categories`；
- 没有相关单位、责任人或完成日期时不能提交；
- `HISTORY` 状态不显示确认创建按钮。

测试使用项目现有 TypeScript 测试工具，不启动浏览器、不运行构建。

- [ ] **步骤 2：建立弹窗组件骨架**

新建组件，props 和 emits 固定为：

```ts
type Props = {
  stepId: string
  proposal: any
  archived?: boolean
}

type Emits = {
  confirmed: []
  dismissed: []
  error: [message: string]
}
```

组件首次挂载自动打开 Teleport modal；关闭后保留一个“继续选择并确认”按钮。弹窗必须具备：

- `role="dialog"`、`aria-modal="true"`；
- Escape 关闭；
- 焦点回收和 Tab 焦点循环；
- 页面滚动锁定；
- `max-height` 和内部滚动，适配当前工作台窄屏。

- [ ] **步骤 3：实现候选加载和默认值**

挂载时调用：

```ts
api(`/contact-proposals/${encodeURIComponent(props.stepId)}/form-options`)
```

默认值来源顺序：

1. Proposal 的 `input.form`；
2. Proposal `display` 中的识别候选；
3. 空值并显示“请选择/请填写”。

部门改变时清空不再属于该部门的责任人；候选接口失败时保留弹窗并显示错误，禁止确认。

- [ ] **步骤 4：实现图片字段分区**

组件必须包含以下真实字段控件：

1. 基本信息：客户、责任部门、申请日期、完成日期、品名、模具编号、产品料号；
2. 完成类型单选：一般、急件、特急件；
3. 变更类别多选：客户变更、设计异常、组立异常、加工异常、外协不良、降低成本、制程改善、其它；
4. 变更说明、对策多行文本；
5. 相关单位多行表格：单位、责任人、完成时间、作业内容、工时、金额、备注；
6. 计价说明、金额合计、币种；
7. 申请/审核/批准为只读流程提示，不显示模型签名。

支持新增/删除单位行，至少保留一行；表单校验错误显示在对应区域，不用 Toast 替代字段错误。

- [ ] **步骤 5：实现“重新准备 Proposal”与本人确认**

提交表单时调用：

```ts
post(`/contact-proposals/${encodeURIComponent(props.stepId)}/form-intent`, {
  input: formPayload,
})
```

收到新的 HumanIntent 后，在同一弹窗内显示最终 Proposal 展示内容和确认策略。点击确认只调用：

```ts
post(`/human-actions/${intent.id}/confirm`, {challenge: intent.challenge})
```

成功后 emit `confirmed`；取消只关闭当前弹窗并保留原 Proposal，不调用业务接口。

- [ ] **步骤 6：接入 App 自动弹出逻辑**

在 `App.vue`：

- 新增 `isEngineeringContactFormProposal(item)`，判断 `item.proposal.kind === 'contact' && item.proposal.action === 'form_tasks'`；
- 待处理项为该类型时挂载 `EngineeringContactTaskDialog`；
- 普通 Proposal 继续挂载现有 `ProposalCard`；
- 弹窗确认后调用现有 `handleCurrentProposalDecision(false)`，刷新 Run、通知和工作区；
- 弹窗取消不把 Proposal 标记为已处理；
- 会话刷新时仍能根据待处理 Proposal 自动恢复弹窗。

- [ ] **步骤 7：增加文案映射**

在 `uiText.ts` 增加表单字段标签、完成类型、变更类别和错误码文案。显示文本统一使用“工程变更申请联络单办理事项”，不要显示为泛化的“普通业务操作”。

- [ ] **步骤 8：运行前端静态契约测试**

运行项目已有的必要 TypeScript 纯函数/文本测试命令；不运行 `npm run build`、`npm run test` 的浏览器套件或内置浏览器。

- [ ] **步骤 9：提交本任务**

```bash
git add web/src/domain-packs/mold/components/EngineeringContactTaskDialog.vue web/src/App.vue web/src/domain-packs/mold/uiText.ts
git commit -m "feat: add engineering contact task selection dialog"
```

---

### 任务 6：更新联络详情和追溯文档

**文件：**
- 修改：`backend/domain_packs/mold/erp/change/contact_lifecycle.py`
- 修改：`web/src/domain-packs/mold/components/ContactPanel.vue`
- 修改：`docs/ENGINEERING_CONTACT_COLLABORATION.md`
- 修改：`docs/REQUIREMENTS_TRACEABILITY.md`
- 修改：`docs/DEVELOPMENT_STATUS.md`

- [ ] **步骤 1：编写快照读取测试**

测试已确认的批量事项详情返回：

- 表单版本；
- 完整变更类别；
- 责任单位、责任人、完成日期；
- 工艺评估和金额；
- 来源文件版本和 Proposal 哈希；
- 无权限用户不能读取隐藏快照字段。

- [ ] **步骤 2：实现只读详情展示**

在 `contact_lifecycle.py` 增加快照摘要读取，保持权限过滤；在 `ContactPanel.vue` 增加“工程变更申请联络单”区域，展示表单快照和各办理事项状态。不得把已创建事项显示成已完成、已审批或已关闭。

- [ ] **步骤 3：更新追溯文档**

补充完整链路：

```text
附件关联确认
→ Skill 自动触发
→ prepare_contact_form_tasks
→ 自动弹窗
→ 人工选择/修订
→ HumanIntent 确认
→ 批量 ContactTask
→ 分派/反馈/方案/复验/关闭
```

明确普通 `prepare_contact_task` 与文档批量表单 Tool 的边界。

- [ ] **步骤 4：运行详情测试和 diff 检查**

运行：

```bash
pytest -q tests/test_engineering_contact_task_batch_confirm.py
python -m compileall -q backend
cd mold-agent && git diff --check
```

预期：PASS，且没有修改既有脏文件。

- [ ] **步骤 5：提交本任务**

```bash
git add backend/domain_packs/mold/erp/change/contact_lifecycle.py web/src/domain-packs/mold/components/ContactPanel.vue docs/ENGINEERING_CONTACT_COLLABORATION.md docs/REQUIREMENTS_TRACEABILITY.md docs/DEVELOPMENT_STATUS.md
git commit -m "docs: trace engineering contact form task flow"
```

---

### 任务 7：定向回归、真实链路验证和清理

**文件：**
- 修改：正式自动化回归测试源码（仅在发现缺失断言时）
- 创建并删除：一次性脱敏测试 PDF、临时日志、临时脚本
- 保留：新增正式测试源码

- [ ] **步骤 1：运行后端定向回归**

运行：

```bash
cd mold-agent
pytest -q tests/test_engineering_contact_task_proposals.py tests/test_engineering_contact_task_batch_confirm.py tests/test_engineering_contact_task_skill_continuation.py
pytest -q tests/test_contact_proposals.py tests/test_contacts.py tests/test_contact_lifecycle.py
pytest -q tests/test_local_capability_registry.py tests/test_local_change_contact_orchestration.py
```

确认普通工程联络创建、单事项、分派、反馈、方案、复验和关闭仍通过。

- [ ] **步骤 2：运行 Python 编译和变更检查**

运行：

```bash
python -m compileall -q backend
python -m pytest -q tests/test_engineering_contact_document_classification.py tests/test_engineering_contact_skill_activation.py
 git diff --check
```

不得运行数据库迁移，不得执行前端构建。

- [ ] **步骤 3：生成脱敏合成工程变更申请联络单**

一次性 PDF 必须包含图片中的字段：客户、责任部门、申请/完成日期、品名、模具编号、产品料号、完成类型、至少两个变更类别、变更说明、对策、至少两个相关单位、作业内容、工时、金额、备注和计价字段。记录 SHA256；不使用真实客户资料。

- [ ] **步骤 4：执行当前工作台真实上传闭环**

通过当前工作台真实上传，不直接插库、不调用内部写库函数、不伪造 OCR 结果。验证：

1. OCR/分类识别为工程联络单候选；
2. 本人确认文档类型后 Skill 自动触发；
3. 创建联络单 Proposal 并本人确认；
4. 原始附件关联 Proposal 并本人确认；
5. 自动生成 `prepare_contact_form_tasks` Proposal；
6. 界面自动弹出完整表单；
7. 选择至少两个单位、责任人、完成日期、完成类型和变更类别；
8. 弹窗重新生成最终 Proposal；
9. 未最终确认前数据库任务数量不变；
10. 最终确认后一次性创建所有任务并保存完整快照；
11. 后续状态为待处理/已分派，而不是已完成/已审批/已关闭。

- [ ] **步骤 5：验证失败恢复和重复操作**

在测试库中分别验证：

- 旧 Proposal 确认期间修改 Case revision；
- 撤销某人员权限；
- 停用责任部门；
- 重复点击确认；
- 重复上传相同 SHA256；
- Worker 在附件确认后重启；
- HISTORY 模式。

每个场景都必须保持原事实不变，不留下部分任务。

- [ ] **步骤 6：清理一次性产物**

删除一次性 PDF、上传副本、临时日志和临时脚本；保留正式自动化回归测试源码和脱敏断言。确认 `git status` 中没有临时样本。

- [ ] **步骤 7：最终检查并提交**

运行：

```bash
git status --short
git diff --check
python scripts/check_runtime.py --check
```

`check_runtime.py --check` 只读检查，不迁移、不启动服务。最终报告中明确区分：自动化测试通过、真实上传闭环通过、前端未执行构建、正式库未执行迁移。

---

## 验收结果必须回答的问题

1. 截图中的“下一步”是否由 Skill 自动生成了批量事项 Proposal，而不是只输出文字？
2. Proposal 是否自动打开完整工程变更申请联络单弹窗？
3. 图片中的完成类型和变更类别是否完整且可人工选择？
4. 相关单位、具体责任人、完成时间、作业内容、工时、金额和备注是否都能填写？
5. 本人确认前是否没有新增 `ContactTask`？
6. 确认后是否一次性创建全部事项且保留表单快照？
7. 重复确认、版本变化和权限变化是否被阻断？
8. HISTORY 模式是否没有创建线上事项？
9. 是否仍然完整遵守 Skill + Tool + Proposal + HumanIntent 边界？
10. 是否未修改当前已有的三个未提交源文件和 `NUL` 文件？
