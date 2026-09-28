# 工程变更申请联络单办理事项自动 Proposal 与选择弹窗设计

## 1. 范围与现状

本设计针对工程联络单文档已经完成以下步骤后的下一阶段：

```text
文档分类确认
→ 工程联络 Skill 激活
→ prepare_contact_create Proposal 确认
→ 原始附件关联 Proposal 确认
→ 自动生成办理事项 Proposal
→ 人工填写/选择完整表单并确认
→ Tool 批量创建相关单位办理事项
```

工程联络单 `ContactCase` 已存在时，不得再次创建 `ContactCase`。当前实现已经具备：

- 分类确认后自动创建工程联络 Agent Run；
- `document_engineering_contact_intake` Skill；
- `prepare_contact_create`、`prepare_contact_attach`、`prepare_contact_task`；
- Proposal 通用确认卡；
- `prepare_contact_attach` 确认后的 Run 续办机制。

当前缺口是：附件关联确认后没有可靠地自动生成适合纸质“工程变更申请联络单”的批量办理事项 Proposal；现有 Proposal 卡也不能承载图片中的多单位、责任人、日期、类别和工艺评估字段。

## 2. 目标

1. 原始附件关联确认后，工程联络 Skill 自动调用新的批量办理事项 Tool。
2. Tool 生成待确认 Proposal，不直接创建事项。
3. 前端检测到该 Proposal 后自动打开大尺寸表单弹窗。
4. 弹窗完整覆盖图片中的字段和选项，并允许人工补充、修改和选择。
5. 用户确认后，Tool 一次性创建多个相关单位办理事项，并保留完整表单快照。
6. 既有单事项 `prepare_contact_task` 继续兼容普通会话，不改变既有生命周期门禁。
7. 不调用 ERP HTTP/MCP、远程数据库或远程文件目录，不执行数据库迁移。

## 3. 表单字段契约

### 3.1 基本信息

已创建联络单中的以下字段在弹窗中只读展示，并允许在未确认的 Proposal 草稿中人工修正后重新准备：

- 客户
- 责任部门
- 申请日期
- 完成日期
- 品名
- 模具编号
- 产品料号

其中系统内部兼容字段仍使用现有 `ContactCase` 字段；表单原始语义保存在 Proposal/表单快照中，不把兼容字段当作完整纸质表单的替代。

### 3.2 完成类型

使用单选：

- 一般：`NORMAL`
- 急件：`URGENT`
- 特急件：`CRITICAL`

这三个内部值复用现有紧急程度兼容枚举，但前端显示使用图片中的“完成类型”文案，不显示为“紧急程度”。

### 3.3 变更类别

使用多选，选项与图片一致：

- 客户变更：`CUSTOMER_CHANGE`
- 设计异常：`DESIGN_ISSUE`
- 组立异常：`ASSEMBLY_ISSUE`
- 加工异常：`MACHINING_ISSUE`
- 外协不良：`OUTSOURCE_DEFECT`
- 降低成本：`COST_REDUCTION`
- 制程改善：`PROCESS_IMPROVEMENT`
- 其它：`OTHER`

必须至少选择一项。现有 `ContactCase.problem_source` 只有单值时，使用人工选择的首要类别作为兼容投影；完整多选值只从表单快照读取，不静默丢弃。

### 3.4 变更内容

- 变更说明
- 对策

二者均为人工可编辑多行文本；OCR 候选只能作为初始值，不能自动视为确认事实。

### 3.5 相关单位与办理事项

支持多行，每行对应一个待创建的 `ContactTask`：

- 流程单位/责任部门
  - 设计部
  - 机加部
  - 组立部
  - 品质部
  - 生管
  - 项目部
  - 业务部
  - 生技部
  - 冲压部
  - 以及当前数据库中实际启用且有权限的其它部门
- 具体责任人
- 完成时间
- 作业内容
- 工时
- 金额
- 备注

部门和人员选项必须来自当前本地数据库和权限校验，不能由模型自行生成。签名字段不伪造；系统中的签名/确认状态由后续办理和审批事件产生。

### 3.6 计价

- 计价说明
- 金额合计
- 币种

金额、币种必须成对填写；金额合计可由明细金额计算并允许人工核对。不得把 OCR 中无法确认的金额直接当作正式金额。

### 3.7 审批展示

弹窗显示图片中的“申请、审核、批准”三个角色节点，但不让模型或 OCR 填写签名，也不在本 Tool 中伪造审批结果。正式处理方案审批仍沿用现有 `prepare_contact_resolution` 和 BPM 确认链。

## 4. Skill 与 Tool 设计

### 4.1 新 Tool

