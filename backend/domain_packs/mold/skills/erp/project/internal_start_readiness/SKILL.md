# 正式开工条件核对

当用户询问某个项目是否能正式开工、是否已经正式下达开工通知、承接与开工是否齐备、合同晚到是否阻塞开工、或开工前还缺什么依据时，先使用 `query_internal_start_readiness`。

使用规则：

- 先用项目 ID、项目编号/名称、承接单号、开工通知单号、合同号等线索定位项目；返回多候选时要求用户指定项目 ID。
- 只依据工具返回的 `readiness`、`customer_start_conditions`、承接记录、开工通知、合同和计划上下文回答。不要把“报价承接”“销售合同存在”“项目计划存在”任一事实单独说成已正式开工。
- 使用 `business_state` 说明“待承接确认→已承接待开工条件→待正式下达→已正式下达→待计划审批→执行中”的当前位置；拒单、暂停、终止和关闭是独立结果，不要强行映射成正常推进阶段。
- 正式开工生效后，使用 `department_handoffs` 核对设计、采购、生产制造、装配和财务五类项目角色的交接记录与投递状态。`UNASSIGNED` 表示角色配置缺失，`FILTERED_BY_CURRENT_PERMISSION` 表示消息已消费但当前授权不允许生成通知；不得把二者说成已通知。
- 使用 `formal_start_material` 核对本次正式通知冻结的外部订单、客户、内部模具、机型/物料、项目、合同状态、开工日期、交期及中标接收版本；不存在冻结材料时明确指出历史资料缺口，不从当前值反推过去通知内容。
- 合同晚到不必然阻塞开工；无生效销售合同时调用 `prepare_internal_start` 必须填写 `expected_contract_date`。使用 `contract_follow_up` 区分待到、今日到期、逾期、缺实际到达日期、缺附件和已收到；`passive_reminder` 只是查询触发的催补提示，不代表系统已签订合同。
- 新模正式开工前必须已有 ERP 唯一内部模具号关联；已有模具设变必须复用中标接收记录中的原内部模具号。工具阻断时先补齐 ERP 模具关系，不在 Agent 本地伪造模具号。
- 客户工艺方案人工确认、外部订单号、客户开工日期、客户交期和外部开工通知附件必须在同一中标接收记录中形成可核对事实；承接决定不能替代这些客户开工条件。
- 如果用户明确要求正式下达开工，且 `query_internal_start_readiness` 返回 `readiness.can_prepare_start_from_known_facts=true` 与可用审批流程，才能调用 `prepare_internal_start`。必须使用查询返回的真实 `project.id`、`project.row_version`、已生效承接记录 `latest_acceptance.id`、当前 `customer_start_conditions.current_revision_id` 和 `workflow_options.id`，不能凭自然语言、截图或历史对话构造。
- `prepare_internal_start` 只生成待本人确认的 proposal。本人确认后才创建正式开工通知材料并提交 Agent BPM；审批生效前不改变项目状态，不下达设计/采购/生产/装配/试模任务，也不把销售合同或承接记录单独当成已正式开工。
- 审批生效时只形成项目状态、五类部门交接回执和站内通知，不创建设计、采购、生产、装配或试模执行任务。部门后续任务仍必须依据已生效项目计划或 ERP 权威执行记录。
- 只读核对时不得创建开工通知、不下达设计/采购/生产/装配/试模任务，不替代项目负责人和相关部门人工确认。
