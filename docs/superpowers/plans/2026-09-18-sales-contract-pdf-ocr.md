# 销售合同 PDF 单 Skill 与 Tool 编排改造实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪。项目禁止子智能体，不得使用 subagent-driven-development。

**目标：** 将当前已经实现的销售合同 PDF 预分类、OCR、复核、版本关系和两级审批能力，收口为一个由附件触发的 `sales_contract_intake` Skill；Skill 在多个 Agent Run 中按持久化状态调用 Tool，并删除合同专用页面及直接 intake 业务 API。

**架构：** PDF 上传后由通用宿主创建附件触发 Agent Run，Harness 根据可信 MIME 元数据激活完整 Skill 指令和已授权工具。Skill 只编排，所有查询和写操作分别通过 `query_*` 与 `prepare_*` Tool；prepare Tool 返回 proposal，本人确认后调用现有领域服务。OCR Worker 独立异步运行，完成后用户在同一会话的新 Run 中由同一 Skill 续办。底层识别已按后续计划 `2026-09-19-local-paddleocr-text-pipeline.md` 收口为 `PyMuPDF → 按需本地 PaddleOCR → 不可变页面文字缓存 → Qwen3 纯文本结构化`，不再把 PDF 或图片发送给视觉模型。

**技术栈：** Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2、PostgreSQL 18、PyMuPDF、PaddleOCR 3.7.0、PaddlePaddle 3.2.0、CUDA 12.6、Vue 3、TypeScript、Vitest、现有 Agent Harness/ToolSearch/Proposal/BPM。

**规格：** `docs/superpowers/specs/2026-09-18-sales-contract-pdf-ocr-design.md`

## 当前基线

- 任务 1～8 的模型、迁移、OCR Worker、领域服务、合同版本财务语义、权限和审计已经提交。
- 任务 9～10 的合同专用上传弹窗和复核工作区已经提交，但与最新确认架构冲突，必须在工具替代验证后精确删除。
- 当前只有 `query_sales_contract_intake` 和 `prepare_sales_contract_from_intake` 两个合同 intake Tool；上传接收、类型确认、OCR 重试和字段复核仍通过直接 HTTP API。
- 当前 Harness 只把 Skill 当作 ToolSearch 分组，附件不能激活 Skill，完整 `SKILL.md` 不会在激活时进入模型上下文。
- 当前 worktree 有任务 11 的未提交修改：`README.md`、`docs/DEVELOPMENT_STATUS.md`、`docs/REQUIREMENTS_TRACEABILITY.md`、`tests/test_contract_intake_proposal.py`。实施时保留并纳入最终提交，不得覆盖。

## 全局约束

- 整套流程只使用一个 `sales_contract_intake` Skill，不拆成多个合同 Skill。
- Agent Run 是当前主 Agent 的任务执行，不是子智能体；不得开启或使用子智能体。
- 初始上传 Run 只由本次上传批次触发；普通 Run 不因会话历史附件自动激活 Skill。
- 只接受 `application/pdf`；文档类型固定为 `BID_NOTICE`、`CUSTOMER_START_NOTICE`、`SALES_CONTRACT`、`MOLD_DRAWING`、`OTHER`。
- 非销售合同只分类归档，不运行完整合同 OCR。
- 所有写操作必须由 `prepare_*` Tool 生成 proposal，并在本人确认后执行。
- 项目、客户和模具必须已存在；OCR 和 Tool 不得创建它们。
- 唯一项目候选也必须人工确认；所有模具明细映射完成前不得提交合同审批。
- 审批固定为 `SALES_SUPERVISOR → FINANCE_OWNER`，两级全部通过后合同才生效。
- OCR 原值、标准化候选值和人工确认值分别保存；来源文件和页码持续可追溯。
- 不运行前端构建，不使用内置浏览器；前端仅运行 Vitest 和 `npm run typecheck`。
- 不对 `192.168.3.215:5432/moldpilot` 运行 pytest、Alembic upgrade 或自动 DDL。
- PostgreSQL 测试只允许本机 `MOLD_TEST_DATABASE_URL` 指定的 `moldpilot_test`，并严格串行运行 pytest。
- 目标库结构落地必须先备份并单独获得潘总批准；一次性 SQL 执行核验后删除。
- `.env`、OCR Key、数据库密码、PDF 原文和 OCR 原始响应不得提交或写日志。
- 主工作区 `backend/app/api.py`、`tests/test_core.py` 的既有未提交修改不得覆盖或混入功能提交。

---

## 文件结构

### 新增

- `backend/domain_packs/mold/tools/erp/commercial/document_intake_tools.py`：附件接收、批次查询、类型确认和 OCR 重试 Tool 及 proposal handler。
- `tests/test_document_intake_tools.py`：文档接收工具、proposal、权限、版本和幂等测试。

### 重点修改

