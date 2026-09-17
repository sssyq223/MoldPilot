办理 ERP 设计订单前，先用 `erp_design_query_orders` 和 `erp_design_get_record` 读取订单、明细、当前阶段以及版本字段。

删除、设计审批或重新提交使用 `erp_design_manage_order`；草稿闲置料决策使用 `erp_design_manage_order_draft_scrap`；正式订单明细的修改和闲置料决策继续使用对应的订单明细工具。所有写入均应先展示订单编号、拟执行操作、`approvalVersion` 或 `detailVersion`，并在用户明确确认后才调用。

订单查询、草稿、设计审批和采购审批是不同状态。只能依据 ERP 回执说明状态，不得把操作已发起说成采购已下单或物料已到货。
