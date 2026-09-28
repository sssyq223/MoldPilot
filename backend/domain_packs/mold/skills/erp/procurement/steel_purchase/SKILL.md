# 原材/钢料 ERP 采购链

版本：1.0.0

目标：在当前用户和项目范围内核对 ERP 原材/钢料采购分组、拆单路径、采购决策、订单与履约事实。

适用条件：用户明确询问原材、钢料、拆单、整单不拆、原材下单或供应商履约；必须先定位 Agent 项目或模具线索。

步骤：

1. 调用 `query_raw_material_purchase_context`，确认项目、ERP 分组、订单、发货、入库、库存和质检来源。
2. 采购申请尚未认领时，转入 `purchase_workbench` Skill 使用 `query_purchase_workbench_context` 和 `prepare_purchase_claim`。
3. 区分拆单、整单不拆、采购决策和正式订单；草稿、审批通过和 ERP 订单回执不能互相替代。
4. 需要拆单或整单不拆时，准备 `prepare_raw_material_split`，展示路径、数量、收货点、ERP 版本和后续影响。
5. 需要确认采购决策时，准备 `prepare_purchase_decision`；需要正式下单时，准备 `prepare_raw_material_order`。
6. 写操作必须等待本人确认；确认后由系统重新校验授权和 ERP 版本，只有收到 ERP 回执才报告成功。
7. ERP 超时、版本冲突、拒绝或供应商异常时保留未确定状态，先查询 ERP 再决定恢复动作。

边界：不在 Agent 数据库创建第二套采购订单、库存或入库台账；不调用 ERP 旧审批流替代 MoldPilot BPM；不把 Agent 审批通过当作 ERP 已下单。