- `backend/agent_core/harness.py`：附件触发 Skill、完整 Skill 指令加载、激活状态 checkpoint。
- `backend/app/schemas.py`：支持受控的 `ATTACHMENT_UPLOAD` Run 输入。
- `backend/app/api.py`：创建附件触发 Run 并记录可信 trigger。
- `backend/domain_packs/mold/tool_gateway.py`：登记完整工具集、附件触发元数据、Schema 和执行分发。
- `backend/domain_packs/mold/tools/erp/commercial/contract_intake_tools.py`：增加人工字段/项目/模具/关系复核 Tool，保留最终合同 Tool。
- `backend/domain_packs/mold/proposal_handlers.py`：登记文档接收和合同复核 proposal handler。
- `backend/domain_packs/mold/skills/erp/commercial/sales_contract_intake/SKILL.md`：改为上传到合同生效前的完整状态机。
- `backend/domain_packs/mold/erp/commercial/contract_intake.py`：补 OCR 失败重试领域方法，不增加 HTTP 入口。
- `backend/domain_packs/mold/manifest.py`：声明 PDF 附件触发策略，移除 intake Router 和专用工作区。
- `web/src/App.vue`：删除合同专用上传组件，保留产品中立的附件触发 Run。
- `web/src/domain-packs/mold/product.ts`、`web/src/domain-packs/template/product.ts`：声明通用附件触发 MIME 配置。
- `tests/test_model_harness.py`、`tests/test_agent_api.py`、`tests/test_contract_intake_review.py`、`tests/test_contract_intake_proposal.py`、`tests/test_domain_pack.py`：覆盖新架构。

### 删除

- `backend/domain_packs/mold/erp/commercial/contract_intake_api.py`
- `tests/test_contract_intake_api.py`
- `web/src/domain-packs/mold/components/DomainUploadFlow.vue`
- `web/src/domain-packs/template/components/DomainUploadFlow.vue`
- `web/src/domain-packs/mold/components/ContractIntakePanel.vue`
- `web/src/domain-packs/mold/contractIntake.ts`
- `web/src/domain-packs/mold/contractIntake.test.ts`

---

### 任务 1：让附件上传以通用方式创建 Agent Run

**文件：**
- 修改：`backend/app/schemas.py`
- 修改：`backend/app/api.py`
- 修改：`backend/domain_packs/mold/manifest.py`
- 修改：`web/src/App.vue`
- 修改：`web/src/domain-packs/mold/product.ts`
- 修改：`web/src/domain-packs/template/product.ts`
- 测试：`tests/test_agent_api.py`
- 测试：`tests/test_domain_pack.py`

- [ ] **步骤 1：编写附件触发 Run 的失败测试**

在 `tests/test_agent_api.py` 增加：

```python
def test_attachment_upload_run_requires_bound_files_and_records_trusted_trigger(client):
    user = sign_in(client)
    pdf = upload(client, PDF, "合同.pdf").json()

    missing = client.post("/api/runs", json={
        "prompt": "",
        "conversation_id": pdf["conversation_id"],
        "file_ids": [],
        "trigger": "ATTACHMENT_UPLOAD",
    })
    assert missing.status_code == 422

    created = client.post("/api/runs", json={
        "prompt": "",
        "conversation_id": pdf["conversation_id"],
        "file_ids": [pdf["id"]],
        "trigger": "ATTACHMENT_UPLOAD",
        "agent_permission_mode": "ask",
    })
    assert created.status_code == 200, created.text
    with SessionLocal() as db:
        run = db.get(m.Run, created.json()["id"])
        assert run.user_id == user["id"]
        assert run.prompt == "处理本次上传附件"
        assert run.checkpoint["run_trigger"] == "ATTACHMENT_UPLOAD"
        assert [row.file_id for row in db.scalars(
            select(m.RunFile).where(m.RunFile.run_id == run.id)
        )] == [pdf["id"]]
```

再断言 `trigger="USER"` 时空 prompt 仍返回 422，跨会话/他人附件仍由 `bind_run_files` 拒绝。

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_agent_api.py::test_attachment_upload_run_requires_bound_files_and_records_trusted_trigger -q
```

预期：FAIL，`RunInput.prompt` 拒绝空字符串或 `trigger` 为额外字段。

- [ ] **步骤 3：实现受控 Run 输入**

在 `backend/app/schemas.py` 使用模型级校验：

```python
class RunInput(StrictModel):
    prompt: str = Field(default="", max_length=6000)
    conversation_id: str | None = None
    file_ids: list[UUID] = Field(default_factory=list, max_length=10)
    trigger: Literal["USER", "ATTACHMENT_UPLOAD"] = "USER"
    agent_permission_mode: Literal["ask", "delegated_auto"] = "ask"

    @model_validator(mode="after")
    def valid_trigger_payload(self):
        if self.trigger == "USER" and not self.prompt.strip():
            raise ValueError("用户任务不能为空")
        if self.trigger == "ATTACHMENT_UPLOAD" and not self.file_ids:
            raise ValueError("附件触发任务必须绑定本次上传文件")
        return self
