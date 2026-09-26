# 本地修改能力与第 11 章 Skill/Tool 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 executing-plans 逐任务实现此计划。项目禁止子智能体；不得使用 subagent-driven-development。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 仅将当前工作区本地修改/新增的业务能力以及第 11 章 FR-078～FR-090 的可实施部分封装为本地 Skill/Tool，并保持远程未修改代码不变。

**架构：** 保留当前本地数据库、Proposal、本人确认、审批、通知和审计机制。业务能力通过本地 Skill 进入，查询 Tool 只读，写入 Tool 只生成 Proposal；确认后才写入本地对象。第 11 章新增本地设变承接上下文和影响编排，不直接调用 ERP；远程未修改的工程联络和 ERP 文件不编辑。

**技术栈：** Python、FastAPI、SQLAlchemy、Pydantic、PostgreSQL 隔离测试库、现有 Tool Gateway、Skill 能力目录、Proposal/HumanIntent/BPM、pytest。

**规格：** `docs/superpowers/specs/2026-09-24-local-capabilities-without-erp-design.md`

## 全局约束

- 只允许修改当前 `git diff --name-only` 文件、当前本地新增文件和本计划新增文件。
- 远程 `HEAD` 中未被本地修改的代码不修改、不删除、不重命名、不回滚。
- 不执行数据库迁移；如需数据库结构，新增迁移源并只做离线/隔离验证。
- 不运行前端构建、不使用浏览器、不联调 ERP。
- 本地业务 Tool/Skill 不调用 ERP HTTP、ERP MCP、ERP 数据库或 ERP 文件目录。
- `prepare_*` Tool 未经本人确认不得写入业务数据。
- 每次编辑前先回读目标文件并保留已有未提交内容；不得使用完整文件覆盖已有本地修改。
- 每次任务只 stage 本任务文件，不能把其他本地修改一起提交。

## 文件与职责锁定

### 本地能力目录和注册

- 修改：`backend/domain_packs/mold/tool_gateway.py` —— 在现有本地注册链中补齐本地 Skill/Tool 的 Schema、路由和执行分发；只改本地增量区域或追加隔离逻辑。
- 修改：`backend/domain_packs/mold/manifest.py` —— 保持本地文档入口和工作区路由；移除/阻断本地新增流程中的 ERP 绑定入口时只改本地增量代码。
- 修改：`backend/domain_packs/mold/proposal_handlers.py` —— 仅为本地新增 Proposal 增加允许的处理器映射。
- 创建：`backend/domain_packs/mold/skills/local/` 下的 Skill 文档 —— 使用中性的本地业务能力名，不新增 ERP 外部接口语义。
- 创建：`backend/domain_packs/mold/tools/local/` 下的本地 Tool 模块 —— 本地查询、Proposal 和确认前校验。
- 创建/修改：`tests/test_local_capability_registry.py` —— 能力目录、Schema、Skill 依赖和执行分发测试。

### 本地文档、中标和开工能力

- 修改：`backend/domain_packs/mold/erp/commercial/checklist_binding.py` —— 若当前本地新增代码仍依赖 ERP 核算清单，改为本地候选/本地资料指纹，不调用 ERPClient。
- 修改：`backend/domain_packs/mold/erp/commercial/post_start_binding.py` —— 后置绑定只允许 MoldPilot 本地合同、项目、模具和文件版本。
- 修改：`backend/domain_packs/mold/erp/commercial/document_workflow.py`、`document_workflow_api.py` —— 只在必要的本地增量范围内补齐 Skill/Tool 需要的本地回执，不扩大 OCR 业务范围。
- 修改：`backend/domain_packs/mold/erp/commercial/bid_start_workflow.py`、`admin_start_workflow.py`、`contract_match_workflow.py` —— 只保留本地事件、草稿、决定、部门回执和候选关系。
- 修改：`backend/domain_packs/mold/tools/erp/commercial/document_intake_tools.py`、`bid_start_tools.py`、`admin_start_notice_tools.py`、`contract_intake_tools.py` —— 只修改当前工作区已存在的本地增量；补齐外部来源阻断和 Tool 契约。
- 测试：现有 `tests/test_document_intake_tools.py`、`tests/test_bid_start_tools.py`、`tests/test_admin_start_workflow_tools.py`、`tests/test_admin_start_proposal_confirmation.py`、`tests/test_post_start_binding_state.py`，必要时新增本地 ERP 依赖阻断测试。

