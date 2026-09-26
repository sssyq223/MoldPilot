# 中标邮件自动触发超级管理员开工通知工作流实现计划

> **面向 AI 代理的工作者：** 当前主工作区内联执行；禁止子智能体、worktree、提交和正式数据库迁移。步骤使用复选框跟踪进度。

**目标：** 人工确认上传文件为 `BID_NOTICE` 后，通过 `bid_to_start_notice` Skill 和 Tool 自动创建仅供超级管理员处理的内部开工通知草稿；超级管理员补充信息并确认内部承接、整套委外或拒绝；承接/委外后合同上传自动生成匹配候选，正式关联仍需人工确认。

**架构：** 保留 `DocumentIntake`、`bid_notice.confirmed` 和现有 Agent Proposal/Confirmation 机制。新增独立的超级管理员开工草稿、追加式草稿修订和合同匹配候选事实，不放宽现有正式 `StartNotice` 与 `PostStartBinding` 的版本门禁。事件触发只生成待处理草稿和超级管理员通知，不自动匹配项目、不自动批准、不自动正式绑定合同。

**技术栈：** FastAPI、SQLAlchemy、PostgreSQL、现有 Skill/Tool Gateway、Agent Outbox/Inbox、Pydantic、串行 pytest。

**规格：** 当前会话已确认的“中标邮件 → Skill/Tool → 超级管理员草稿 → 人工决定 → 合同候选匹配”流程。

## 全局约束

- 保留所有已有未提交修改；不创建 worktree、不提交。
- 不执行正式数据库迁移；新增迁移源只供隔离测试和后续审查。
- 不向普通项目部门创建回执或发送业务承接通知；初始通知仅发送超级管理员。
- 不自动确认项目承接、整套委外、拒绝或合同正式关联。
- 审计和 Tool 返回不输出 PDF 正文、模型原文、提示词、Key 或 Token。
- 测试仅使用 `127.0.0.1:55432/moldpilot_test`，不得触碰 `agent_db` 业务数据。

## 任务 1：超级管理员开工草稿模型和迁移源

**文件：**
- 创建：`backend/domain_packs/mold/erp/project/admin_start_workflow_models.py`
- 修改：`backend/domain_packs/mold/models.py`
- 创建：`backend/domain_packs/mold/alembic_domain/versions/mb0d0e000015_admin_start_workflow.py`
- 测试：`tests/test_admin_start_workflow_schema.py`

- [x] 先写模型字段、状态、事件唯一键、修订唯一键和候选唯一键测试。
- [x] 运行测试确认缺少模型导致失败。
- [x] 添加 `AdminStartNoticeDraft`、`AdminStartNoticeRevision`、`StartContractMatchCandidate` 三类模型。
- [x] 保存来源事件、文件版本、接收记录、当前结构化快照、超级管理员确认决定和版本。
- [x] 候选只保存合同目标引用、目标版本、匹配证据摘要和候选状态，不直接写正式绑定。
- [x] 将模型导出到 Mold 模型集合并编写活动迁移源，不执行正式迁移。
- [x] 运行 schema 测试和 compileall。

## 任务 2：BID_NOTICE 事件自动触发 Skill/Tool 草稿

**文件：**
- 创建：`backend/domain_packs/mold/skills/erp/commercial/bid_to_start_notice/orchestrator.py`
- 修改：`backend/domain_packs/mold/erp/commercial/document_workflow_api.py`
- 修改：`backend/domain_packs/mold/erp/commercial/bid_start_workflow.py`
- 创建：`backend/domain_packs/mold/tools/erp/commercial/admin_start_notice_tools.py`
- 修改：`backend/domain_packs/mold/tool_gateway.py`、`backend/domain_packs/mold/proposal_handlers.py`
- 测试：`tests/test_admin_start_workflow_unit.py`、`tests/test_admin_start_workflow_tools.py`