```

`create_run` 只在 `ATTACHMENT_UPLOAD` 且 prompt 为空时使用服务端固定文案 `处理本次上传附件`，并把 `run_trigger` 写入初始 checkpoint。不得根据前端提交的任意 Skill key 直接授权 Skill。

- [ ] **步骤 4：增加产品中立的上传触发配置**

Mold `PUBLIC_METADATA` 和 `web/src/domain-packs/mold/product.ts` 增加：

```python
"attachment_run": {"enabled": true, "media_types": ["application/pdf"]}
```

模板包不声明自动触发。`App.vue` 上传完整批次后，按 `product.attachment_run.media_types` 过滤文件；匹配文件一次性创建一个 `ATTACHMENT_UPLOAD` Run，不挂载任何 Mold 合同组件，不把文件名写成业务指令。此任务同时移除 `DomainUploadFlow` 的 import、`latestUploadBatch` 状态和模板挂载，使新 Run 与旧直连流程不会并行执行；对应死文件等 Tool 链路验证后再在任务 6 删除。已经自动绑定到 Run 的文件从 `selectedFiles` 移除，避免用户下次发送时重复绑定。

- [ ] **步骤 5：验证一批多 PDF 只创建一个 Run**

在 `tests/test_domain_pack.py` 增加静态边界断言：

```python
assert "attachment_run" in app_source
assert "DomainUploadFlow" not in app_source
for term in ("销售合同", "/api/document-intakes", "contract-intakes"):
    assert term not in app_source
```

前端不新增业务专用组件。后端 API 测试用两个 `file_ids` 断言仅新增一个 Run 和两个 RunFile。

- [ ] **步骤 6：运行定向测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_agent_api.py tests/test_domain_pack.py -q
```

预期：全部 PASS。

- [ ] **步骤 7：提交**

```bash
git add backend/app/schemas.py backend/app/api.py backend/domain_packs/mold/manifest.py web/src/App.vue web/src/domain-packs/mold/product.ts web/src/domain-packs/template/product.ts tests/test_agent_api.py tests/test_domain_pack.py
git commit -m "feat: start agent runs from configured attachments"
```

---

### 任务 2：让 Harness 基于附件激活完整 Skill

**文件：**
- 修改：`backend/agent_core/harness.py`
- 修改：`backend/domain_packs/mold/tool_gateway.py`
- 测试：`tests/test_model_harness.py`
- 测试：`tests/test_domain_pack.py`

- [ ] **步骤 1：编写附件激活和完整指令加载失败测试**

在 `tests/test_model_harness.py` 增加 Skill 夹具：

```python
PDF_SKILL = {
    "key": "sales_contract_intake",
    "name": "销售合同 PDF 接收",
    "version": "1.0.0",
    "instructions": "唯一流程指令：先查询本次 Run 附件，再准备文档接收 proposal。",
    "agent_description": "处理销售合同 PDF 接收、分类、OCR、复核和审批。",
    "tools": ["query_uploaded_files", "query_document_intake"],
    "optional_tools": ["prepare_document_intake"],
    "activation_attachments": [{"media_types": ["application/pdf"]}],
}
```

构造 `run_trigger="ATTACHMENT_UPLOAD"`、PDF files、无合同关键词的 prompt，断言第一次模型调用：

```python
assert "query_uploaded_files" in model.tool_names[0]
assert "prepare_document_intake" in model.tool_names[0]
assert "唯一流程指令" in model.transcripts[0][0]["content"]
```

再分别断言：普通 USER Run、非 PDF、未授权 Skill 均不激活这些工具。

- [ ] **步骤 2：运行失败测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_model_harness.py -k "attachment and skill" -q
```

预期：FAIL，当前 Harness 只按 prompt 判断 business tool，完整 Skill 指令未进入系统消息。

- [ ] **步骤 3：把附件触发和指令加入 Skill group**

`_skill_tool_groups` 返回以下附加字段：

```python
{
    "instructions": skill.get("instructions", ""),
    "activation_attachments": skill.get("activation_attachments") or [],
}
```

实现纯函数：

```python
def _attachment_skill_groups(context, groups):
    if context.get("run_trigger") != "ATTACHMENT_UPLOAD":
        return []
    media_types = {str(row.get("media_type") or "") for row in context.get("files") or []}
    return [group for group in groups if any(
        media_types & set(rule.get("media_types") or [])
        for rule in group.get("activation_attachments") or []
    )]
```

只使用服务端绑定的 Run files，不读文件名和内容。

- [ ] **步骤 4：激活工具并加载完整 Skill 指令**

在 `run_loop` 初始化时：

- 从 checkpoint 恢复 `active_skill_keys`；
- 新附件 Run 匹配到 Skill 后，把该 Skill required/optional/activation tools 加入 `active_tool_names`；
- 附件匹配视为本 Run 的明确业务触发，但所有写工具仍只生成 proposal；
- 把激活 Skill 的完整 `instructions` 放入首个 system message；
- checkpoint 保存 `active_skill_keys`；
- ToolSearch 后续激活 Skill 时，将完整指令加入下一轮 transient system instructions，而不是只返回 Skill 名称。

不得修改现有权限过滤：Harness 只能在 `context["tools"]` 已包含的工具中激活。

- [ ] **步骤 5：在领域包声明 PDF 触发元数据**

`SKILLS["sales_contract_intake"]` 增加：

```python
"activation_attachments": [{"media_types": ["application/pdf"]}],
```

`skill_context` 必须把该字段及完整 instructions 传入 Run context。`capability_descriptor` 可展示触发类型，但不得把 MIME 匹配当作额外授权。

- [ ] **步骤 6：验证恢复与普通 Run 隔离**

增加测试：第一次 checkpoint 后模拟 Worker 失租重领，传入保存的 `active_skill_keys`，断言仍加载同一 Skill；同一会话后续普通 Run 未携带附件且 prompt 与合同无关时，不因历史附件激活 Skill。

- [ ] **步骤 7：运行测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_model_harness.py tests/test_domain_pack.py -q
```