### 本地模型配置能力

- 创建：`backend/domain_packs/mold/tools/local/model_configuration_tools.py` —— 将当前本地模型目录、供应商检测、默认模型和文档模型引用封装为查询/Proposal Tool。
- 创建：`backend/domain_packs/mold/skills/local/model_provider_configuration/SKILL.md`。
- 创建：`backend/domain_packs/mold/skills/local/document_model_configuration/SKILL.md`。
- 修改：`backend/app/model_catalog_api.py` —— 仅保留或复用本地配置服务；不让 HTTP 配置接口成为绕过 Proposal 的第二条业务写入路径。
- 测试：新增 `tests/test_local_model_configuration_tools.py`，覆盖超级管理员、revision 冲突、Key 不回显、默认模型和文档模型占用门禁。

### 第 11 章本地设变与工程联络能力

- 创建：`backend/domain_packs/mold/erp/change/local_change_models.py` —— 保存设变承接分类、执行方式、收费/免费、合同关系、客户模号历史、原模具/原项目关联和承接状态；不增加 ERP 来源字段。
- 创建：`backend/domain_packs/mold/tools/local/change_intake_tools.py` —— `query_local_change_context`、`prepare_local_change_intake`、`prepare_local_change_association`、`prepare_local_change_acceptance`。
- 创建：`backend/domain_packs/mold/skills/local/change/engineering_change_intake/SKILL.md`。
- 创建：`backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md` —— 编排现有本地联络协作 Tool，不修改远程原有 `contact_tools.py`。
- 修改：`backend/domain_packs/mold/models.py` 或本地模型聚合入口 —— 只增加本地新增模型注册，不重写远程模型。
- 创建：`backend/domain_packs/mold/alembic_domain/versions/mb0d0e000017_local_change_intake.py` —— 若本地设变承接对象需要表结构，新增迁移源但不执行迁移。
- 测试：创建 `tests/test_local_change_intake_tools.py`，覆盖 FR-078～081 的分类、收费/合同独立记录、原模具复用、客户模号历史、多候选阻断和首次外部模具分流。
- 测试：创建 `tests/test_local_change_contact_orchestration.py`，覆盖 FR-082～090 的字段完整性、影响动作、方案版本、执行反馈、独立复验、处理人自复验阻断、关闭门禁和本地来源限制。

---

### 任务 1：固化本地文件白名单并建立越界检查

**文件：**
- 创建：`tests/local_scope.py` —— 只读收集当前工作区允许范围。
- 创建：`tests/test_local_scope_guard.py`
- 不修改远程业务代码。

- [x] **步骤 1：编写失败测试**

测试读取 `git diff --name-only` 和 `git ls-files --others --exclude-standard`，断言本地范围检查器能识别允许文件；断言远程未修改的第 11 章文件不在允许修改集合中。

```python
def test_scope_contains_worktree_changes_but_not_remote_only_files():
    allowed = current_worktree_scope()
    assert "backend/domain_packs/mold/tool_gateway.py" in allowed
    assert "backend/domain_packs/mold/tools/erp/change/change_intake_tools.py" not in allowed
```

- [x] **步骤 2：运行测试确认失败**

运行：`pytest tests/test_local_scope_guard.py -q`

预期：FAIL，`current_worktree_scope` 尚未实现。

- [x] **步骤 3：实现最小范围检查器**

在 `tests/local_scope.py` 中实现只读范围收集；禁止自动修改文件、自动恢复文件或自动 stage 文件。测试只允许访问当前工作区 Git 元数据。

