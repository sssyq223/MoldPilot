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
先 query 锁定 inquiryId + 当前分站
  → 用户明确要办且状态允许
  → prepare_* 出确认卡
  → 本人确认后才调 ERP

用户只问「有几个 / 待办 / 待填价」时不要激活本 Skill，走 `outsource_followup_query`。本 Skill 只在用户说填我方报价、发询价、填成交价、重选加工商时办理。
```

1. 先用 `query_erp_outsource_followup_board`（或已有查询结果）锁定 **inquiryId** 和分站。同一模具多单时列出候选，等用户选定后再 prepare。
2. 状态不对、缺金额或加工商时只澄清，不 prepare。
3. 确认卡必须写清模具号、零件、将写入 ERP 的金额或供应商。用户否认则停。
4. 禁止口头宣布已写入。以确认卡回执和 ERP 返回为准。

## 跨角色交接

- 加工商报价后：区间内 ERP 免审定标，单子到待接单（加工商办理）。超区间才填成交价，再等主管王群、总经理李辉审批。
- 零件/模具拒单后用重选 + 发询价。工序拒单由 ERP 自动转下一家；只有队列耗尽才重派。
- 不代办加工商报价/接单，不代批下单审批。

## 边界

采购主管/总经理审批、加工商报价/接单、仓管发料入库、质检不在本 Skill。整套委外合同用 `full_outsource_review`。