预期：全部 PASS。

- [ ] **步骤 8：提交**

```bash
git add backend/agent_core/harness.py backend/domain_packs/mold/tool_gateway.py tests/test_model_harness.py tests/test_domain_pack.py
git commit -m "feat: activate full skills from trusted attachments"
```

---

### 任务 3：封装文档接收、类型确认和 OCR 重试 Tool

**文件：**
- 创建：`backend/domain_packs/mold/tools/erp/commercial/document_intake_tools.py`
- 创建：`tests/test_document_intake_tools.py`
- 修改：`backend/domain_packs/mold/erp/commercial/contract_intake.py`
- 修改：`backend/domain_packs/mold/tool_gateway.py`
- 修改：`backend/domain_packs/mold/proposal_handlers.py`
- 修改：`backend/domain_packs/mold/notification_policy.py`
- 测试：`tests/test_contract_ocr.py`

- [ ] **步骤 1：编写 Tool Schema 和无副作用失败测试**

在 `tests/test_document_intake_tools.py` 增加：

```python
def test_prepare_document_intake_only_returns_proposal(db, user, upload_run):
    result = execute(db, user, "prepare_document_intake", {
        "file_ids": upload_run.file_ids,
    }, run=upload_run.run)
    assert result["proposal"]["action"] == "document_intake_create"
    assert result["proposal"]["input"]["file_ids"] == upload_run.file_ids
    assert db.scalar(select(func.count()).select_from(m.DocumentIntake)) == 0
```

同时测试 `query_uploaded_files` 只返回 `RunFile` 绑定附件，不返回同会话旧文件。

- [ ] **步骤 2：运行失败测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_document_intake_tools.py -q
```

预期：FAIL，工具未登记。

- [ ] **步骤 3：定义文档 Tool 输入模型**

`document_intake_tools.py` 定义：

```python
class PrepareDocumentIntakeInput(StrictModel):
    file_ids: list[UUID] = Field(min_length=1, max_length=10)

class DocumentIntakeQueryInput(StrictModel):
    document_intake_id: UUID | None = None

class TypeConfirmation(StrictModel):
    intake_file_id: UUID
    document_type: Literal[
        "BID_NOTICE", "CUSTOMER_START_NOTICE", "SALES_CONTRACT", "MOLD_DRAWING", "OTHER"
    ]
    contract_group_key: str | None = Field(default=None, max_length=60)

class PrepareDocumentTypeConfirmationInput(StrictModel):
    document_intake_id: UUID
    expected_version: int = Field(ge=1)
    files: list[TypeConfirmation] = Field(min_length=1, max_length=10)

class PrepareDocumentOcrRetryInput(StrictModel):
    document_intake_id: UUID
    expected_version: int = Field(ge=1)
    job_ids: list[UUID] = Field(min_length=1, max_length=10)
```

- [ ] **步骤 4：实现 query 与三个 prepare proposal**

固定工具：

- `prepare_document_intake`
- `query_document_intake`
- `prepare_document_type_confirmation`
- `prepare_document_ocr_retry`

`prepare_document_intake` 必须验证文件属于当前 Run、当前用户、同一会话且全部是 PDF；幂等 request key 使用 UUID5，由 `run.id + 排序后的 file_ids` 确定，模型不能提供 request key。

`query_document_intake` 有 ID 时读取该批次；无 ID 时只列出当前 Run 会话中当前用户可见的最近批次，不返回其他会话。

三个 prepare 工具统一返回：

```python
{
    "data": [],
    "source": "agent_proposal",
    "as_of": now().isoformat(),
    "proposal": {
        "kind": "document_intake",
        "action": action,
        "requires_approval": False,
        "input": data.model_dump(mode="json"),
        "display": display,
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False),
    },
}
```

- [ ] **步骤 5：实现确认处理和重新校验**

`source`、`validate_intent`、`confirm` 复用现有 proposal 哈希、Run 用户、授权指纹和工具可用性门禁。确认时重新加载文件/intake/job并重建 display；变化则返回 `VERSION_CONFLICT`。

动作分发：

```python
if proposal["action"] == "document_intake_create":
    return confirm_create(...)
if proposal["action"] == "document_type_confirmation":
    return confirm_types(...)
if proposal["action"] == "document_ocr_retry":
    return confirm_retry(...)
