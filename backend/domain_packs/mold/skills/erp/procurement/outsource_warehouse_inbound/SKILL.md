# 委外仓管回厂收货入库

只办理加工商**成品发货之后**的仓库动作：一次入库确认。不代办原料发货、质检、拒收破损。

## ERP 一步（仓管只办这一次）

```text
加工商成品发货
  → 【仓管】入库确认  POST /material/inbound  inboundType=3
      ├─ 零件 / 模具委外、工序末道 T → 成品库
      └─ 工序非末道（F / 未知）→ 半成品库（后续工序流转由 ERP/物料人员处理）
  → 质检领取
  → 合格后才入账库存
```

发货后即可办理，不要先做到货确认，不要调用 `/entrust/arrival-confirm`。用户说「确认收货 / 确认到货 / 入库确认」都办这一步。

## 部分入

未指定行数量时，按该行待入库数量全确认。可以只选部分发货明细、或部分数量（不超过待入库）。拒收、缺失、破损本期不 prepare。

待入库数量 = 发货数量 − 已入库 − 退回 − 异常，不要求先到货确认。

入库目标按 ERP `processor-inbound-v1`：零件/模具进成品库；工序仅明确末道进成品库；非末道或末道缺失进半成品库并提示数据缺失。确认卡展示行级库别。一张确认卡只能入同一目标库；成品库和半成品库请分次办理。ERP 没返回 `processorInboundTarget` 时禁止提交。回执以 ERP 物料入库单为准，不再默认成品库。

## 工作流

对象清楚就直接查。按用户本轮意图选工具，不要等特定口令。问待办或数量时本轮必须 `CALL_TOOL` `query_erp_outsource_warehouse_inbound`，禁止 `CONVERSATION`。只问数量或「查看待办 / 查询待办」时不要 prepare，列出结果即可。用户说「确认入库 / 入库确认 / 确认到货 / 确认收货」时立刻 `prepare_erp_outsource_warehouse_inbound`，不要再 query 一遍后说没有待办，不要改办到货确认。

| 意图 | 工具 |
| --- | --- |
| 待收货、待入库、回厂待办 | `query_erp_outsource_warehouse_inbound` |
| 确认入库 / 办入库 / 确认到货 / 确认收货 / 办到货 | `prepare_erp_outsource_warehouse_inbound`（发货单号或订单号） |

「到货确认待办 / 回厂入库待办有几个」是查询，不是办理。

同一模具多单时列出候选。用户否认确认卡则停。

## 边界

原料发货 / 工序备料用 `outsource_warehouse_ops`。成品发货用 `outsource_processor_product_ship`。质检用 `outsource_quality_ops`。
