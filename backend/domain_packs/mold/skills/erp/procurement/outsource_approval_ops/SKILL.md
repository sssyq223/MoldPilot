# 委外下单审批

办理零件/模具委外**超区间下单审批**。采购主管（王群）和总经理（李辉）共用本 Skill；各人只批自己节点。

## 固定顺序

```text
采购员提交成交价
  → 【采购主管】通过 / 驳回
  → 【总经理】通过 / 驳回
  → 全部通过后加工商待接单
```

只有加工商报价**超出直接接单区间**、采购员提交成交价后才有本审批。区间内免审，不会出现在本待办。工序委外没有下单审批，不要按这套办。

## 本角色节点

| 登录角色 | 可批节点 |
| --- | --- |
| 委外采购主管 | 采购主管审批 |
| 总经理 | 总经理审批 |

看不到对方节点的待办，也不能代批。

## 工作流

对象清楚就直接查，不必先复述。「待我审批 / 委外审批 / 审批待办」本轮必须 `CALL_TOOL` `query_erp_outsource_approval_todos`，禁止 `CONVERSATION`。只问数量时不要 prepare。

| 意图 | 工具 |
| --- | --- |
| 待我审批、审批中有哪些 | `query_erp_outsource_approval_todos` |
| 通过 | `prepare_erp_outsource_approval_pass`（要 taskId） |
| 驳回 | `prepare_erp_outsource_approval_reject`（要 taskId 和原因） |

同一模具多单时列出候选，用户选定 **taskId** 后再 prepare。状态已变或不是本节点只说明原因，不 prepare。用户否认确认卡则停。

## 边界

不填价、不发询价、不定标。采购办理用 `outsource_buyer_ops`。整套委外合同用 `full_outsource_review`。