```

创建调用 `contract_intake.create`；类型确认调用 `contract_intake.confirm_types`；不得在 Tool 中复制领域校验。

- [ ] **步骤 6：补充 OCR 重试领域方法**

在 `contract_intake.py` 增加 `retry_jobs(...)`：锁定 intake 和指定 job，校验版本、所有权、`status == "FAILED"`、job 属于 intake、当前没有有效租约；重置为 `QUEUED`，清理安全错误和 retry_at，递增 intake/group 版本并记录 `document.ocr.retry_queued`。不在请求事务中调用 OCR。

- [ ] **步骤 7：登记 Schema、执行器和 proposal handler**

`tool_gateway.py` 为四个工具登记描述、权限、Schema 和执行分发。`proposal_handlers.py` 增加：

```python
ProposalHandler(
    "document_intake.execute",
    "domain_packs.mold.tools.erp.commercial.document_intake_tools",
    frozenset({
        "prepare_document_intake",
        "prepare_document_type_confirmation",
        "prepare_document_ocr_retry",
    }),
)
```

查询工具不进入 proposal handler。

- [ ] **步骤 8：验证权限、版本、幂等和重试**

测试必须覆盖：

- 非 PDF、跨会话、非 RunFile、他人文件；
- 一次 proposal 未确认时无 intake；
- 重复确认同一 create proposal 只产生一个 intake；
- 类型漏文件、未知类型、旧版本、重复确认；
- 只有销售合同排队完整 OCR；
- 失败 job 可重试，处理中/成功 job 不可重试；
- OCR 原始错误响应不出现在 Tool 回执。

- [ ] **步骤 9：运行定向测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_document_intake_tools.py tests/test_contract_ocr.py tests/test_files.py -q
```

预期：全部 PASS。

- [ ] **步骤 10：提交**

```bash
git add backend/domain_packs/mold/tools/erp/commercial/document_intake_tools.py backend/domain_packs/mold/erp/commercial/contract_intake.py backend/domain_packs/mold/tool_gateway.py backend/domain_packs/mold/proposal_handlers.py backend/domain_packs/mold/notification_policy.py tests/test_document_intake_tools.py tests/test_contract_ocr.py
git commit -m "feat: expose document intake through agent tools"
```

---

### 任务 4：封装合同 OCR 人工复核 Tool

**文件：**
- 修改：`backend/domain_packs/mold/tools/erp/commercial/contract_intake_tools.py`
- 修改：`backend/domain_packs/mold/tool_gateway.py`
- 修改：`backend/domain_packs/mold/proposal_handlers.py`
- 修改：`tests/test_contract_intake_review.py`
- 修改：`tests/test_contract_intake_proposal.py`

- [ ] **步骤 1：把复核测试改为 Tool 入口并先确认失败**

在 `tests/test_contract_intake_review.py` 增加：

```python
def test_prepare_review_snapshots_candidates_without_writing(db, admin, extracted_group, run):
    queried = execute(db, admin, "query_sales_contract_intake", {
        "contract_intake_group_id": extracted_group["group_id"],
    }, run=run)
    assert queried["data"][0]["project_candidates"][0]["id"] == extracted_group["project_id"]

    prepared = execute(db, admin, "prepare_sales_contract_intake_review", {
        "contract_intake_group_id": extracted_group["group_id"],
        "expected_version": extracted_group["group_version"],
        "project_id": extracted_group["project_id"],
        "project_version": extracted_group["project_version"],
        "accept_normalized_fields": True,
        "field_overrides": [],
        "mold_mappings": [{"row_key": "p1:mold-1", "mold_id": extracted_group["mold_id"]}],
        "relationship": {"relation_type": "NEW", "target_contract_id": None, "reason": "首次合同"},
    }, run=run)
    assert prepared["proposal"]["action"] == "sales_contract_intake_review"
    assert db.get(m.ContractIntakeGroup, extracted_group["group_id"]).project_id is None
```

- [ ] **步骤 2：运行失败测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_contract_intake_review.py -q
```

预期：FAIL，`prepare_sales_contract_intake_review` 未登记。

- [ ] **步骤 3：定义复核输入**

在 `contract_intake_tools.py` 增加：

```python
class FieldOverride(StrictModel):
    field_id: UUID
    confirmed_value: dict

class MoldMapping(StrictModel):
    row_key: str = Field(min_length=1, max_length=80)
    mold_id: UUID

class ContractRelationship(StrictModel):
    relation_type: Literal["NEW", "DUPLICATE", "REVISION", "SUPPLEMENT", "REPLACEMENT"]
    target_contract_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)

class ContractIntakeReviewProposalInput(ContractIntakeQueryInput):
    expected_version: int = Field(ge=1)
    project_id: UUID
    project_version: int = Field(ge=1)
    accept_normalized_fields: bool
    field_overrides: list[FieldOverride] = Field(default_factory=list, max_length=1000)
    mold_mappings: list[MoldMapping] = Field(max_length=500)
    relationship: ContractRelationship