- [x] **步骤 4：运行测试确认通过**

运行：`pytest tests/test_local_scope_guard.py -q`

预期：PASS；同时执行 `git diff --name-only`，确认没有因为测试产生代码修改。

- [x] **步骤 5：提交**

```bash
git add tests/local_scope.py tests/test_local_scope_guard.py
git commit -m "test: guard local modification scope"
```

### 任务 2：盘点并补齐本地业务 Skill/Tool 注册

**文件：**
- 修改：`backend/domain_packs/mold/tool_gateway.py`
- 修改：`backend/domain_packs/mold/manifest.py`
- 修改：`backend/domain_packs/mold/proposal_handlers.py`
- 创建：`backend/domain_packs/mold/skills/local/` 下本地 Skill 文档
- 创建：`tests/test_local_capability_registry.py`

- [x] **步骤 1：先增加注册契约测试**

测试本地业务能力均有 Skill、Tool、Schema 和执行分支；写入 Tool 必须标记 Proposal/确认；能力描述和路由不得包含 ERP 外部调用。

```python
def test_local_capabilities_have_skill_and_tool_contracts():
    context = skill_context(admin)
    assert "sales_contract_intake" in context
    assert "bid_to_start_notice" in context
    assert "engineering_change_intake" in context
    assert tool_schema("prepare_local_change_intake")
```

- [x] **步骤 2：运行测试确认失败**

运行：`pytest tests/test_local_capability_registry.py -q`

预期：FAIL，新增本地 Skill/Tool 尚未全部注册。

- [x] **步骤 3：补齐最小注册链**

在现有 `TOOLS`、`SKILLS`、`CAPABILITY_NAMES`、`CAPABILITY_DEPARTMENTS`、`CAPABILITY_TYPES`、`tool_schema` 和 `execute` 的本地增量区域登记能力。不要删除或重写远程原有注册；若需要阻断本地范围内的 ERP 外部能力，只增加最终过滤或拒绝逻辑，并保存原有未提交内容。

- [x] **步骤 4：补齐 Skill 文档**

每个本地 Skill 写清触发语、首轮查询、Proposal、本人确认、版本冲突、权限错误、失败恢复和“不调用 ERP”边界。工程联络 Skill 只编排本地对象和本地协作 Tool。

- [x] **步骤 5：运行测试确认通过**

运行：`pytest tests/test_local_capability_registry.py tests/test_harness_explicit_tool_activation.py -q`

预期：PASS；能力目录不出现本轮新增的 ERP 外部 Tool。

- [x] **步骤 6：提交**

```bash
git add backend/domain_packs/mold/tool_gateway.py backend/domain_packs/mold/manifest.py backend/domain_packs/mold/proposal_handlers.py backend/domain_packs/mold/skills/local tests/test_local_capability_registry.py
git commit -m "feat: register local business skills and tools"
```

### 任务 3：解除本地文档/开工/绑定能力的 ERP 依赖

**文件：**
- 修改：`backend/domain_packs/mold/erp/commercial/checklist_binding.py`
- 修改：`backend/domain_packs/mold/erp/commercial/post_start_binding.py`
- 修改：`backend/domain_packs/mold/erp/commercial/document_workflow.py`
- 修改：`backend/domain_packs/mold/erp/commercial/document_workflow_api.py`
- 修改：`backend/domain_packs/mold/erp/commercial/bid_start_workflow.py`
- 修改：`backend/domain_packs/mold/erp/commercial/admin_start_workflow.py`
- 修改：`backend/domain_packs/mold/erp/commercial/contract_match_workflow.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/document_intake_tools.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/bid_start_tools.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/admin_start_notice_tools.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/contract_intake_tools.py`
- 测试：现有本地文档/中标/开工测试及新增 `tests/test_local_business_no_erp.py`

- [x] **步骤 1：增加 ERP 依赖阻断测试**

测试本地合同登记、开工决定、部门回执、合同候选和后置绑定不会实例化 `ERPClient`、调用 ERP MCP 或写入 ERP 来源字段；后置绑定只接受 MoldPilot 本地对象版本。