新增 `prepare_contact_form_tasks`，职责是准备工程变更申请联络单的批量办理事项 Proposal。

输入核心结构。自动触发阶段允许 `form` 省略或为 `null`，只生成空表单 Proposal；弹窗补齐后重新准备时 `form` 必须完整：

```json
{
  "case_id": "...",
  "revision": 3,
  "form": {
    "customer": "...",
    "responsible_department_id": "...",
    "application_date": "2026-09-27",
    "completion_date": "2026-10-02",
    "completion_type": "URGENT",
    "change_categories": ["DESIGN_ISSUE", "PROCESS_IMPROVEMENT"],
    "change_description": "...",
    "countermeasure": "...",
    "related_units": [
      {
        "department_id": "...",
        "assignee_id": "...",
        "completion_date": "2026-09-29",
        "work_content": "...",
        "hours": "4.00",
        "amount": "0.00",
        "currency": "CNY",
        "remark": "..."
      }
    ],
    "pricing_note": "...",
    "total_amount": "0.00",
    "currency": "CNY"
  }
}
```

第一阶段由 Skill 自动生成一个待选择的初始 Proposal，`form` 可以为空，不得确认执行。弹窗补齐后调用同一 Tool 的受控“重新准备”入口，后端重新校验并生成完整 Proposal；不由前端直接写数据库。若最终 Proposal 仍缺少必填字段，Tool 必须拒绝创建事项。

### 4.2 自动触发

修改工程联络文档 Skill：

1. `prepare_contact_attach` 确认成功后重新查询 `ContactCase` 当前版本。
2. 只对 `ONLINE` 联络单准备 `prepare_contact_form_tasks`。
3. `HISTORY` 联络单不得生成线上办理事项，只提示补录线下过程。
4. 自动触发失败时保留原附件关联事实，不重复创建联络单或附件。
5. 生成 Proposal 后停止，等待弹窗中的本人选择和确认。

修改 `agent_resume.py` 的工程联络续办映射：

```text
prepare_contact_create → prepare_contact_attach
prepare_contact_attach → prepare_contact_form_tasks
```

不得把批量办理事项直接并入原始附件关联事务，也不得因为模型回复文字而认为事项已经创建。

## 5. Proposal 确认与幂等

- Proposal 仍由 `contact.execute` 处理。
- 表单修订必须带原 Proposal 的 Step、Case revision、授权指纹和内容哈希。
- 后端生成修订后的 Proposal 输入和展示快照，前端不能自行伪造 `proposal_hash`。
- 未完成必填字段、部门停用、人员无权限、版本变化、授权变化或附件变化时拒绝确认并要求重新准备。
- 同一请求标识和同一表单内容重复确认返回同一回执，不重复创建任务。
- Case 已关闭、历史补录、已有相同表单版本或存在待处理的相同批次时不得重复创建。

## 6. 持久化边界

本轮不新增数据库迁移，原因是当前正式本地数据库与工作区迁移版本存在明确边界，且既有 `ContactCase`/`ContactTask` 已承载生命周期和权限门禁。

确认执行时：

1. 使用现有 `ContactTask` 创建各相关单位办理事项。
2. 使用现有字段做兼容投影：部门、责任人、事项标题、作业内容、金额、来源和状态。
3. 将完整图片表单作为不可变 `ContactRecord.detail`/审计快照保存，包含表单版本、所有单位行、工艺评估、计价字段、来源文件版本和 Proposal 哈希。
4. 现有 `ContactCase` 的单值字段只作为查询兼容投影，多选变更类别和完整表单以快照为准。
5. 不把“已创建事项”说成已完成、已审批或已复验。

如果后续需要以 SQL 级结构化方式查询每个表单字段，再单独提出数据库设计和迁移审批；本需求不在正式库执行迁移。

## 7. 前端交互

### 7.1 自动弹窗

扩展现有 Proposal 展示机制：

- 当待确认 Proposal 为 `contact/form_tasks` 时，自动打开 `EngineeringContactTaskDialog`。
- 页面刷新或重新进入会话时，如仍存在未处理 Proposal，可以再次打开。
- 用户关闭弹窗不会丢弃 Proposal，底部保留“查看并选择”入口。
- 普通 `prepare_contact_task` 仍使用现有通用 Proposal 卡，不受影响。

### 7.2 弹窗布局

使用大尺寸、可滚动、带键盘焦点管理的弹窗，分区为：

1. 来源文件与联络单版本
2. 基本信息
3. 完成类型与变更类别
4. 变更说明与对策
5. 相关单位办理事项表格
6. 工艺评估/计价
7. 申请、审核、批准流程说明
8. Proposal 哈希、版本和确认提示