```

`accept_normalized_fields` 不是自动确认：Tool 必须把每个最终值展开到 proposal display，只有用户点击通用确认卡后才写入 confirmed_value。为 false 时，所有字段都必须在 overrides 中明确提供。

- [ ] **步骤 4：扩展查询 Tool 返回完整候选**

`query_sales_contract_intake` 返回：

- group 状态和版本；
- 文件、OCR 状态和安全错误；
- 每个字段的 raw/normalized/confirmed/confidence/source；
- `project_candidates` 及命中依据；
- 候选项目内模具；
- 当前可选同项目销售合同关系目标；
- review warnings；
- `workflow_options`。

查询只读取当前用户可见数据，不写 `project_id`，唯一候选也不自动确认。

- [ ] **步骤 5：实现复核 proposal 和确认处理**

prepare 阶段调用领域查询和确定性预览函数，构造所有字段的最终快照：override 优先，否则在 `accept_normalized_fields=True` 时使用 normalized_value。proposal display 至少包含：原始 PDF、字段原值/候选值/最终值/来源、项目、客户一致性、每条模具映射、付款节点、合同关系和差异警告。

确认阶段重新生成快照并比较 display hash，再调用：

```python
contract_intake.review_group(
    db,
    user,
    group_id,
    expected_version=data.expected_version,
    project_id=str(data.project_id),
    project_version=data.project_version,
    confirmed_fields=confirmed_fields,
    mold_mappings=mold_mappings,
    relationship=relationship,
)
```

- [ ] **步骤 6：登记工具和 handler**

`tool_gateway.py` 登记 `prepare_sales_contract_intake_review`，归入 `sales_contract_intake` Skill。现有 `contract_intake.execute` handler 的工具集合增加该工具；`confirm` 按 proposal action 分发到复核或最终合同创建。

- [ ] **步骤 7：迁移原 API 测试证据**

把以下断言全部保留在 Tool 测试中：

- 字段来源文件和页码；
- 唯一、多候选、无候选、无权限；
- 缺客户关系、客户冲突；
- 模具缺失、模具不属于项目；
- 付款金额超过合同金额；
- 项目版本过期；
- raw_value 不被覆盖；
- proposal 取消不产生人工确认；
- proposal 确认后进入 `READY_FOR_DRAFT`。

- [ ] **步骤 8：运行定向测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_contract_intake_review.py tests/test_contract_intake_proposal.py tests/test_business_matching.py -q
```

预期：全部 PASS。

- [ ] **步骤 9：提交**

```bash
git add backend/domain_packs/mold/tools/erp/commercial/contract_intake_tools.py backend/domain_packs/mold/tool_gateway.py backend/domain_packs/mold/proposal_handlers.py tests/test_contract_intake_review.py tests/test_contract_intake_proposal.py
git commit -m "feat: review contract OCR through agent tools"
```

---

### 任务 5：把完整流程写入唯一 Skill 并验证跨 Run 续办

**文件：**
- 修改：`backend/domain_packs/mold/skills/erp/commercial/sales_contract_intake/SKILL.md`
- 修改：`backend/domain_packs/mold/tool_gateway.py`
- 修改：`tests/test_model_harness.py`
- 修改：`tests/test_domain_pack.py`
- 修改：`tests/test_contract_intake_proposal.py`

- [ ] **步骤 1：编写 Skill 工具清单和状态恢复失败测试**

`tests/test_domain_pack.py` 断言：

```python
skill = SKILLS["sales_contract_intake"]
assert skill["tools"] == [
    "query_uploaded_files",
    "query_document_intake",
    "query_sales_contract_intake",
]
assert set(skill["optional_tools"]) == {
    "prepare_document_intake",
    "prepare_document_type_confirmation",
    "prepare_document_ocr_retry",
    "prepare_sales_contract_intake_review",
    "prepare_sales_contract_from_intake",
}
assert skill["activation_attachments"] == [{"media_types": ["application/pdf"]}]
```

读取 SKILL.md，断言包含所有状态和工具名，不包含直接 intake API 路径或专用工作区说明。

- [ ] **步骤 2：重写唯一 Skill**

`SKILL.md` 固定写明：

1. 附件 Run 先调用 `query_uploaded_files`；
2. 尚未接收时调用 `prepare_document_intake`；
3. 预分类处理中结束当前 Run，不轮询；
4. 等待类型确认时先 query，再 prepare 类型确认；
5. 非合同归档后结束；
6. OCR 处理中结束当前 Run；
7. OCR 失败仅按用户明确要求 prepare 重试；
8. 待复核时 query 后 prepare 人工复核；
9. `READY_FOR_DRAFT` 时重新 query 后 prepare 最终合同；
10. 已创建时只查询合同和审批状态，不重复执行。

明确禁止：自行采用 OCR 值、自动选唯一项目、创建主数据、等待 Worker、调用直接业务 API、把 proposal 当成已执行、把 BPM 提交当成合同生效。

- [ ] **步骤 3：验证跨 Run 只依赖数据库状态**

在 `tests/test_contract_intake_proposal.py` 增加三个 Run：

- Run A 绑定上传文件，创建 intake 后结束；
- Worker 测试夹具写入预分类/OCR 结果；
- Run B 无附件但在同会话按 intake ID 查询并准备复核；
- Run C 在复核确认后重新查询并准备合同审批。

断言 Run B/C 不读取 Run A 的模型结论，只使用 Tool 返回的当前版本。

- [ ] **步骤 4：验证不同会话和普通 Run 不续办**