```python
def test_post_start_binding_is_local_only(monkeypatch, db, admin):
    monkeypatch.setattr("domain_packs.mold.erp_adapter.ERPClient", fail_if_called)
    result = execute(db, admin, "prepare_post_start_binding", local_binding_input)
    assert result["source"] == "agent_proposal"
```

- [x] **步骤 2：运行测试确认失败**

运行：`pytest tests/test_local_business_no_erp.py tests/test_post_start_binding_state.py -q`；本轮以不加载数据库夹具的本地边界测试复现并验证。

预期：至少在核算清单绑定路径失败，显示仍依赖 ERPClient 或 ERP 来源字段。

- [x] **步骤 3：改为本地事实和本地 Proposal**

保留合同 OCR、项目候选、模具快照、部门回执和本地文件版本；删除本地新增路径对 ERP 核算清单引用的要求。对原本需要 ERP 的资料，返回“本地未登记/需要人工上传本地资料”，不得伪造完成。

- [x] **步骤 4：运行定向测试**

运行：`pytest tests/test_local_business_no_erp.py tests/test_automatic_document_intake.py tests/test_bid_start_tools.py tests/test_admin_start_workflow_tools.py tests/test_post_start_binding_state.py -q`

预期：PASS；本地业务 Proposal 和确认链保持不变。已执行不触发迁移的本地边界测试；数据库集成测试按约束未执行。

- [ ] **步骤 5：提交**

```bash
git add backend/domain_packs/mold/erp/commercial/checklist_binding.py backend/domain_packs/mold/erp/commercial/post_start_binding.py backend/domain_packs/mold/erp/commercial/document_workflow.py backend/domain_packs/mold/erp/commercial/document_workflow_api.py backend/domain_packs/mold/erp/commercial/bid_start_workflow.py backend/domain_packs/mold/erp/commercial/admin_start_workflow.py backend/domain_packs/mold/erp/commercial/contract_match_workflow.py backend/domain_packs/mold/tools/erp/commercial tests/test_local_business_no_erp.py
git commit -m "refactor: keep local document and start workflows independent"
```

### 任务 4：封装本地模型配置能力

**文件：**
- 创建：`backend/domain_packs/mold/tools/local/model_configuration_tools.py`
- 创建：`backend/domain_packs/mold/skills/local/config/model_provider_configuration/SKILL.md`
- 创建：`backend/domain_packs/mold/skills/local/config/document_model_configuration/SKILL.md`
- 修改：`backend/app/model_catalog_api.py`
- 测试：`tests/test_local_model_configuration_tools.py`

- [x] **步骤 1：编写失败测试**

覆盖超级管理员读取、供应商目录检测、revision 契约、API Key 不回显、默认模型更改、文档模型引用阻断和普通用户拒绝。

```python
def test_model_configuration_prepare_does_not_write_before_confirmation(db, admin):
    before = catalog_revision()
    result = execute(db, admin, "prepare_model_provider_save", provider_input)
    assert result["source"] == "agent_proposal"
    assert catalog_revision() == before
```

- [x] **步骤 2：运行测试确认失败**

运行：`pytest tests/test_local_model_configuration_tools.py -q`

预期：FAIL，模型配置尚未有本地 Tool 契约。

- [x] **步骤 3：实现只读和 Proposal Tool**

复用现有 `model_catalog`、`model_discovery` 和配置锁；Tool 不保存客户端传入的确认身份，不返回 Key，不绕过 revision。确认执行必须进入已有本地管理员确认机制。

- [x] **步骤 4：运行测试确认通过**

运行：`pytest tests/test_local_model_configuration_tools.py tests/test_model_catalog.py tests/test_model_discovery.py tests/test_run_model_selection.py -q`

预期：PASS。

- [ ] **步骤 5：提交**

