# 委外质检领取与合格

只办理委外**回厂入库之后**的质检：领取任务、提交全检合格。不代办仓管入库、厂内工序质检、不合格退货。

## ERP 事实

入库确认后 ERP 生成 `quality_inspection_task`，`source_type=processor_inbound`。

```text
仓管入库
  → 质检待领取（pending）
  ├─ 【质检】领取  PUT /quality/inspection/{taskId}/claim
  │    状态变为 inspecting，再提交合格
  └─ 【质检】直接合格  PUT /quality/inspection/{taskId}/submit
       ERP 允许 pending 直接提交，并同时记录当前人为领取人
  → 合格数量进入成品库或半成品库存
```

用户说「领取质检」时只 prepare 领取；用户明确要办合格（「判合格 / 确认合格 / 检验通过 / 提交合格」）时，pending 或 inspecting 都可 prepare 合格。「检验合格 / 质检合格」单独出现多是查状态（“质检合格的有几条”），不算办理。两种动作都必须经过确认卡，不能口头宣布已完成。

不合格、部分合格、拒收退货本期只澄清，不 prepare。

## 工作流

对象清楚就直接查。「质检待办 / 领取质检 / 检验合格」本轮必须 `CALL_TOOL` `query_erp_outsource_quality_tasks`，禁止 `CONVERSATION`。只问数量时不要 prepare。办理时按用户明确意图选择领取或合格。

| 意图 | 工具 |
| --- | --- |
| 质检待办、领取、合格 | `query_erp_outsource_quality_tasks` |
| 领取 | `prepare_erp_outsource_quality_claim`（要 taskId） |
| 全检合格（判合格 / 确认合格 / 检验通过） | `prepare_erp_outsource_quality_pass`（要 taskId） |

同一模具多单时列出候选。用户否认确认卡则停。

## 边界

仓库收货入库用 `outsource_warehouse_inbound`。厂内工序质检不是本 Skill。
