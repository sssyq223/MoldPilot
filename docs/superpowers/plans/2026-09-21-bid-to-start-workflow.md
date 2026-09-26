# 中标到开工通知及后置关联实现计划

> **面向 AI 代理的工作者：** 使用 executing-plans 在当前主工作区内联执行；禁止子智能体、worktree、提交和正式数据库迁移。步骤使用复选框跟踪进度。

**目标：** 在现有文档接收、报价承接和合同 OCR 能力之上，补齐可审计的中标匹配、开工通知版本、部门独立回执、项目部最终决定及合同/核算清单后置绑定门禁。

**架构：** 保留 `DocumentIntake` 作为上传接收事实，新增领域表承载中标事件匹配、开工通知版本、部门回执、项目决定和后置绑定。`bid_notice.confirmed` 只创建待匹配记录，人工匹配及中标接收版本确认后才允许生成开工通知；旧 `BusinessSubject` 链路通过兼容查询保留，不作为新绑定门禁的唯一事实。

**技术栈：** FastAPI、SQLAlchemy、PostgreSQL、现有 Agent Core Outbox/Inbox、Pydantic、串行 pytest。

**规格：** `docs/superpowers/specs/2026-09-21-bid-to-start-workflow-design.md`

## 全局约束

- 保留所有已有未提交修改；不创建 worktree、不提交。
- 不执行正式数据库迁移；新增迁移源和临时测试 SQL 只用于审查，执行后删除临时 SQL。
- 测试仅使用 `127.0.0.1:55432/moldpilot_test`，不得触碰 `agent_db` 的业务数据。
- 不自动确认、批准、登记或绑定真实业务资料，不重新处理历史失败文档。
- 审计和 API 不输出 PDF 正文、模型原文、提示词、Key 或 Token。

---

## 任务 1：持久化模型和迁移源

**文件：**
- 创建：`backend/domain_packs/mold/erp/commercial/bid_start_workflow_models.py`
- 修改：`backend/domain_packs/mold/models.py`
- 创建：`backend/domain_packs/mold/alembic_domain/versions/mb0d0e000014_bid_start_workflow.py`
- 测试：`tests/test_bid_start_workflow_schema.py`

- [x] 编写模型元数据测试，验证五类对象的字段、状态约束、唯一键和外键关系。
- [x] 运行测试确认新增模型尚未注册，得到预期失败。
- [x] 添加以下最小表模型：`BidNoticeMatch`、`StartNotice`、`StartNoticeDepartmentAck`、`ProjectStartDecision`、`PostStartBinding`。
- [x] 增加 `StartNotice` 版本唯一约束；部门回执按通知版本和部门键唯一；绑定同时保存通知版本、决定 ID 和目标版本。
- [x] 将模型导出到 Mold 模型集合，不改变旧模型字段。
- [x] 编写与 ORM 一致的活动迁移源；本轮不执行正式数据库迁移，隔离测试库可按测试夹具验证迁移源。
- [x] 运行 schema 静态测试和 `python -m compileall` 验证模型可导入。

## 任务 2：中标确认事件幂等消费

**文件：**
- 创建：`backend/domain_packs/mold/erp/commercial/bid_start_workflow.py`
- 修改：`backend/domain_packs/mold/erp/commercial/document_workflow.py`
- 测试：`tests/test_bid_start_workflow_unit.py`、`tests/test_bid_start_consumer.py`、`tests/test_bid_event_identity.py`

- [x] 先写测试：未确认分类不能创建匹配；确认后的 `bid_notice.confirmed` 创建一条 `PENDING_MATCH`；同一事件重放返回同一记录。
- [x] 运行目标测试确认失败原因是缺少事件消费函数或模型。
- [x] 实现 `consume_confirmed_bid_notice(db, user, event_id)`：校验事件动作、文件版本、分类 ID、接收记录、证据快照、分类器版本、操作 ID 和文件访问权；不创建项目关系、不创建开工通知。
- [x] 记录安全的事件引用、文件版本和分类快照摘要，不复制正文。
- [x] 增加 Skill/Tool 的显式中标事件消费动作；禁止后台隐式自动匹配项目。该动作只在 `mb0d0e000014` 部署后可用于正式库。
- [x] 运行任务 2 的串行单元测试，确认重复消费不增加记录。

## 任务 3：项目匹配和中标接收版本确认

**文件：**
- 修改：`backend/domain_packs/mold/erp/commercial/bid_start_workflow.py`
- 创建：`backend/domain_packs/mold/tools/erp/commercial/bid_start_tools.py`
- 修改：`backend/domain_packs/mold/tool_gateway.py`、`backend/domain_packs/mold/proposal_handlers.py`
- 测试：`tests/test_bid_start_tools.py`、`tests/test_bid_match_api_unit.py`