弹窗支持新增、删除和编辑相关单位行；至少保留一行有效办理事项。责任人候选随部门变化刷新，已选人员不再属于部门或失去权限时立即标记错误。

### 7.3 确认流程

```text
自动弹出表单
→ 用户补充/修改/选择
→ 后端重新准备完整 Proposal
→ 弹窗展示最终影响范围
→ 本人确认执行
→ 批量创建 ContactTask
→ 原有 Skill 继续返回业务回执
```

不在前端直接调用 `/api/contacts/{id}/tasks`，不绕过 Tool Proposal 和 HumanIntent。

## 8. 失败处理

- OCR 字段缺失：保留空值并要求人工补充，不自动猜测。
- 没有可选项目/责任部门/责任人：弹窗明确显示“无法确认”，不能提交。
- 部门或人员权限变化：重新加载候选并重新准备 Proposal。
- Case revision 变化：提示当前联络单已更新，放弃旧表单确认。
- Tool 批量创建中途失败：事务整体回滚，不留下部分责任事项。
- 重复上传或重复 Proposal：使用文件 SHA256、Case revision、Proposal 哈希和请求标识阻止重复创建。
- HISTORY 模式：只允许保存线下过程和附件，不创建线上责任事项。

## 9. 预计修改文件

### 后端

- `backend/domain_packs/mold/tools/erp/change/contact_tools.py`
  - 新增批量表单输入模型、Proposal 展示、部门/人员候选和受控修订入口。
- `backend/domain_packs/mold/erp/change/contacts.py`
  - 新增批量事项确认服务、人员资格校验和表单快照记录。
- `backend/domain_packs/mold/proposal_handlers.py`
  - 注册 `prepare_contact_form_tasks`。
- `backend/domain_packs/mold/tool_gateway.py`
  - 注册 Tool，加入工程联络文档 Skill 可选工具。
- `backend/app/agent_resume.py`
  - 将附件关联后的续办动作改为批量表单事项 Proposal。
- `backend/domain_packs/mold/skills/local/document/document_engineering_contact_intake/SKILL.md`
  - 明确附件关联后自动生成批量事项 Proposal。
- `backend/domain_packs/mold/skills/local/change/engineering_contact_collaboration/SKILL.md`
  - 明确表单事项、线上/历史模式和人工选择门禁。
- `backend/domain_packs/mold/erp/change/contact_lifecycle.py`
  - 如需读取完整表单快照，增加只读展示适配。
- `backend/domain_packs/mold/manifest.py` 或现有路由位置
  - 注册候选和表单 Proposal 路由。

### 前端

- 新建 `web/src/domain-packs/mold/components/EngineeringContactTaskDialog.vue`。
- 修改 `web/src/components/ProposalCard.vue`，识别批量表单 Proposal 并自动打开弹窗。
- 修改 `web/src/App.vue`，处理表单 Proposal 的确认、关闭、刷新和回执。
- 修改 `web/src/domain-packs/mold/uiText.ts`，增加图片字段和选项文案。

### 测试

- 新建 `tests/test_engineering_contact_task_proposals.py`：Tool Schema、字段校验、权限和版本门禁。
- 新建 `tests/test_engineering_contact_task_batch_confirm.py`：批量创建、事务回滚、幂等和快照。
- 扩展 `tests/test_contact_proposals.py`：附件关联后自动进入批量事项 Proposal，未确认前不新增任务。
- 扩展 `tests/test_local_change_contact_orchestration.py`：Skill 工具注册和无 ERP 边界。
- 增加前端纯函数/文本契约测试，不运行前端构建。

## 10. 验收标准

1. 工程联络附件关联确认后，Skill 自动生成批量办理事项 Proposal。
2. Proposal 出现后自动弹出完整工程变更申请联络单表单。
3. 弹窗包含图片中的完成类型、变更类别、相关单位、作业内容、工时、金额、备注和计价字段。
4. 人工可选择责任部门、具体责任人和完成时间。
5. 未确认前 `ContactTask` 数量不增加。
6. 确认后一次性创建所有选中的相关单位办理事项，并保留完整表单快照。
7. 部门、人员、Case revision、权限和附件变化会阻断旧 Proposal。
8. 重复确认不会重复创建事项。
9. HISTORY 模式不创建线上事项。
10. 全流程仍然通过 Skill、Tool、Proposal 和 HumanIntent，不直接写业务 API，不调用 ERP。
11. 现有普通工程联络单、反馈、方案、复验和关闭回归不受影响。
12. 不执行数据库迁移、不运行前端构建；使用必要的定向 pytest、`compileall` 和 `git diff --check` 验证。
