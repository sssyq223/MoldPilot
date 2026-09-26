# 中标到开工通知及后置关联设计

## 目标

在不破坏现有 `DocumentIntake`、`BidIntakeRevision`、`BusinessSubject` 和合同 OCR 链路的前提下，补齐“中标通知确认 → 项目匹配 → 开工通知 → 部门独立回执 → 项目部最终决定 → 合同/核算清单后置绑定”的可审计门禁。

## 方案

采用新增领域对象、兼容旧业务事实的适配方案。`DocumentIntake` 继续作为上传接收事实；人工确认产生的 `bid_notice.confirmed` 事件只创建待匹配记录，不直接创建正式开工通知。项目匹配和中标接收版本确认后，系统才创建不可变的 `StartNotice` 版本。现有 `BusinessSubject(kind='internal_start')` 在兼容期作为旧正式开工事实，但新流程以 `StartNotice` 的最终决定作为合同和核算清单绑定前置条件。

## 状态和门禁

### 中标事件匹配

`PENDING_MATCH → MATCHED → INTAKE_CONFIRMED`，也允许 `REJECTED`。同一 `bid_notice.confirmed` 事件只能对应一条匹配记录；候选项目和匹配依据必须由人工确认，模型分类不直接产生项目关系。

### 开工通知

`DRAFT → DEPARTMENT_REVIEW → PROJECT_ACCEPTED | FULL_OUTSOURCE_ACCEPTED | REJECTED | RETURNED`。

- `DRAFT` 只能由已确认的中标接收版本产生；
- 部门回执独立记录，不因一个部门退回而覆盖其他部门回执；
- 项目部最终决定必须引用具体开工通知版本；
- 只有 `PROJECT_ACCEPTED` 或 `FULL_OUTSOURCE_ACCEPTED` 才能进入正式关联准备。

### 后置绑定

合同或核算清单只能以候选方式存在，正式绑定必须同时引用：

- `start_notice_id`；
- `start_notice_version`；
- `project_decision_id`；
- 合同或核算清单自身版本；
- 模具逐行确认快照。

任何开工通知版本、项目最终决定或合同/清单版本变化都必须阻止旧绑定继续写入。

## 安全和兼容

- 不自动确认项目、不自动批准开工、不自动建立合同正式关联；
- 不写入模型原文、提示词、Key、Token 或 PDF 正文到审计日志；
- 事件消费必须幂等，并校验原始文件所有权和确认事件；
- 旧 `BidIntakeCase`、`BidIntakeRevision`、`InternalStartSnapshot`、`BusinessSubject` 数据保留；兼容查询明确标注旧链路；
- 本轮只创建迁移源和测试 SQL，不执行正式数据库迁移。

## 验收标准

1. 未确认的分类不能创建中标匹配或开工通知。
2. 同一 `bid_notice.confirmed` 重放不会重复创建匹配记录。
3. 项目匹配、接收版本、开工通知版本和部门回执均可追溯。
4. 部门回执互不覆盖，缺失或退回部门会阻止项目最终通过。
5. 未达到项目最终决定时，合同和核算清单只能预览，正式绑定返回明确门禁错误。
6. 所有新增行为有串行测试，测试目标只能是 `127.0.0.1:55432/moldpilot_test`。
