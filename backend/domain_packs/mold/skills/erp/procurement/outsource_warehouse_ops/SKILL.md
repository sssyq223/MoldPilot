# 委外仓管供料办理

只办理**仓库人员**在接单之后的供料动作。零件/模具走原料发货，工序走备料完成。不代办加工商收货、回厂入库、质检。采购直发不是本 Skill。回厂入库用 `outsource_warehouse_inbound`。

## 本角色可办阶段

| 委外类型 | 查询后动作 | 办理工具 |
| --- | --- | --- |
| 零件 / 模具 | 原料发货 | `prepare_erp_outsource_warehouse_ship` |
| 工序 | 备料完成 | 同一工具；确认后 ERP 视为发货=收货 |

热处理工序备料必须带每个零件的实际重量。

## 跨角色交接

```text
加工商接单
  → 仓库待办生成（物料库 / 半成品库）
  → 【仓管】确认发料或备料完成
      ├─ 零件/模具：加工商确认原料收货 → 再生产 → 成品发货
      └─ 工序：加工商不用收货，直接生产并成品发货
采购直发由物料供应商办理，不要当成仓库待办。
```

## 工作流

对象清楚就直接查，不必先复述。按用户本轮意图选工具，不要等特定口令。问待办或数量时本轮必须 `CALL_TOOL` `query_erp_outsource_warehouse_tasks`，禁止 `CONVERSATION`。只问数量时不要 prepare。用户要发料或备料时用 `prepare_erp_outsource_warehouse_ship`。

| 意图 | 工具 |
| --- | --- |
| 仓库待办、待发料、待备料、有几个 | `query_erp_outsource_warehouse_tasks` |
| 确认发料 / 确认备料 / 办发料（「备料完成的有哪些」是查询，不是办理） | `prepare_erp_outsource_warehouse_ship`（订单号，必要时加模具号、批次号） |

1. 先查询锁定 **订单号**，必要时加模具号、批次号。同一工单多行一次确认，但不能跨工单或跨来源。禁止使用内部数字 id。
2. 工序单说明「备料完成即交接」；零件单说明「发货后等加工商收货」。
3. 状态已变、缺重量、不是仓库 pending 只澄清，不 prepare。
4. 禁止口头宣布已写入。以确认卡回执和 ERP 返回为准。

## 边界

加工商收料用 `outsource_processor_fulfillment`，成品发货用 `outsource_processor_product_ship`。回厂收货入库用 `outsource_warehouse_inbound`。质检用 `outsource_quality_ops`。
