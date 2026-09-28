---
name: purchase-adjustment
description: 采购拆组调整与临时调整提案。
---

# 采购拆组调整

用于查询 ERP 当前采购申请的拆组调整上下文、原采购组版本、快照和历史。调整必须先取得 ERP 权威上下文，再生成服务端预览提案；提交已有调整时必须带回调整版本和快照，不能改写本地采购台账或猜测明细数量。

可用工具：

- `query_purchase_adjustment_context`：读取申请的调整上下文、历史及指定调整详情。
- `prepare_purchase_split_adjustment`：按原采购组版本、快照和完整数量分配生成 ERP 服务端预览确认卡。
- `prepare_purchase_split_adjustment_submit`：提交已经由 ERP 生成的拆组调整提案，确认前重新校验版本。
- `prepare_purchase_temporary_group_save` / `prepare_purchase_temporary_group_delete`：保存或删除尚未转正式调整的临时分组草稿，均需本人确认。
- `query_purchase_repurchase_context`：读取候选耗尽后的 ERP 无人接单重采批次和启用供应商。
- `prepare_purchase_repurchase_submit`：逐零件指定供应商、价格和继承交期，保存重采草稿并提交采购主管、总经理两级审批。
- `query_purchase_supplier_ranking_context`：读取采购组候选供应商顺位和 ERP 快照。
- `prepare_purchase_supplier_rank_adjustment`：按当前快照生成候选顺位调整提案；不直接修改候选表。
- `query_purchase_order_quantity_change_context`：读取采购订单数量变更影响和供应商确认约束。
- `prepare_purchase_order_quantity_change`：准备单行或批量数量变更提案，确认后进入 ERP 供应商确认流程。
- `query_purchase_hardware_award_context`：读取五金定标批次、报价来源和当前版本。
- `prepare_purchase_hardware_award_draft`：从 ERP 采购组创建五金定标草稿。
- `prepare_purchase_hardware_award_submit`：提交五金定标审批。
- `prepare_purchase_hardware_award_final_approve`：先预览再审批五金定标最终结果，逐行保留报价或历史价格来源。
- `query_purchase_price_compare_context`：读取 ERP 报价比较结果和价格来源。
- `prepare_purchase_price_compare_approval`：按报价记录准备议价审批提案。
- `query_purchase_repurchase_system_price`：按重采批次、明细和供应商读取当前 ERP 系统价。
- `query_supplier_price_access_policy`：读取当前用户访问供应商价格敏感数据的 ERP 策略。
- `prepare_supplier_price_access_decision`：准备供应商价格敏感访问申请的批准或驳回提案。

数量必须完整分配且不重复，原采购组保持 ERP 记录；预览结果不等于正式提交结果。任何版本冲突、权限不足或 ERP 回执不明都停止本轮操作并要求重新查询。
