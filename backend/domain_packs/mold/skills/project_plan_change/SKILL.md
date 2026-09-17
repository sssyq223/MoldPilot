# 项目计划与计划变更

当用户明确要求创建项目基线计划，或调整、重编、顺延、补充已有项目计划节点时使用。该 Skill 只封装“查询证据 → 准备计划提案 → 本人确认 → BPM 审批”的工作流，不直接修改计划。

使用规则：

- 先调用 `query_project_plan_context`，用项目 ID、项目编号/名称、计划单号或任务关键字定位项目。返回多候选时要求用户指定项目 ID。
- 当前没有有效计划且用户要求建立初始计划时，必须使用查询返回的真实 `project_id`、项目版本、完整任务清单和 `baseline_workflow_options.id` 调用 `prepare_project_plan_baseline`。项目未正式开工、已有有效计划或已有待处理计划申请时不得准备基线计划。
- 调用 `prepare_project_plan_baseline` 时，应提交完整基线任务列表；依赖和日期必须满足后端校验。本人确认后才会创建 `project_plan` 业务材料并提交 Agent BPM；审批生效前不会下达 ERP 执行任务，也不会把内部计划当成客户承诺交期。
- 准备变更前必须取得查询返回的真实 `project_id`、项目版本、当前有效计划 `previous_id`、完整任务清单和 `workflow_options.id`。不得只凭自然语言线索、截图文字或历史对话生成变更，也不得猜测审批流程 ID。
- 调用 `prepare_project_plan_change` 时，应提交变更后的完整任务列表，而不是只提交差异；已完成任务不能重排，已开工任务不能删除，依赖和日期必须满足后端校验。
- `prepare_project_plan_change` 只生成待本人确认的 proposal。本人确认后才会创建 `plan_change` 业务材料并提交 Agent BPM；审批生效前原计划、任务日期和客户承诺交期都不改变。
- 计划变更已生效后，如果用户表示“本部门已核对/确认影响”，必须先用 `query_project_plan_context` 读取 `department_confirmations`，再用其中真实的 `id` 与 `version` 调用 `prepare_plan_department_confirmation`。不得凭部门名称或自然语言自行构造确认项。
- `prepare_plan_department_confirmation` 只生成本部门影响确认 proposal。本人确认后仅记录该确认项已核对，不修改项目计划、不替代计划变更审批、不写入 ERP 执行进度。
- 涉及客户交期变化时，不要把内部计划顺延当作客户确认。必须提示需要独立客户确认依据或后续业务流程。
