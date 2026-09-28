# 采购与供应商迁移收口矩阵

这份矩阵把 RYmodle 两个业务包的 **88 个源工具**一次性归档，明确区分源工具数量、目标 Tool 数量和无法安全迁移的排除项。

## 总数

| 源包 | 源工具 | 已直接迁移或合并到目标链 | 当前排除项（缺 Agent 安全契约） |
|---|---:|---:|---:|
| `procurement_work` | 56 | 38 | 18 |
| `supplier_work` | 32 | 23 | 9 |
| **合计** | **88** | **61** | **27** |

61 个源工具合并为 MoldPilot 当前的 **51 个目标 Tool**，因为源项目把同一 ERP 业务动作拆成多个薄封装（例如查询、预览、提交、审批）；目标项目按 ERP 业务边界合并为只读查询、人工确认提案和单一回执链。

本轮新增的 9 个只读/审批入口为采购订单详情、发货指令、供应商资格、价格目录、价格审批、可发货明细、供应商待办汇总、拒单原因和逐行价格审批决定。它们都已经具备 Typed Schema、权限/部门目录、Skill 入口和显式 ERP Adapter 路由。

## 已迁移或合并

采购侧已覆盖数量变更、采购申请/决策、钢料拆分、询价、报价比较、拆组调整、候选顺位、临时分组、无人接单重采、重采系统价和五金定标；采购订单、供应商资格、价格目录和价格审批查询也已接入。供应商侧已覆盖订单/报价/发货查询、接单/拒单、报价提交、发货创建、交付异常、可发货明细、待办汇总、拒单原因以及价格访问和价格审批。

每个已迁移入口都同时具备：

- `migration_tools.py` 中的 Pydantic Typed Schema 与 ERP 来源/限制说明；
- `tool_gateway.py` 中的工具、Skill、权限、部门和能力类型注册；
- 写动作的人工确认提案、确认前 ERP 重新读取和幂等回执；
- `erp_adapter.py` 中的固定业务路由，禁止 Agent 传入任意 URL、SQL 或 ERP Token。

对应目标入口集中在：

- `backend/domain_packs/mold/erp_adapter.py`
- `backend/domain_packs/mold/tools/erp/procurement/migration_tools.py`
- `backend/domain_packs/mold/tool_gateway.py`
- `backend/domain_packs/mold/proposal_handlers.py`
- `backend/domain_packs/mold/skills/erp/procurement/`

## 明确排除的源工具

以下 27 个源工具不再以“待开发”混入已迁移数量，而是登记为当前版本的排除项。排除理由是：源实现依赖 RYmodle 本地采购表/供应商表或页面编排；ERP 虽可能存在普通管理页面路由，但当前没有同时满足 MoldPilot 要求的 Agent 专用 VO、供应商身份边界、版本校验、幂等回执和人工确认契约。它们不能用自由 JSON 或本地镜像假装完成。

### 采购侧 18 个

`purchase_claim_list`、`purchase_processing_get`、`purchase_delivery_instruction_confirm`、`purchase_request_prepare`、`purchase_order_prepare`、`purchase_request_submit`、`purchase_request_approve`、`purchase_request_reject`、`purchase_drawing_get`、`purchase_attached_costing_get`、`purchase_attached_costing_prepare`、`purchase_attached_costing_submit`、`purchase_direct_assign`、`purchase_reassign`、`purchase_hardware_split_prepare`、`purchase_hardware_split_submit`、`purchase_hardware_split_rebuild_prepare`、`purchase_hardware_split_rebuild_submit`。

其中采购申请草稿/审批、附图方料核价、直接指派和五金拆分虽然存在 ERP 管理端接口，但尚未提供可由 MoldPilot 安全调用的完整 Agent 契约；本轮不绕过该缺口。

### 供应商侧 9 个

`supplier_quantity_change_list`、`supplier_progress_report`、`supplier_quote_reject`、`supplier_quality_reship`、`supplier_quote_compare`、`supplier_price_draft_save`、`supplier_qualification_confirm`、`supplier_price_submit`、`supplier_price_disable`。

其中供应商数量变更列表、报价拒绝、质量补发和价格全生命周期动作需要供应商身份/价格审批 VO 的进一步联调；报价比较已由采购侧 ERP 报价比较查询覆盖其只读证据，但源工具带有供应商页面特定编排，暂不另造重复入口。

## 收口判定

“迁移完成”在本项目中定义为：88 个源工具都有明确状态；61 个已迁移/合并源工具经过目标权限、Typed Schema、人工确认和 ERP Adapter 注册；27 个排除项有逐项名称和具体契约缺口；相关 Skill、矩阵和测试同步。后续若 ERP 提供上述 Agent 安全契约，应按同一确认/回执模式逐项解除排除，不得把普通 UI 路由直接暴露给模型。
