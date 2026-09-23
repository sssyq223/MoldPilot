# 委外仓管回厂收货入库

只办理加工商**成品发货之后**的仓库动作：到货确认（确认收货）和仓储入库。不代办原料发货、质检、拒收破损。

## ERP 两步（不要合成一笔）

```text
加工商成品发货
  → 【仓管】到货确认  POST /entrust/arrival-confirm/{shipmentId}/confirm
  → 【仓管】仓储入库  POST /entrust/arrival-confirm/{shipmentId}/confirm-inbound
      ├─ 零件 / 模具委外、工序末道 T → 成品库
      └─ 工序非末道（F / 未知）→ 半成品库（后续工序流转由 ERP/物料人员处理）
  → 质检领取
```

用户说「确认收货 / 到货」先办到货。到货已完成后才办入库。不要在一张确认卡里连写两步。

## 部分收 / 部分入

未指定行数量时，按该行待办数量全确认。可以只选部分发货明细、或部分数量（不超过待到货 / 已确认到货且尚未入库数量）。拒收、缺失、破损本期不 prepare。

入库目标按 ERP `processor-inbound-v1`：零件/模具进成品库；工序仅明确末道进成品库；非末道或末道缺失进半成品库并提示数据缺失。确认卡展示行级库别，一张发货单可同时含成品和半成品。ERP 没返回 `processorInboundTarget` 时禁止提交入库。回执以 ERP `inboundDetails` 为准，不再默认成品库。待入库数量 = 已确认到货 − 已入库 − 退回。

## 工作流

对象清楚就直接查。「收货待办 / 入库待办 / 回厂待办 / 到货确认」本轮必须 `CALL_TOOL` `query_erp_outsource_warehouse_inbound`，禁止 `CONVERSATION`。只问数量时不要 prepare。按 `nextAction` 选到货或入库，不要一次办两步。

| 意图 | 工具 |
| --- | --- |
| 待收货、待入库、回厂待办 | `query_erp_outsource_warehouse_inbound` |
| 确认到货 / 确认收货 / 办到货 | `prepare_erp_outsource_warehouse_arrival`（要 shipmentId） |
| 确认入库 / 办入库 | `prepare_erp_outsource_warehouse_inbound`（要 shipmentId） |

「到货确认待办 / 回厂入库待办有几个」是查询，不是办理。

同一模具多单时列出候选。用户否认确认卡则停。

## 边界

原料发货 / 工序备料用 `outsource_warehouse_ops`。成品发货用 `outsource_processor_product_ship`。质检用 `outsource_quality_ops`。
