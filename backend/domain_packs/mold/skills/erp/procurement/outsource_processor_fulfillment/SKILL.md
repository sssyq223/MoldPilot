# 委外加工商履约

只办理**本加工商**在接单之后的**原料收货**。成品发货用 `outsource_processor_product_ship`。不代办仓库发料、回厂入库、质检。

## 本角色可办阶段

| 委外类型 | 可办 |
| --- | --- |
| 零件 / 模具 | 仓库发料后：`prepare_erp_outsource_processor_receipt`；收货后转成品发货 Skill |
| 工序 | **不要收货**。仓库备料完成后直接去 `outsource_processor_product_ship` |

## 跨角色交接

```text
接单后
  零件/模具：等仓管薛海峰原料发货 → 本加工商确认来料 → 成品发货
  工序：等仓管备料完成（ERP 自动收货）→ 本加工商直接成品发货
```

查询若显示「等待仓库」，只说明等谁，不 prepare 收货或发货。

## 工作流

对象清楚就直接查，不必先复述。按用户本轮意图选工具，不要等特定口令。问待办或数量时本轮必须 `CALL_TOOL` `query_erp_outsource_processor_fulfillment`，禁止 `CONVERSATION`。只问数量时不要 prepare。按 `nextAction`：收货才 prepare，等待仓库只说明。空清单如实说，不要说库连接失败。

| 意图 | 工具 |
| --- | --- |
| 我的收料、履约待办 | `query_erp_outsource_processor_fulfillment` |
| 确认来料 | `prepare_erp_outsource_processor_receipt`（要 shipmentId） |
| 成品发货 | 转 `outsource_processor_product_ship`，不在本 Skill prepare |

同一模具多单时列出候选，用户选定后再 prepare。工序单拿来收货时说明原因，不 prepare。用户否认确认卡则停。

## 边界

报价/接单/拒单用 `outsource_processor_ops`。仓库发料用 `outsource_warehouse_ops`。成品入库与质检不是本 Skill。
