# ERP 审批流迁移记录

迁移范围：ERP `wf_process_definition` 当前 17 条启用审批流。目标是由 MoldPilot BPM 持有审批过程，ERP 只保留业务事实查询和审批完成后的受控业务动作。

迁移入口：`scripts/migrate_erp_workflows.py`

迁移结果：17 条 `version=1` 定义已写入 MoldPilot `workflow_definition`，状态为 `DRAFT`，元数据 `migration_status=MIGRATED_DRAFT`。脚本可重复执行；已发布定义不会被覆盖为草稿。

| ERP 流程键 | MoldPilot 节点摘要 | 特殊语义 |
|---|---|---|
| `design_new_model_approval` | 设计主管 → 采购主管 | 新模适用 |
| `design_modify_model_approval` | 设计主管 → 管理会签 → 采购主管 | 项目负责人/设计负责人并集会签 |
| `purchase_request_approval` | 采购主管 | 采购申请 |
| `production_outsource_approval` | 生管 | 委外排产 |
| `design_order_approval` | 设计负责人/项目负责人会签 | 同序会签 |
| `purchase_reconcile_internal_approval` | 品质/采购确认 → 采购 → 财务 → 项目负责人 | 多角色确认 |
| `outsource_order_approval` | 采购主管 → 项目负责人 | 委外下单 |
| `procure_price_approval` | 采购主管 | 逐行表单可扩展 |
| `mold_repair_design_change_approval` | 设计主管 | 修改图纸表单 |
| `purchase_manual_dispatch_approval` | 采购主管 → 项目负责人 | 无人接单直派 |
| `mold_repair_order_reapproval` | 设计主管 → 采购主管 → 项目负责人 | 三段审批 |
| `purchase_supplier_rank_adjustment_approval` | 采购主管 | 排名调整 |
| `purchase_split_group_adjustment_approval` | 采购主管 | 临时拆单 |
| `procure_hardware_award_approval` | 项目负责人 | 逐行定标表单 |
| `price_sensitive_access_approval` | 采购主管 | 查看理由必填 |
| `supplier_exception_deduction_approval` | 采购主管 | 扣款金额/原因必填 |
| `design_new_model_attached_square_approval` | 设计主管 → 交期确认 → 采购主管 | 交期表单 |

发布前置：需要在 MoldPilot 项目角色中补齐并确认 ERP 对应的品质、采购、设计、财务、制造和项目负责人。当前库缺少可用于 `QUALITY_OWNER` 的项目角色成员，因此保持草稿，不自动改变现有已发布流程。

联动测试：按照用户要求，模板迁移和组织映射发布完成后再执行 ERP↔MoldPilot 联动测试；本次只做定义校验、BPMN 编译和数据库迁移核对。
