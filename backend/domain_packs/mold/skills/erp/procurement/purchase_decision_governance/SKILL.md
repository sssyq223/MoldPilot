# 采购决策与价格受控执行

版本：1.0.0

目标：核对 ERP 采购决策和价格材料，串联 MoldPilot 人工审批与 ERP 正式动作回执。

步骤：

1. 调用 `query_purchase_decision_context`，确认真实分组、当前状态、报价审批预览和订单情况。
2. 需要确认决策、关闭空组或提交五金报价时，分别准备对应操作提案。
3. MoldPilot BPM 只产生审批事实；审批完成后仍需通过 ERP Adapter 做正式业务动作。
4. ERP 操作必须记录确认人、操作号、请求哈希、ERP 来源标识和最终状态。

边界：不把旧 ERP 审批待办接入 MoldPilot BPM，不在两侧各生成一张订单；ERP 事实和 Agent 审批事实必须分开核对。
