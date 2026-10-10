# ERP 审批流迁移全量测试记录

测试日期：2026-10-09  
测试范围：ERP 已迁移到 MoldPilot 的 17 条审批流  
MoldPilot：`D:\mold-agent\MoldPilot`  
ERP：`D:\ERP-system\management-system`  
测试账号：MoldPilot `admin / admin123`

## 测试设计

本轮验证的是 MoldPilot BPM 是否能够承接 ERP 审批目录中的每一条流程：能否提交、正确解析迁移后的审批人、按顺序或并行会签生成待办、校验节点表单，并在全部同意后完成业务状态变更。

每条流程使用独立的临时项目、物料、供应商和单据，在同一个 MoldPilot 数据库事务中提交并逐级审批，测试结束回滚事务。因此不会留下测试项目、测试单据或测试审批实例。表单节点使用合法测试值：预计完成日期、逐行定标结论/单价/说明、价格查看理由和异常扣款金额/原因。

迁移配置同时按 ERP 原审批人固化到 MoldPilot 账号：设计主管于孟、采购主管张亚倩、总经理李辉、模具主管谢志华、交期确认郭伟、品质赵殿烨、仓库薛海峰、财务陈财务。这样部门普通成员不会因为部门匹配而被错误加入 `ALL` 会签。

## 全量结果

- [x] `design_new_model_approval`：于孟 → 张亚倩；完成，业务状态 `EFFECTIVE`
- [x] `design_modify_model_approval`：于孟 →（李辉、谢志华会签）→ 张亚倩；完成，业务状态 `EFFECTIVE`
- [x] `purchase_request_approval`：张亚倩；完成，业务状态 `APPROVED`
- [x] `production_outsource_approval`：谢志华；完成，业务状态 `APPROVED`
- [x] `design_order_approval`：于孟、李辉同序会签；完成，业务状态 `EFFECTIVE`
- [x] `purchase_reconcile_internal_approval`：赵殿烨、薛海峰会签 → 张亚倩 → 陈财务 → 李辉；完成，业务状态 `APPROVED`
- [x] `outsource_order_approval`：张亚倩 → 李辉；完成，业务状态 `APPROVED`
- [x] `procure_price_approval`：李辉；完成，业务状态 `EFFECTIVE`
- [x] `mold_repair_design_change_approval`：于孟；完成，业务状态 `EFFECTIVE`
- [x] `purchase_manual_dispatch_approval`：张亚倩 → 李辉；完成，业务状态 `APPROVED`
- [x] `mold_repair_order_reapproval`：于孟 → 张亚倩 → 李辉；完成，业务状态 `EFFECTIVE`
- [x] `purchase_supplier_rank_adjustment_approval`：张亚倩；完成，业务状态 `APPROVED`
- [x] `purchase_split_group_adjustment_approval`：张亚倩；完成，业务状态 `APPROVED`
- [x] `procure_hardware_award_approval`：李辉，逐行定标表单通过；完成，业务状态 `APPROVED`
- [x] `price_sensitive_access_approval`：李辉，查看理由表单通过；完成，业务状态 `APPROVED`
- [x] `supplier_exception_deduction_approval`：张亚倩，扣款金额/原因表单通过；完成，业务状态 `APPROVED`
- [x] `design_new_model_attached_square_approval`：于孟 → 郭伟（交期表单）→ 张亚倩；完成，业务状态 `EFFECTIVE`

全量结果：**17/17 提交并逐级办理通过**。运行输出中没有 `ASSIGNMENT_BLOCKED`、表单缺失或 BPM 节点异常。

## 真实上传联动结果

真实文件：`C:\Users\LENOVO\Desktop\test\上传文件\M250238-P4-五金请购单66.xlsx`。

- MoldPilot 解析后创建 ERP `design_upload_session.id=21`，9 行五金明细。
- MoldPilot 已创建本地 BPM 单据 `subjectId=94b8c2e8-c3ce-4941-b21c-50f0bc337c41`，审批实例 `instanceId=ea1bfbce-15e3-44c3-a37d-a5b2838b86ae`。
- 于孟和张亚倩已完成 MoldPilot 两级审批，MoldPilot 单据状态为 `EFFECTIVE`。
- ERP 已写入采购申请 `id=9`、单号 `PR20261009150932`，来源 `design_upload/hardware`。
- ERP 旧的设计审批实例已停用，ERP 采购申请状态为 `approval_status=approved`、`current_stage_code=moldpilot_bpm_completed`、`workflow_instance_id=NULL`；采购人员可在 ERP 采购申请/订单列表查看该单，不再等待 ERP 的旧设计审批节点。
- MoldPilot 查询接口 `GET /api/erp-design-uploads/approval-status/94b8c2e8-c3ce-4941-b21c-50f0bc337c41` 已返回本地 `COMPLETED/EFFECTIVE`、ERP `found=true`、采购申请 `approved`、无遗留待办。

前端可查看的原始上传会话：`228fb949-aaa4-4a8a-b873-49a7bab5855b`；本轮最终审批和 ERP 回读以以上 subject/instance/request ID 为准。

## 代码与配置收口

- `business.enter_stage` 已兼容迁移数据中旧的 `assignment_pools[].users` 形状。
- 迁移脚本已支持 ERP 固定审批人和新增的模具主管、交期确认、仓库角色；17 条定义已重新发布到 MoldPilot。
- ERP 直连导入在 MoldPilot BPM 完成后会关闭 ERP 旧审批投影，把采购申请标记为可供采购人员查看；不依赖 ERP 浏览器 JWT、Cookie 或 MCP 占位令牌。
- 测试期间新增的权限是已迁移固定审批人的 MoldPilot 审批读取/办理权限；临时业务测试数据已回滚。

## 自动化验证

- `python -m compileall -q backend scripts`：通过
- `pytest tests/test_erp_design_mcp_tools.py tests/test_erp_workflow_migration.py -q`：`40 passed`
- 全量 BPM 事务测试：`17/17 passed`，测试数据回滚

