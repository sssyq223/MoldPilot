# MoldPilot 本地修改能力与第 11 章 Skill/Tool 设计

## 1. 最终范围

本设计只覆盖两部分：

1. 当前工作区已经存在的本地修改代码和本地新增代码；
2. 第 11 章“设变与工程联络单”FR-078～FR-090 中允许在本地范围实施的内容。

“本地修改代码”以当前工作区为准：

- `git diff --name-only` 中的文件；
- `git ls-files --others --exclude-standard` 中的本地新增文件；
- 为本范围新增的 Skill、Tool、契约、测试和文档文件。

远程 `HEAD` 中未被本地修改的代码不修改、不删除、不重命名、不回滚。特别是远程原有的工程联络、设变、审批和 ERP 目录文件，除非它们已经出现在本地修改清单中，否则不得直接编辑。

本设计不扩大到其他尚未完成的产品功能，也不以需求规格书中的全部 FR-001～FR-118 作为本轮实现范围。

## 2. 当前本地能力封装范围

本地业务代码中的业务能力都必须通过 Skill/Tool 进入；基础设施代码不伪装成 Skill/Tool。

### 2.1 销售合同文档接收与复核

Skill：`sales_contract_intake`

本地 Tool：

- `query_uploaded_files`
- `prepare_document_intake`
- `query_document_intake`
- `prepare_document_type_confirmation`
- `prepare_document_ocr_retry`
- `query_sales_contract_intake`
- `prepare_sales_contract_intake_review`
- `prepare_sales_contract_from_intake`

覆盖当前本地已实现的 PDF 接收、预分类、OCR 状态、人工类型确认、字段复核和本地合同草稿审批。PaddleOCR、文档模型和 Worker 只是内部实现，不作为独立业务 Tool 暴露。

### 2.2 中标到开工通知

Skill：`bid_to_start_notice`

本地 Tool：

- `query_confirmed_bid_notices`
- `prepare_bid_notice_match`
- `prepare_bid_project_match`
- `prepare_bid_intake_confirmation`
- `prepare_start_notice`
- `query_admin_start_notices`
- `prepare_admin_start_notice_update`
- `prepare_admin_start_notice_decision`
- `prepare_admin_start_department_dispatch`
- `prepare_admin_start_department_ack`
- `prepare_department_ack`
- `prepare_project_start_decision`
- `prepare_contract_match_confirmation`
- `prepare_post_start_binding`

这些 Tool 只操作本地项目、模具、开工通知、部门回执、合同候选和文件版本。不得调用 ERP 接口或绑定 ERP 核算清单。

### 2.3 本地模型配置

只有当前本地修改代码已经提供的模型配置能力纳入本轮 Skill/Tool 封装，不新增额外模型管理功能。

建议统一为管理员 Skill：`model_provider_configuration`，覆盖本地模型目录、供应商检测、模型档位、默认模型和文档专用模型引用。配置 Tool 必须保留管理员权限、revision 冲突、Key 不回显和 Proposal 确认规则。

### 2.4 其他本地修改业务能力

对当前本地修改或新增代码逐项建立映射：

- 有用户业务意图、业务对象和业务结果的，必须登记为 Skill/Tool；
- 仅提供 Worker、OCR、缓存、配置、迁移、启动或存储能力的，作为内部支撑保留；
- 不为了“全部封装”而给测试、脚本、前端组件或数据库迁移新增 Skill。

## 3. 第 11 章本地实现范围

### 3.1 设变承接

覆盖 FR-078～FR-081：

- 客户设变、内部修模改模、委外设变分类；
- 客户设变内部执行或委外执行；
- 收费/免费、是否新增合同、执行范围分别记录；
- 无合同小设变仍检查开工和审批依据；
- 已有模具复用内部模具号并保留客户模号历史；
- 首次外部模具设变进入人工报价和新业务承接分支；
- 不因模号、订单号或合同号相似自动合并。

建议本地 Skill：`engineering_change_intake`

建议本地只读/准备 Tool：

- `query_local_change_context`
- `prepare_local_change_intake`
- `prepare_local_change_association`
- `prepare_local_change_acceptance`

这些 Tool 只生成本地 Proposal；人工确认后写入本地设变记录、模具关系、客户模号历史和承接依据。

### 3.2 工程联络单

覆盖 FR-082～FR-084：

