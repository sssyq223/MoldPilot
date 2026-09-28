# 原材、五金与供应商迁移：第二阶段差异矩阵

**基线分支：** `feature/procurement-migration`

**阶段目标：** 在第一阶段采购决策、报价审批、正式下单、钢料拆单和交期变更基础上，补齐采购工作台和供应商门户的核心闭环入口。

## 已实现

| 业务链 | MoldPilot 工具 | ERP 固定路由 | 说明 |
|---|---|---|---|
| 采购工作台 | `query_purchase_workbench_context` | `GET /purchase/request/list`、`GET /purchase/workbench/split/list`、`GET /purchase/workbench/split/{request_id}` | 查询采购申请、责任人、版本和拆单工作台 |
| 采购认领 | `prepare_purchase_claim` | `POST /purchase/workbench/requests/{request_id}/claim` | 人工确认后认领，不在 Agent 建本地责任台账 |
| 五金询价 | `prepare_hardware_inquiry` | `POST /purchase/decision/{group_id}/inquiry` | 使用 ERP 候选供应商发送询价 |
| 供应商门户查询 | `query_supplier_portal_context` | `GET /supplier/quote-task/list`、`GET /supplier/purchase-order/list`、`GET /supplier/delivery/list` | 按当前 ERP 供应商身份读取 |
| 供应商报价 | `prepare_supplier_quote_submit` | `POST /supplier/quote-task/{task_id}/submit` | 逐行单价、税率、交期必须结构化填写 |
| 供应商接单/拒单 | `prepare_supplier_order_decision` | `PUT /supplier/purchase-order/{order_id}/accept|reject|decision` | 支持整单和部分接单提案 |
| 供应商发货 | `prepare_supplier_delivery_create` | `POST /supplier/delivery` | 校验订单明细、收货点和数量后提交 |
| 供应商异常 | `prepare_supplier_exception` | `POST /supplier/exception` | 覆盖延期、短缺、质量和其他异常反馈 |

所有写工具都遵循：ERP 查询 → MoldPilot 提案 → 本人确认 → 版本重查 → Adapter 调用 → 保存 ERP 回执。供应商门户操作要求当前 ERP Token 是供应商身份；采购员身份不能伪造供应商状态。

## 数量变化

第二阶段新增 **8 个 Tool**、**2 个 Skill**：

- Tool：2 个查询、6 个受控操作。
- Skill：`purchase_workbench`、`supplier_portal`。
- 原有 `steel_purchase`、`hardware_purchase`、`supplier_collaboration` 已增加跨 Skill 的转接规则。

## 尚未迁移

以下能力继续保留在下一阶段：采购报价比较和逐行定标审批的完整专用工具、供应商价格资格/有效价审批、数量变更决定、拒单候选耗尽后的重采和双级审批、附图件核价、质量补发及真实 ERP 联调验收。它们不能由本阶段的上下文查询或提案卡推断为已完成。

## 验证边界

本阶段测试使用 MockTransport 验证固定路由、字段白名单、来源标识和确认卡协议。真实 ERP 测试环境仍需验证供应商账号权限、请求字段与业务状态机；没有真实回执时不报告业务成功。
