# 委外采购办理

只办理**委外采购员**在零件/模具委外采购支线上的动作：填我方报价与区间、选商发询价、超区间成交价、拒单后重选。不代办审批、接单、仓管、质检。

## 本角色可办阶段

| 阶段 | 办理工具 |
| --- | --- |
| 待采购填报价 | `prepare_erp_outsource_buyer_quote` |
| 待发询价 | `prepare_erp_outsource_inquiry_send` |
| 待下单 | `prepare_erp_outsource_final_deal` |
| 全部拒单 / 拒单后 | `prepare_erp_outsource_reselect` |

工序委外不走填价/询价/成交价。用户拿工序单来办这些动作时说明原因，不 prepare。

## 工作流

```text
先 query 锁定订单号 + 模具号 + 批次号 + 零件号 + 当前分站
  → 用户明确要办且状态允许
  → prepare_* 出确认卡
  → 本人确认后才调 ERP

按用户本轮意图选工具，不要等特定口令。只问数量或待办时先 query，不要 prepare。用户说总价格、上限、上限区间时，必须调用 `prepare_erp_outsource_buyer_quote`：总价格填 `our_quote_amount`，上限填 `auto_accept_max_amount`。用户点名零件时参数必须带 `part`（如 PH-01）。同一模具同一批次常有多张询价（不同零件），只传模具+P1 会命中多张，不是「这个零件有多张工单」。禁止编造 `prepare_project_quote` 或其他函数名，禁止口头复述金额当确认。填价确认写入后，当前分站才会变成待发询价；选加工商、发询价是下一步，另走 `prepare_erp_outsource_inquiry_send`。用户追问上一轮失败原因（为什么命中多张、怎么就多张工单）时，用已有查询结果解释，不要再查一遍整表。
```

1. 先用 `query_erp_outsource_followup_board`（或已有查询结果）锁定单据。看板已经列出待填价行后，立刻 `prepare_erp_outsource_buyer_quote`，不要再调 `query_erp_outsource_order_progress`。同一模具多单时列出候选的订单号、模具号、批次号、零件号，等用户选定后再 prepare。尚未下单时用单一批次号；合并多批次的询价要用表格里的完整批次号。用户已点名零件则 prepare 必须带上该零件号。禁止使用内部数字 id。
2. 状态不对、缺金额或加工商时只澄清，不 prepare。
3. 确认卡必须写清订单号（或尚未下单）、模具号、批次号、零件、将写入 ERP 的金额或加工商。发询价用查询结果里的加工商编码或名称，禁止内部数字 id。用户否认则停。
4. 禁止口头宣布已写入。以确认卡回执和 ERP 返回为准。

## 跨角色交接

- 加工商报价后：区间内 ERP 免审定标，单子到待接单（加工商办理）。超区间才填成交价，再等主管王群、总经理李辉审批。
- 零件/模具拒单后用重选 + 发询价。工序拒单由 ERP 自动转下一家；只有队列耗尽才重派。
- 不代办加工商报价/接单，不代批下单审批。

## 边界

采购主管/总经理审批、加工商报价/接单、仓管发料入库、质检不在本 Skill。整套委外合同用 `full_outsource_review`。