```bash
git add backend/domain_packs/mold/tools/local/model_configuration_tools.py backend/domain_packs/mold/skills/local/config/model_provider_configuration backend/domain_packs/mold/skills/local/config/document_model_configuration backend/app/model_catalog_api.py tests/test_local_model_configuration_tools.py
git commit -m "feat: expose local model configuration capabilities"
```

### 任务 5：实现第 11 章设变承接本地对象和 Tool

**文件：**
- 创建：`backend/domain_packs/mold/erp/change/local_change_models.py`
- 创建：`backend/domain_packs/mold/tools/local/change_intake_tools.py`
- 创建：`backend/domain_packs/mold/skills/local/change/engineering_change_intake/SKILL.md`
- 修改：`backend/domain_packs/mold/models.py`
- 创建：`backend/domain_packs/mold/alembic_domain/versions/mb0d0e000017_local_change_intake.py`
- 创建：`tests/test_local_change_intake_tools.py`

- [x] **步骤 1：编写 FR-078～081 失败测试**

覆盖客户/内部/委外分类，内部或委外执行方式，收费/免费与合同状态分离，原模具复用，客户模号历史，多候选阻断，以及首次外部模具必须进入承接分支。

```python
def test_existing_mold_change_reuses_identity_and_records_customer_number_history(db, admin):
    result = execute(db, admin, "prepare_local_change_intake", existing_mold_change)
    assert result["source"] == "agent_proposal"
    assert result["proposal"]["input"]["mold_mode"] == "EXISTING"
```

- [x] **步骤 2：运行测试确认失败**

运行：`pytest tests/test_local_change_intake_tools.py -q`

预期：FAIL，Tool 和本地设变承接对象尚未存在。

- [x] **步骤 3：实现本地模型和严格输入**

字段至少包括：项目、原模具、客户模号及历史、设变分类、执行方式、收费状态、合同状态、执行范围、客户依据、报价/承接状态、当前版本和创建人。所有候选关联必须通过权限和唯一性校验；多候选不自动选择。

- [x] **步骤 4：实现查询和 Proposal Tool**

`query_local_change_context` 只返回本地项目、模具、合同、开工依据、已有设变和候选关系；三个 `prepare_local_*` Tool 只生成 Proposal。确认时复核项目版本、模具版本、附件所有权和授权。

- [x] **步骤 5：新增迁移源但不执行**

迁移只创建本地设变承接表和必要索引；不修改已执行迁移，不执行正式库迁移。测试夹具仅通过本地白名单建表或隔离 PostgreSQL 验证。本轮仅进行迁移源静态检查，未连接数据库。

- [x] **步骤 6：运行测试确认通过**

运行：`pytest --noconftest tests/test_local_change_intake_tools.py tests/test_local_change_migration_source.py -q`

预期：PASS；本轮仅验证 Tool 和迁移源文本/模型结构，不连接正式库、不执行迁移。

- [ ] **步骤 7：提交**

```bash
git add backend/domain_packs/mold/erp/change/local_change_models.py backend/domain_packs/mold/tools/local/change_intake_tools.py backend/domain_packs/mold/skills/local/change/engineering_change_intake backend/domain_packs/mold/models.py backend/domain_packs/mold/alembic_domain/versions/mb0d0e000017_local_change_intake.py tests/test_local_change_intake_tools.py
git commit -m "feat: add local engineering change intake"
```

### 任务 6：封装第 11 章工程联络协作编排

**文件：**
- 创建：`backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md`
- 修改：`backend/domain_packs/mold/tool_gateway.py`
- 修改：`backend/domain_packs/mold/proposal_handlers.py`
- 创建：`tests/test_local_change_contact_orchestration.py`

- [x] **步骤 1：编写 FR-082～090 失败测试**

覆盖结构化必填字段、责任事项影响对象、继续/暂停/取消/返工/重新下达、方案版本冻结、执行反馈、独立复验、处理人自复验阻断、附件变更失效和关闭门禁。

