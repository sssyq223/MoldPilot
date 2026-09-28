# 五金 ERP 采购链

版本：1.0.0

目标：核对 ERP 五金采购分组、报价审批材料、供应商、价格、交期和订单状态，并通过人工确认触发受控 ERP 动作。

适用条件：用户明确询问五金采购、询价、报价、比较、定标或五金下单。

步骤：

1. 调用 `query_hardware_purchase_context`，确认真实项目、采购分组和报价审批预览。
2. 采购申请尚未认领或需要发送询价时，转入 `purchase_workbench` Skill；使用 `prepare_purchase_claim` 或 `prepare_hardware_inquiry`。
3. 展示供应商、单价、交期、报价依据和当前 ERP 状态，标明来源和核对时间。
4. 报价材料准备使用 `prepare_hardware_quote`；报价审批归 MoldPilot BPM，不能调用 ERP 待办或旧审批流。
5. ERP 决策满足前置条件后，使用 `prepare_hardware_order` 生成正式下单确认卡。
6. 本人确认后系统重新读取分组并校验版本、权限、供应商和报价；仅以 ERP 返回的订单回执结束动作。

边界：不在 Agent 侧复制五金报价、订单或供应商台账；询价外发、报价提交和正式下单都必须是显式人工确认动作。