同一用户另一个会话中的 Run 不得无 ID 查询原会话 intake；其他用户即使知道 intake/group ID 也得到 404 或 `NOT_FOUND_OR_FORBIDDEN`。普通闲聊 Run 不激活该 Skill。

- [ ] **步骤 5：运行测试**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_model_harness.py tests/test_domain_pack.py tests/test_contract_intake_proposal.py -q
```

预期：全部 PASS。

- [ ] **步骤 6：提交**

```bash
git add backend/domain_packs/mold/skills/erp/commercial/sales_contract_intake/SKILL.md backend/domain_packs/mold/tool_gateway.py tests/test_model_harness.py tests/test_domain_pack.py tests/test_contract_intake_proposal.py
git commit -m "feat: orchestrate contract intake with one skill"
```

---

### 任务 6：删除合同专用页面和直接业务 API

**文件：**
- 删除：`backend/domain_packs/mold/erp/commercial/contract_intake_api.py`
- 删除：`tests/test_contract_intake_api.py`
- 删除：`web/src/domain-packs/mold/components/DomainUploadFlow.vue`
- 删除：`web/src/domain-packs/template/components/DomainUploadFlow.vue`
- 删除：`web/src/domain-packs/mold/components/ContractIntakePanel.vue`
- 删除：`web/src/domain-packs/mold/contractIntake.ts`
- 删除：`web/src/domain-packs/mold/contractIntake.test.ts`
- 修改：`backend/domain_packs/mold/manifest.py`
- 修改：`web/src/App.vue`
- 修改：`web/src/domain-packs/mold/components/DomainWorkspacePanel.vue`
- 修改：`web/src/domain-packs/template/components/DomainWorkspacePanel.vue`
- 修改：`web/src/domain-packs/mold/product.ts`
- 修改：`web/src/domain-packs/mold/uiPolicy.ts`
- 修改：`web/src/domain-packs/mold/uiText.ts`
- 修改：`tests/test_domain_pack.py`

- [ ] **步骤 1：确认工具测试已经覆盖待删除入口**

严格串行运行：

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_document_intake_tools.py tests/test_contract_intake_review.py tests/test_contract_intake_proposal.py -q
```

预期：全部 PASS。未通过时禁止删除旧入口。

- [ ] **步骤 2：删除 direct API Router**

删除 `contract_intake_api.py`，从 `manifest.install` 移除 import 和 `include_router`。保留领域服务 `contract_intake.py`，Tool 继续直接调用它。

- [ ] **步骤 3：删除专用前端组件和业务辅助模块**

删除列出的 Vue/TypeScript 死文件；任务 1 已经从 `App.vue` 移除 `DomainUploadFlow` import、`latestUploadBatch` 和组件挂载，本步骤断言这些引用没有被恢复，并保留任务 1 实现的产品中立附件触发 Run。`DomainWorkspacePanel.vue` 移除 `contract-intakes` 分支。

- [ ] **步骤 4：移除专用产品元数据和跳转**

移除：

- “合同识别” workspace tab；
- `contract_intake` detail link；
- notification/tool evidence 到 `contract-intakes` 的跳转。

保留工具中文名称和审计动作中文名称，因为通用会话和审计仍需展示。

- [ ] **步骤 5：删除旧 API 测试并加强无双轨断言**

删除 `tests/test_contract_intake_api.py` 前，确认其中行为已迁移到工具测试。`tests/test_domain_pack.py` 增加：

```python
for path in (
    "backend/domain_packs/mold/erp/commercial/contract_intake_api.py",
    "web/src/domain-packs/mold/components/DomainUploadFlow.vue",
    "web/src/domain-packs/mold/components/ContractIntakePanel.vue",
    "web/src/domain-packs/mold/contractIntake.ts",
):
    assert not (project_root / path).exists()

manifest = (project_root / "backend/domain_packs/mold/manifest.py").read_text(encoding="utf-8")
assert "contract_intake_api" not in manifest
assert "/api/document-intakes" not in project_sources
assert "/api/contract-intakes" not in project_sources
```

搜索范围排除历史文档和 Git 对象，只检查运行源码及测试。