- [x] 先写测试：BID_NOTICE 确认后只生成一条管理员草稿和管理员 Outbox；事件重放不重复生成；非 BID_NOTICE 不触发。
- [x] 实现事件到 `bid_to_start_notice` Skill 的显式编排入口，使用事件 ID 幂等。
- [x] 增加只读查询 Tool 和超级管理员草稿 Tool；自动动作不得直接形成最终承接决定。
- [x] 只向活跃超级管理员发送通知快照，不向五类项目部门生成回执。
- [x] 草稿生成失败进入可追溯 NEEDS_REVIEW，不吞掉异常、不自动重试业务决定。
- [x] 运行目标单元测试。

## 任务 3：超级管理员补充、确认和版本留痕

**文件：**
- 修改：`backend/domain_packs/mold/tools/erp/commercial/admin_start_notice_tools.py`
- 修改：`backend/domain_packs/mold/proposal_handlers.py`
- 修改：`backend/domain_packs/mold/tool_gateway.py`
- 测试：`tests/test_admin_start_workflow_tools.py`、`tests/test_admin_start_workflow_db.py`

- [x] 先写测试：普通用户不能读写；超级管理员可准备草稿修订；确认前不改变最终状态；旧版本提交返回 `VERSION_CONFLICT`。
- [x] 实现查询、准备补充、确认决定三个 Tool。
- [x] 决定只允许 `INTERNAL_ACCEPTED`、`FULL_OUTSOURCE_ACCEPTED`、`REJECTED`，全部要求依据。
- [x] 每次补充和确认追加 `AdminStartNoticeRevision`，并记录操作人、依据、版本和事件引用。
- [x] 承接/委外确认后进入 `READY_FOR_CONTRACT_MATCH`；拒绝进入 `REJECTED`；不创建普通部门回执。
- [x] 运行 PostgreSQL 私有 schema 事务测试。

## 任务 4：合同上传后的 Skill/Tool 自动匹配候选

**文件：**
- 创建：`backend/domain_packs/mold/erp/commercial/contract_match_workflow.py`
- 修改：`backend/app/document_worker.py`
- 修改：`backend/domain_packs/mold/tools/erp/commercial/document_event_tools.py`
- 修改：`backend/domain_packs/mold/proposal_handlers.py`、`backend/domain_packs/mold/tool_gateway.py`
- 测试：`tests/test_admin_start_contract_match.py`

- [x] 先写测试：只有承接/委外草稿能生成候选；合同编号、项目、客户、模具和订单证据进入候选摘要；重复事件不重复候选。
- [x] 在销售合同识别完成后通过 `contract.ocr.ready` 事件触发合同匹配 Tool。
- [x] 只生成 `PROPOSED` 候选，不自动写 `PostStartBinding`。
- [x] 候选保存目标版本、指纹、证据来源和匹配原因；冲突或多候选保留为待人工核对。
- [x] 提供超级管理员人工确认候选的 Tool；当前只确认候选，不绕过后续正式绑定门禁。
- [x] 运行隔离 PostgreSQL 合同匹配测试。

## 任务 5：组合回归和文档

**文件：**
- 修改：`backend/domain_packs/mold/skills/erp/commercial/bid_to_start_notice/SKILL.md`
- 修改：`contracts/bid-to-start-notice.skill.json`
- 修改：`docs/DEVELOPMENT_STATUS.md`、`docs/REQUIREMENTS_TRACEABILITY.md`
- 测试：`tests/test_admin_start_workflow_schema.py`、`tests/test_admin_start_workflow_tools.py`、`tests/test_admin_start_workflow_db.py`、`tests/test_admin_start_contract_match.py`

- [x] 更新 Skill 触发前置、自动草稿、超级管理员确认和合同候选匹配边界。
- [x] 更新 Tool 契约，明确自动阶段不等于业务批准，正式合同关联必须人工确认。
- [ ] 串行运行新增测试和既有中标/合同接收/文件权限/Agent API 回归。
- [x] 运行 Python compileall 和本批路径 `git diff --check`。
- [x] 不运行前端构建、浏览器或真实供应商请求；不执行正式迁移。