- 统一记录客户设变、设计异常、组立异常、加工异常、采购异常、质检异常、试模异常、外协不良、降本和制程改善；
- 保存客户、项目、模具、料品、申请日期、问题来源、责任部门、变更类别、紧急程度、说明、对策、要求/完成时间、工时、金额、附件、版本和审批记录；
- 原件、操作记录、执行结果、复检结果不可覆盖；
- 额外工时和金额保留本地财务影响线索。

建议本地 Skill：`engineering_contact_collaboration`

Tool 原则：查询、创建、补充记录、责任部门、事项、分派、反馈、附件、方案、复验和关闭均必须走本地 Tool；写操作均生成 Proposal，不能直接写库。

### 3.3 评估、落实和关闭

覆盖 FR-085～FR-090：

- 发起后向项目负责人、设计和责任部门发送本地通知；
- 方案审批、退回、重新提交均保留版本；
- 影响项必须明确图纸、物料、采购事项、在制任务、供应商任务或计划节点，以及继续、暂停、取消、返工、重新下达动作；
- 未受影响事项继续原计划；
- 批准后只更新本地正式版本、任务、节点计划和通知；
- 方案批准不等于执行完成；
- 执行反馈、复检/复验、人工关闭分别记录；
- 处理人不能复验自己的结果；
- 关闭前检查所有有效影响事项均已执行并独立复验合格。

若涉及远程未修改文件，不能直接编辑；应通过本地新增适配层或本地已修改注册入口完成能力暴露。无法在本地范围安全实现的部分必须报告，不得越界修改远程代码。

## 4. ERP 处理边界

本轮不做“全项目 ERP 清理”，只处理本地修改代码和第 11 章本地实现中的 ERP 依赖：

- 本地业务 Tool/Skill 不调用 ERP HTTP、ERP MCP、ERP 数据库或 ERP 文件目录；
- 本地新增流程不保存 ERP 原生 ID、ERP Token、ERP 用户身份或 ERP 操作回执；
- 本地能力目录不新增 ERP Tool/Skill；
- 本地后置绑定只能绑定 MoldPilot 自有对象；
- 远程未修改的 ERP 源文件保留原样，不触碰。

因此本轮验证只能声明“本地修改范围内不调用 ERP”，不能声明远程 ERP 源文件已删除。

## 5. Skill/Tool 通用契约

每个纳入范围的业务 Skill 必须声明：

1. 触发条件和业务边界；
2. 首轮只读 Tool；
3. 参数不足、多候选、权限不足和版本冲突处理；
4. 可用的 `prepare_*` Tool；
5. Proposal 和本人确认要求；
6. 执行前的权限、版本、附件所有权和业务状态复核；
7. 建议、审批、执行、复验和关闭的分离规则；
8. 幂等、失败恢复和审计要求；
9. 不接受任意外部 URL、外部 ID 或外部 Token。

基础设施 Tool（如已有运行就绪查询）只有在当前文件属于本地修改范围时才纳入本轮，不扩展其业务范围。

## 6. 文件修改规则

实施前先生成允许修改文件白名单：

- 当前 `git diff` 文件；
- 当前本地新增文件；
- 本轮新增 Skill/Tool/契约/测试/文档文件。

每次编辑后执行：

- `git diff --name-only` 范围检查；
- 未修改远程文件检查；
- 本地新增文件登记检查；
- 业务 Tool/Skill 注册链检查。

如果第 11 章实现需要修改远程未改文件，立即停止并报告，不自动绕过限制。

## 7. 验证边界

不运行前端构建，不执行数据库迁移，不联调 ERP。

验证只覆盖：

- 本地业务 Skill/Tool 是否出现在能力目录；
- 参数 Schema、权限和 Proposal 是否正确；
- 未确认前不写库；
- 确认后本地数据库、审计、通知和幂等回执是否正确；
- 第 11 章 FR-078～FR-090 的分类、模具关联、版本、影响项、执行、复验和关闭门禁；
- 远程未修改文件没有被改动。

## 8. 后续顺序

1. 固化本地修改文件白名单。
2. 盘点本地业务代码已有 Skill/Tool 和缺口。
3. 仅对本地修改能力补齐 Skill/Tool 注册和契约。
4. 在本地允许范围实现第 11 章缺口。
5. 增加定向测试和 diff 越界检查。
6. 不进入其他未完成业务能力。