- [ ] **步骤 6：运行后端与前端允许验证**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_domain_pack.py tests/test_document_intake_tools.py tests/test_contract_intake_review.py tests/test_contract_intake_proposal.py tests/test_files.py -q
Set-Location web
npm test
npm run typecheck
Set-Location ..
```

预期：全部 PASS；不得运行 `npm run build`。

- [ ] **步骤 7：提交**

```bash
git add -A backend/domain_packs/mold/erp/commercial/contract_intake_api.py backend/domain_packs/mold/manifest.py web/src tests/test_contract_intake_api.py tests/test_domain_pack.py
git commit -m "refactor: remove direct contract intake surfaces"
```

---

### 任务 7：端到端回归、文档和受控数据库准备

**文件：**
- 修改：`tests/test_contract_intake_proposal.py`
- 修改：`README.md`
- 修改：`docs/DEVELOPMENT_STATUS.md`
- 修改：`docs/REQUIREMENTS_TRACEABILITY.md`
- 修改：`docs/superpowers/specs/2026-09-18-sales-contract-pdf-ocr-design.md`
- 修改：`docs/superpowers/plans/2026-09-18-sales-contract-pdf-ocr.md`

- [ ] **步骤 1：完成 Tool/Skill 端到端 PostgreSQL 场景**

端到端测试必须经过真实 gateway 和 proposal handler，不直接调用旧 HTTP API：

```text
上传两个 PDF
→ ATTACHMENT_UPLOAD Run 激活 Skill
→ query_uploaded_files
→ prepare_document_intake + 本人确认
→ 假 Worker 完成预分类
→ query_document_intake
→ prepare_document_type_confirmation + 本人确认
→ 假 Worker 写入合同头、两套模具、付款节点及页码
→ query_sales_contract_intake
→ prepare_sales_contract_intake_review + 本人确认
→ prepare_sales_contract_from_intake + 本人确认
→ 业务主管审批
→ 财务审批
→ 合同生效
```

断言两个 `ContractDocument`、两条 `ContractMoldLine`、人工字段、来源页码、审计动作、审批顺序和最终财务状态。

- [ ] **步骤 2：严格串行运行后端定向回归**

只启动一个 pytest 进程：

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_contract_intake_schema.py tests/test_contract_ocr.py tests/test_document_intake_tools.py tests/test_contract_intake_review.py tests/test_contract_intake_proposal.py tests/test_contract_tools.py tests/test_finance_context_tools.py tests/test_files.py tests/test_bpm_project_roles.py tests/test_domain_pack.py tests/test_model_harness.py tests/test_agent_api.py -q
```

预期：0 failed。若出现 `TRUNCATE` 锁等待，先查询并关闭本项目遗留测试连接，确认没有并行 pytest 后再串行重跑；不得并发补跑同一测试库。

- [ ] **步骤 3：运行前端允许验证**

```powershell
Set-Location web
npm test
npm run typecheck
Set-Location ..
```

预期：全部 PASS。不得运行前端构建或内置浏览器。

- [ ] **步骤 4：运行静态检查**

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m py_compile backend/agent_core/harness.py backend/app/api.py backend/app/document_worker.py backend/domain_packs/mold/erp/commercial/contract_intake.py backend/domain_packs/mold/tools/erp/commercial/document_intake_tools.py backend/domain_packs/mold/tools/erp/commercial/contract_intake_tools.py
git diff --check
```

预期：退出码均为 0。

- [ ] **步骤 5：更新文档为单 Skill 架构**

README 和状态文档明确：

- 上传触发 Agent Run；
- 一个 `sales_contract_intake` Skill；
- Tool 列表和人工确认边界；
- OCR Worker 是独立第五进程；
- 没有合同专用页面和直接 intake API；
- 代码/隔离测试完成不等于真实 OCR、目标库和业务人员验收完成。

- [ ] **步骤 6：只准备正式库变更材料，不执行**

1. 只读核对目标库 revision；
2. 创建并校验完整备份；
3. 根据迁移生成带数据库名、起始 revision 和目标表不存在断言的一次性 SQL；
4. 将 SQL、备份位置、哈希和验证命令交潘总审批；
5. 未获得单独批准时停止；
6. 获批执行、核验表/约束/权限/revision 后立即删除一次性 SQL。

不得在本任务自行迁移目标库。

- [ ] **步骤 7：检查工作区和提交最终改动**

```powershell
git status --short
git diff --check
```

确认没有 `.env`、PDF、OCR 响应、临时 SQL、调试日志或测试临时文件。提交任务 11 原有未提交改动与本任务文档：

```bash
git add README.md docs/DEVELOPMENT_STATUS.md docs/REQUIREMENTS_TRACEABILITY.md docs/superpowers/specs/2026-09-18-sales-contract-pdf-ocr-design.md docs/superpowers/plans/2026-09-18-sales-contract-pdf-ocr.md tests/test_contract_intake_proposal.py
git commit -m "docs: verify skill-driven contract intake"
```

---

## 最终完成门禁

只有同时满足以下条件才能宣称本功能完成：

- PDF 上传通过可信附件元数据激活唯一 Skill；
- 完整 `SKILL.md` 在激活后进入 Harness 上下文并可在 checkpoint 恢复；
- 文档接收、类型确认、OCR 重试、合同复核和合同创建均通过 Tool；
- 所有写操作经过 proposal 和本人确认；
- OCR Worker 异步执行，Agent Run 不持续轮询；
- 同一会话的新 Run 可以根据数据库状态续办；
- 合同专用上传弹窗、复核工作区和直接 intake API 已删除；
- 所有定向 pytest 严格串行通过；
- Vitest、typecheck、py_compile、`git diff --check` 通过；
- 隔离 PostgreSQL 端到端 Tool/Skill 场景通过；
- 真实 OCR 服务用脱敏文本/扫描/多 PDF 样本验证；
- 目标库变更经潘总单独批准、备份、执行和核验；
- 业务主管和财务角色、权限及已发布流程有效；
- 目标项目已有客户关系和可关联正式模具；
- 潘总完成实际界面和真实样本验收；
- 文档未把未完成的真实联调标记为已验收。