- [x] 先写并运行基础测试：状态、版本、已有项目冲突和请求字段校验。
- [x] 添加查询待匹配 Tool，只返回权限允许的安全字段，并排除已经消费的确认事件。
- [x] 添加人工匹配准备 Tool，要求 `expected_row_version`、项目版本、来源依据和确认幂等边界。
- [x] 增加已有 `BidIntakeRevision` 的人工确认绑定，校验项目归属、版本和文件权限；不绕过既有接收资料校验。
- [x] 匹配确认只将记录推进到 `MATCHED`，接收版本确认后才推进到 `INTAKE_CONFIRMED`，不得自动创建 `internal_start`。
- [x] 运行 PostgreSQL 私有 schema 事务测试；正式库尚未执行 `mb0d0e000014`，未启动正式库集成。

## 任务 4：开工通知版本和部门独立回执

**文件：**
- 修改：`backend/domain_packs/mold/erp/commercial/bid_start_workflow.py`
- 创建：`backend/domain_packs/mold/erp/project/start_notice_workflow.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/bid_start_tools.py`
- 测试：`tests/test_start_notice_creation.py`、`tests/test_start_notice_state.py`、`tests/test_bid_start_tools.py`

- [x] 先写测试：只有已确认中标接收版本可创建 `DRAFT`；部门回执互不覆盖。
- [x] 实现 `create_start_notice`，冻结项目、客户、内部模具和中标接收版本摘要。
- [x] 初始化五类部门独立回执；缺少收件人不伪造业务承接。
- [x] 实现部门回执：`ACCEPTED`、`RETURNED`、`NEED_INFO`，每次带部门自己的版本和依据。
- [x] 接入部门 Outbox 通知，保存接收人快照和投递事件；业务回执仍独立保持 `PENDING`，送达不视为承接。
- [x] 运行 PostgreSQL 私有 schema 集成测试，确认未回执时阻止项目最终通过。

## 任务 5：项目部最终决定

**文件：**
- 修改：`backend/domain_packs/mold/erp/project/start_notice_workflow.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/bid_start_tools.py`
- 测试：`tests/test_start_notice_state.py`、`tests/test_bid_to_start_db_integration.py`

- [x] 先写测试：缺少部门回执时不能通过。
- [x] 实现 `PROJECT_ACCEPTED`、`FULL_OUTSOURCE_ACCEPTED`、`REJECTED`、`RETURNED` 决定，并保存决定人、依据、通知版本和部门回执快照。
- [x] 只有两种 accepted 决定允许进入后置关联准备；拒绝和退回保持可追溯。
- [x] 在新开工材料中加入只读的旧 `internal_start` 兼容引用，明确标记 `compatibility_only`；新合同/清单绑定仍只接受新项目决定。
- [x] 运行 PostgreSQL 私有 schema 集成测试，并验证客户开工条件及报价承接门禁。

## 任务 6：合同及核算清单后置绑定

**文件：**
- 创建：`backend/domain_packs/mold/erp/commercial/post_start_binding.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/contract_tools.py`
- 修改：现有核算清单服务或新增 `backend/domain_packs/mold/erp/commercial/checklist_binding.py`
- 测试：`tests/test_post_start_binding_state.py`、`tests/test_post_start_binding_service.py`、`tests/test_bid_to_start_db_integration.py`

- [x] 先写测试：没有 accepted 项目决定时只能预览，正式绑定返回 `START_DECISION_REQUIRED`；通知版本或目标版本变化返回 `VERSION_CONFLICT`。
- [x] 实现销售合同版本绑定，校验项目、合同版本和内部模具逐行快照。
- [x] 接入 ERP `production_cost_sheet_file` 只读绑定引用和 `file_hash` 外部版本指纹；Agent 不复制清单内容、不修改 ERP 状态，非 ERP 稳定档案引用仍返回 `CHECKLIST_BINDING_NOT_READY`。真实 ERP 服务联调仍待单独验收。
- [x] 正式绑定写入 `PostStartBinding`，保证同一目标版本不可重复绑定。
- [x] 运行 PostgreSQL 私有 schema 合同绑定测试。

## 任务 7：组合回归和只读验收

**文件：**
- 修改：`docs/DEVELOPMENT_STATUS.md`
- 修改：`docs/REQUIREMENTS_TRACEABILITY.md`
- 测试：`tests/test_bid_start_workflow_unit.py`、`tests/test_bid_start_consumer.py`、`tests/test_start_notice_creation.py`、`tests/test_start_notice_state.py`、`tests/test_post_start_binding_state.py`、`tests/test_post_start_binding_service.py`、`tests/test_bid_start_tools.py`、`tests/test_bid_start_db_integration.py`、`tests/test_bid_to_start_db_integration.py`

- [x] 串行运行新增测试，不运行前端构建、浏览器或真实供应商请求。
- [x] 串行完成既有中标、报价承接、开工分发、合同 OCR、文件权限、Agent API 和 `test_document_streaming.py` 回归；未运行前端或真实供应商请求。
- [x] 对 `127.0.0.1:55432/moldpilot_test` 执行只读检查；当前隔离测试库 Mold 版本为 `mb0d0e000014`，新工作流五张表已存在；未对 `agent_db` 写入业务数据，正式库迁移仍未执行。
- [x] 对本批后端、测试、契约和文档路径执行 `git diff --check`；当前工作区另有未纳入本批的 `web/src/style.css` 文件尾部空行，完整工作区检查需单独清理。
- [x] 更新开发状态，明确仅有迁移源、自动化测试和页面/业务验收边界。