```python
def test_approved_solution_does_not_mean_closed(db, admin):
    result = execute(db, admin, "query_local_change_context", {"identifier": "CHG-001"})
    assert result["data"][0]["derived_status"]["has_open_execution_or_recheck_items"] is True
```

- [x] **步骤 2：运行测试确认失败**

运行：`pytest tests/test_local_change_contact_orchestration.py -q`

预期：FAIL，当前本地 Skill/Tool 编排尚未覆盖完整 FR-082～090 门禁。

- [x] **步骤 3：编排现有本地联络能力**

不修改远程原有 `contact_tools.py` 或远程 Skill 文件。通过本地 Tool Gateway 和新增本地 Skill 调用已有本地查询/Proposal 能力；对本地新增设变承接对象补充版本和影响项校验。

- [x] **步骤 4：实现 FR-085～090 的本地门禁**

执行方案必须冻结附件和影响项；方案生效、执行反馈、独立复验、关闭分别保存。影响合同、金额、交期、计划或任务时只形成本地待处理事项，不伪造已经完成的业务回执。

- [x] **步骤 5：运行测试确认通过**

运行：`pytest tests/test_local_change_contact_orchestration.py tests/test_contact_proposals.py tests/test_contact_impact.py -q`

预期：PASS；本轮实际执行本地入口契约测试 `3 passed`；既有联络生命周期文件未执行数据库夹具，避免触发迁移。

- [ ] **步骤 6：提交**

```bash
git add backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration backend/domain_packs/mold/tool_gateway.py backend/domain_packs/mold/proposal_handlers.py tests/test_local_change_contact_orchestration.py
git commit -m "feat: orchestrate local engineering contact workflow"
```

### 任务 7：本地范围验证与文档追溯

**文件：**
- 修改：`docs/DEVELOPMENT_STATUS.md`
- 修改：`docs/REQUIREMENTS_TRACEABILITY.md`
- 创建：`tests/test_local_scope_final.py`

- [x] **步骤 1：增加最终范围测试**

测试所有本轮新增/修改业务 Tool 均能从 Skill 或能力目录发现；所有 ERP 外部调用在本地范围被阻断；远程未修改文件的 Git hash/内容未改变。

- [x] **步骤 2：运行后端定向回归**

运行：`pytest tests/test_local_scope_guard.py tests/test_local_capability_registry.py tests/test_local_business_no_erp.py tests/test_local_model_configuration_tools.py tests/test_local_change_intake_tools.py tests/test_local_change_contact_orchestration.py -q`

预期：PASS；使用 `--noconftest` 执行，`17 passed`；未运行前端构建、未执行迁移、未联调 ERP。

- [x] **步骤 3：执行 diff 越界检查**

运行：`git diff --name-only HEAD~1..HEAD` 和工作区 `git diff --name-only`，逐项与本地白名单比对；检查未出现远程未修改文件。

- [x] **步骤 4：更新追溯文档**

只记录实际完成的本地 Skill/Tool、FR-078～090 覆盖情况、测试命令和未验收限制；不能把自动化测试通过写成生产业务验收通过。

- [ ] **步骤 5：提交**

```bash
git add docs/DEVELOPMENT_STATUS.md docs/REQUIREMENTS_TRACEABILITY.md tests/test_local_scope_final.py
git commit -m "docs: trace local capabilities and chapter 11 coverage"
```

## 计划自检

- 规格第 1 节的本地范围、远程保护对应任务 1、2、7。
- 规格第 2 节的合同文档、中标开工、模型配置对应任务 2、3、4。
- 规格第 3 节 FR-078～081 对应任务 5，FR-082～090 对应任务 6。
- 规格第 4 节 ERP 边界对应任务 3、6、7。
- 规格第 5～6 节 Skill/Tool 契约对应任务 2、4、5、6。
- 规格第 7 节文件修改规则对应任务 1、7。
- 规格第 8 节验证边界对应全部任务的测试步骤。
- 计划未安排前端构建、正式迁移、ERP 联调或远程文件修改。
- 计划中的每个步骤均有具体文件、命令、预期结果和提交边界。
