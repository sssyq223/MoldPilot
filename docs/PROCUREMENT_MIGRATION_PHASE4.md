# 采购迁移第四阶段

## 目标

在 ERP 最新 `zhangwenjin` 源码（同步到 `cb782a8b0`）上，继续把高价值采购边界迁移到 MoldPilot。MoldPilot 只负责权限、确认卡、幂等操作记录和版本重校验；ERP 继续负责订单、供应商、报价、审批和金额事实。

## 本阶段已迁移

| 业务链 | 新增工具 | ERP 接口 |
| --- | --- | --- |
| 采购订单数量变更 | `query_purchase_order_quantity_change_context`、`prepare_purchase_order_quantity_change` | `/api/agent/procurement/orders/{order_id}/quantity-change-impact`、`/api/agent/procurement/actions/order-quantity-change-proposals` |
| 五金定标审批 | `query_purchase_hardware_award_context`、`prepare_purchase_hardware_award_submit`、`prepare_purchase_hardware_award_final_approve` | `/purchase/hardware-award/{batch_id}`、`/purchase/hardware-award/{batch_id}/submit`、`/purchase/hardware-award/todos/{todo_id}/final-preview`、`final-approve` |
| 报价比较与价格访问 | `query_purchase_price_compare_context`、`prepare_purchase_price_compare_approval`、`query_supplier_price_access_policy`、`prepare_supplier_price_access_decision` | `/purchase/price-compare/list`、`/purchase/price-compare/negotiated-approval`、`/api/agent/procurement/price-access/policy`、`/requests/{id}/decision` |
| 无人接单重采核价 | `query_purchase_repurchase_system_price` | `/purchase/manual-dispatch/{batch_id}/lines/{line_id}/system-price` |

数量变更支持单行和批量明细，输入会拒绝重复明细；确认时使用 ERP 当前影响快照，并为动作绑定 MoldPilot 操作号。五金最终定标要求逐行声明当前报价或历史价格来源，确认时先调用 ERP 终审预览，再提交最终审批。

## 安全边界

- 查询工具不在本地缓存报价、候选供应商或金额。
- 写操作只通过确认提案执行，确认前重新读取 ERP 当前版本/状态。
- ERP 返回不明时将操作标记为待核对，不自动重试正式动作。

## 收口阶段

收口阶段新增采购订单与发货指令、供应商资格/价格目录/价格审批、供应商可发货明细、供应商待办和拒单原因查询，并把供应商价格审批批准/驳回接入人工确认提案。

供应商数量变更查询、价格草稿维护、供应商资格/有效价全生命周期、附图件核价、采购申请草稿/审批、五金拆分重建、报价拒绝和质量补发仍按[收口矩阵](./PROCUREMENT_MIGRATION_CLOSURE_MATRIX.md)登记为 Agent 安全契约排除项；普通 ERP 管理页面路由不直接暴露给模型。
