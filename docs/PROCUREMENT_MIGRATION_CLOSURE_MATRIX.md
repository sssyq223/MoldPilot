# 采购与供应商迁移收口矩阵

这份矩阵把 RYmodle 两个业务包的 **88 个源工具**一次性归档，避免把“目标项目工具数量”和“源工具数量”混为一谈。

## 总数

| 源包 | 源工具 | 已直接迁移或合并到目标链 | 当前仍需 ERP 专用契约 |
|---|---:|---:|---:|
| `procurement_work` | 56 | 36 | 20 |
| `supplier_work` | 32 | 14 | 18 |
| **合计** | **88** | **50** | **38** |

50 个源工具合并为 MoldPilot 当前的 **42 个目标 Tool**，因为源项目把同一 ERP 业务动作拆成了多个薄封装（例如查询、预览、提交、审批）；目标项目按 ERP 业务边界合并为查询工具、确认提案和单一回执链。

## 已迁移或合并

采购侧已覆盖数量变更、采购申请/决策、钢料拆分、询价、报价比较、拆组调整、候选顺位、临时分组、无人接单重采、重采系统价和五金定标。供应商侧已覆盖订单/报价/发货查询、接单/拒单、报价提交、发货创建、交付异常以及价格访问审批。

对应目标入口集中在：

- `backend/domain_packs/mold/erp_adapter.py`
- `backend/domain_packs/mold/tools/erp/procurement/migration_tools.py`
- `backend/domain_packs/mold/tool_gateway.py`
- `backend/domain_packs/mold/skills/erp/procurement/`

## 尚未直接迁移的源工具

这些工具没有被伪装成“已完成”。它们需要 ERP 侧补充明确的 Agent 安全接口、供应商身份接口或专用 VO 后，才能进入 MoldPilot 的确认提案链。

采购侧 24 个：

`purchase_delivery_instruction_get`、`purchase_claim_list`、`purchase_processing_get`、`purchase_delivery_instruction_confirm`、`purchase_order_get`、`purchase_request_prepare`、`purchase_order_prepare`、`purchase_request_submit`、`purchase_request_approve`、`purchase_request_reject`、`purchase_drawing_get`、`purchase_attached_costing_get`、`purchase_attached_costing_prepare`、`purchase_attached_costing_submit`、`purchase_direct_assign`、`purchase_reassign`、`purchase_hardware_split_prepare`、`purchase_hardware_split_submit`、`purchase_hardware_split_rebuild_prepare`、`purchase_hardware_split_rebuild_submit`。

供应商侧 20 个：

`supplier_quantity_change_list`、`supplier_delivery_available_list`、`supplier_pending_tasks`、`supplier_progress_report`、`supplier_quote_reject`、`supplier_quality_reship`、`supplier_qualification_list`、`supplier_qualification_get`、`supplier_price_list`、`supplier_quote_compare`、`supplier_price_draft_save`、`supplier_qualification_confirm`、`supplier_price_submit`、`supplier_price_disable`、`supplier_order_reject_reason_list`、`supplier_price_approval_get`、`supplier_price_approve`、`supplier_price_reject`。

## 收口判定

“迁移完成”在本项目中定义为：源工具全部有明确状态，已迁移工具都经过目标权限、Typed Schema、人工确认和 ERP Adapter 注册；未直接迁移的工具必须有具体 ERP 契约缺口，不能用空实现或自由 JSON 冒充完成。

因此当前代码已经完成核心采购链迁移，但 **88 个源工具尚未全部变成可执行的 MoldPilot 工具**。要达到业务全量迁移，下一次开发必须围绕上面 44 个缺口一次性补齐 ERP 接口和供应商身份联调，不能再按零散工具追加。
