# 委外加工商成品发货

只办理**本加工商**把已加工件发回厂。零件委外和工序委外都走本 Skill。不代办收料、仓库入库、质检。

## 何时能发

工单阶段必须是生产中 / 发货中。可发数量按 ERP：

```text
可发 = min(订单数量 − 已占用发货, 已收原料 − 已占用发货)
自找料 / 不需我方供料：不卡原料，只卡订单剩余
```

部分原料先发、加工商只确认了其中一部分时，**只能发已收对应的那部分**。未收完不要按整单数量 prepare。

## 入库目标（业务规则）

从 ERP `entrust_upload_lines` 读该零件的 `is_first_operation` / `is_end_operation`（true=T，false=F）。回厂入库目标按业务规则：

| 委外类型 | 末道 `is_end_operation` | 回厂入库目标 |
| --- | --- | --- |
| 零件 | — | **成品库** |
| 模具 | — | **成品库** |
| 工序 | T | **成品库** |
| 工序 | F / 未知 | **半成品库** |

`is_first_operation` 管的是仓库**供料来源**（首道物料库 / 非首道半成品库），不是回厂目标。末道标志未知时按半成品库展示，并提示数据缺失。规则版本 `processor-inbound-v1`，与 ERP 订单展示/入库/库存/台账/质检同一矩阵。确认卡展示 ERP 行级结果，不以整单默认成品库。

## 拒单转下一家（本 Skill 不办，但必须知道）

工序委外有 `dispatch_queue_json` 时：

```text
本加工商拒单
  → 同一张工单换 supplier_id，dispatch_index+1
  → 阶段回到待接单
  → 下一家用接单/拒单 Skill
  → 下一家接单后才重新生成该家的仓库供料
  → 上一家未发完的成品不转给下一家
队列耗尽 → 工单停在拒单，采购重派
```

零件/模具拒单没有队列，采购重选后再询价。本 Skill 只给**当前接单加工商**发货。

## 工作流

对象清楚就直接查。按用户本轮意图选工具，不要等特定口令。问待办或能发哪些时本轮必须 `CALL_TOOL` `query_erp_outsource_processor_product_ship`，禁止 `CONVERSATION`。只问能发哪些时不要 prepare。用户要发货时用 `prepare_erp_outsource_processor_product_ship`。

| 意图 | 工具 |
| --- | --- |
| 哪些能发、发成品库还是半成品库 | `query_erp_outsource_processor_product_ship` |
| 发货（发成品 / 发半成品 / 确认成品发货 / 办成品发货） | `prepare_erp_outsource_processor_product_ship`（要订单号，必要时加模具号、批次号；行数量可空=按可发全发） |

「成品发货待办有几个」是查询，不是办理。

确认卡必须写清订单号、模具号、批次号、零件、本次数量、已收/可发、入库目标。不要报内部数字 id。用户否认则停。

## 下一步（仓管 / 质检）

```text
加工商成品发货（本 Skill）
  → 仓管到货确认  POST /entrust/arrival-confirm/{shipmentId}/confirm
  → 仓管入库确认  POST /entrust/arrival-confirm/{shipmentId}/confirm-inbound
      ├─ 成品库 → 成品库存
      └─ 半成品库 → 半成品库存 → 再转临时库
  → 质检
```

仓管用 `outsource_warehouse_inbound` 做到货再入库。质检用 `outsource_quality_ops` 领取或直接提交合格。入库目标按上表（`processor-inbound-v1`）。ERP 发货行会固化 `is_end_operation`，仓库入库只读这份快照。

## 边界

确认来料用 `outsource_processor_fulfillment`。报价接单用 `outsource_processor_ops`。仓库发料用 `outsource_warehouse_ops`。
