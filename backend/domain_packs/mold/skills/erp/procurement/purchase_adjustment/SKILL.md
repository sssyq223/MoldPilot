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

数量必须完整分配且不重复，原采购组保持 ERP 记录；预览结果不等于正式提交结果。任何版本冲突、权限不足或 ERP 回执不明都停止本轮操作并要求重新查询。
