# 委外加工商办理

只办理**本加工商**在零件/模具/工序委外上的动作：报价、接单、拒单。多家加工商共用本 Skill，数据范围限本供应商。不代办采购成交价、审批、仓管、质检。

## 本角色可办阶段

| 阶段 | 办理工具 |
| --- | --- |
| 待报价（零件/模具） | `prepare_erp_outsource_processor_quote` |
| 待接单 | `prepare_erp_outsource_processor_accept` 或 `prepare_erp_outsource_processor_reject` |

工序委外**没有报价**。用户拿工序单来报价时说明原因，不 prepare。

## 跨角色交接（不要越权代办）

```text
零件/模具：
  加工商报价
    → 区间内：ERP 免审定标，本加工商待接单（提醒接单）
    → 超区间：等采购员徐桂利填成交价
        → 主管王群审批 → 总经理李辉审批
        → 通过后本加工商待接单
  拒单 → 采购员重选加工商再发询价

工序：
  无报价，直接待接单
  拒单（工单有 dispatch_queue_json）：
    同一张工单换 supplier_id，dispatch_index+1，阶段回到待接单
    上一家待办取消；下一家用同一套接单/拒单
    下一家接单后才重新生成该家的仓库供料，上一家未发完成品不转走
  队列耗尽 → 工单停在拒单，等采购员重派
  零件/模具拒单没有队列，采购重选后再发询价
```

加工商看不到我方报价、接单上限、成交价。不要根据内部价判断区间，以重新查询后的分站为准。

## 工作流

```text
先 query 锁定 invitationId / orderId + 当前分站
  → 用户明确要办且状态允许
  → prepare_* 出确认卡
  → 本人确认后才调 ERP

用户只问「有几个 / 待报价 / 待接单」时不要激活本 Skill，走 `outsource_processor_query`。本 Skill 只在用户说提交报价、我要接单、拒绝接单时办理。
```

1. 先用 `query_erp_outsource_processor_board`（或已有查询结果）锁定本供应商单据。同一模具多单时列出候选，等用户选定后再 prepare。
2. 报价要 `invitationId`、金额、交期 `YYYY-MM-DD`、是否含税。接单要 `orderId`。拒单要 `orderId` 和 ERP 拒单原因编码。缺参数只澄清，不 prepare。
3. 报价确认后必须再查一次：
   - 待接单：提醒用户接单。
   - 待下单 / 审批中：说明超区间，等采购成交价和王群、李辉审批，不要自己接单。
4. 拒单后不要自己选下一家。工序单由 ERP 转派；零件/模具单等采购员重选。
5. 禁止口头宣布已写入。以确认卡回执和 ERP 返回为准。

## 边界

接单之后的收料用 `outsource_processor_fulfillment`，成品发货用 `outsource_processor_product_ship`。仓库发料用 `outsource_warehouse_ops`。采购填价/发询价/成交价用 `outsource_buyer_ops`。下单审批用 `outsource_approval_ops`。
