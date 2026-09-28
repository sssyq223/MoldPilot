# 采购迁移第三阶段：采购调整

**基线分支：** `feature/procurement-migration`

本阶段在采购工作台和供应商门户之后，接入 ERP 的采购拆组调整边界。调整数据继续以 ERP 为唯一事实源，MoldPilot 只保存提案和操作回执。

## 新增能力

| 业务链 | MoldPilot 工具 | ERP 固定路由 | 说明 |
|---|---|---|---|
| 拆组调整查询 | `query_purchase_adjustment_context` | `GET /purchase/workbench/split-adjustments/context/{request_id}`、`GET /purchase/workbench/split-adjustments`、`GET /purchase/workbench/split-adjustments/{adjustment_id}` | 查询原组上下文、版本、快照和历史 |
| 拆组调整预览 | `prepare_purchase_split_adjustment` | `POST /purchase/workbench/split-adjustments/preview` | 按原组版本、快照和完整数量分配生成确认提案；预览不等于正式提交 |
| 拆组调整提交 | `prepare_purchase_split_adjustment_submit` | `POST /purchase/workbench/split-adjustments/{adjustment_id}/submit` | 对 ERP 已生成的调整提案重新校验版本后提交 |
| 临时分组维护 | `prepare_purchase_temporary_group_save`、`prepare_purchase_temporary_group_delete` | `PUT/DELETE /purchase/workbench/split-adjustments/context/{request_id}/temporary-groups...` | 保存或删除未转正式调整的 ERP 草稿 |
| 无人接单重采 | `query_purchase_repurchase_context`、`prepare_purchase_repurchase_submit` | `GET /purchase/manual-dispatch/by-order/{order_id}`、`GET /purchase/manual-dispatch/{batch_id}`、`PUT .../draft`、`POST .../submit` | 读取重采批次、逐件指定供应商和价格，提交两级审批 |
| 候选顺位调整 | `query_purchase_supplier_ranking_context`、`prepare_purchase_supplier_rank_adjustment` | `GET /api/agent/procurement/split-groups/{group_id}/supplier-ranking`、`POST /api/agent/procurement/actions/supplier-rank-adjustment/{preview,proposals}` | 用 ERP 快照生成候选顺位提案，不直接写候选表 |

新增 Skill：`purchase_adjustment`。数量分配、目标组标识、重采明细和原因使用严格结构化输入；原采购组不会被本地改写。版本冲突、权限不足或 ERP 结果不明时停止本轮操作。

## 边界

本阶段没有把本地模拟数据当作 ERP 调整事实，也没有把“预览”报告成“已提交”。价格资格审批、附图件核价和质量补发仍需后续阶段接入；数量变更、五金定标和重采系统价已在第四阶段接入。真实 ERP 联调仍需使用对应供应商/采购身份验证字段与状态机。

## 验证

MockTransport 覆盖查询、字段白名单、预览和提交路由；采购迁移工具覆盖严格输入和确认卡。真实 ERP 回执未接入前，不报告业务动作已成功。
