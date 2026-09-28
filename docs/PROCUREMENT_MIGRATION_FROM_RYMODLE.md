# 原材、五金与供应商业务迁移技术文档

**文档状态：** 迁移设计基线

**当前实现状态（本分支）：** 已完成第一阶段只读上下文、采购决策/五金报价/正式下单/钢料拆单和供应商交期变更的受控 Adapter、工具注册与人工确认卡；真实 ERP 联调、接口字段最终核对和供应商门户身份验证仍需在 ERP 测试环境完成。

**适用项目：** `D:\mold-agent\MoldPilot`

**来源项目：** `C:\Users\LENOVO\Desktop\RYmodle`

**范围说明：** 本文只迁移原材、五金、供应商相关的业务合同、工具和 Skill。底层 Harness、会话编排运行时、模型接入和前端工作区不在迁移范围内。

## 1. 迁移结论

迁移采用“业务契约迁移、ERP 事实归 ERP、Agent 负责受控编排”的方案：

1. 不复制 RYmodle 的 Harness、Tool Runtime、Skill Runtime 或 ERP 直连数据库实现。
2. 不把 RYmodle 的 `purchase_order`、`supplier_shipment`、库存、入库、质检等表迁成 MoldPilot 的第二套权威台账。
3. 将 RYmodle 的业务链拆成 MoldPilot 的 ERP Adapter、MCP Tool、Skill 和 Agent BPM 人工确认四层。
4. ERP 继续作为采购订单、拆单、供应商接单、发货、入库、库存和质检事实的唯一来源；MoldPilot 只保存提案、审批快照、执行回执和审计证据。
5. 所有会改变 ERP 事实的动作都必须经过“查询真实对象 → 生成操作提案 → 人工确认 → 适配器调用 ERP → 保存回执”的闭环。

这与 MoldPilot 的产品约束一致：原材、五金、委外属于 ERP 已有能力，Agent 通过新受控适配器复用，不能在 Agent 侧重做一套采购 ERP。详见 [产品开发约束](./PRODUCT_CONTRACT.md) 和 [ERP 能力复用与开发归属核对](./ERP_CAPABILITY_REVIEW.md)。

## 2. 当前基线与差异

### 2.1 RYmodle 中应迁移的能力

源项目已经把采购工具和 Skill 串成了两条主业务链：

- **原材/钢料链：** 采购申请或领料 → 人工确认 → 加工信息 → 拆单预览 → 拆单提交或整单不拆 → 采购决策 → 下单或关闭空组 → 送货地址 → 供应商接单/拒单/发货 → 仓库到货、入库、质检、退货/返工/复检。
- **五金链：** 采购申请 → 拆单 → 询价 → 供应商报价 → 报价比较 → 定标 → 五金报价审批 → 采购订单 → 供应商履约 → 仓库和质检。
- **供应商异常链：** 供应商拒单 → ERP 自动重分配；候选耗尽后进入补采、价格预览、采购员提交、经理/总经理审批和重新下单。
- **供应商价格链：** 价格查询/比较 → 价格草稿 → 人工提交 → 价格审批 → 生效或驳回。
- **供应商协同链：** 供应商订单接单/拒单、报价任务、可发货量、发货单、进度、数量变更、交期调整、质量补发和异常反馈。

源项目中与本迁移直接相关的已实现包包括：

| 源 Skill/Tool 包 | 迁移范围 | 目标形态 |
|---|---|---|
| `steel-purchase` | 原材/钢料拆单、决策、下单前后衔接 | `raw_material_tools.py` + `steel_purchase` Skill |
| `hardware-purchase` | 五金申请、询价、报价、比较、下单 | `hardware_tools.py` + `hardware_purchase` Skill |
| `hardware-award-approval` | 五金定标和报价审批 | Tool 提案 + MoldPilot 通用 BPM |
| `supplier-collaboration` | 供应商接单、报价、发货、进度和异常 | `supplier_tools.py` + `supplier_collaboration` Skill |
| `supplier-price` | 供应商价格查询、比较、草稿和提交 | `price_tools.py` + 采购价格 BPM |
| `supplier-price-approval` | 价格审批、驳回和生效 | MoldPilot 通用 BPM，不调用 ERP 审批流 |
| `purchase-adjustment` | 数量调整、拆单调整、补采和重分配 | `purchase_exception_tools.py` + 异常/补采 BPM |

`ordinary-material-purchase` 和 `drawing-part-purchase` 在源项目中不是当前可直接迁移的已完成能力。它们应作为后续独立范围，不得在本次迁移中标记为已交付。

### 2.2 MoldPilot 已有基础

MoldPilot 已具备迁移所需的四类基础：

- `backend/domain_packs/mold/erp_adapter.py` 已有 `procurement_execution_context()`，可以读取 ERP 采购订单、供应商发货、入库、库存流水和质量上下文，并保留 `source_system/source_endpoint/source_ref/as_of`。
- `backend/domain_packs/mold/tool_gateway.py` 集中注册 `TOOLS`、`SKILLS`、权限、部门和执行分派，可作为新工具的唯一注册入口。
- `backend/domain_packs/mold/skills/erp/procurement/` 已有采购申请核对、价格上下文、发货风险、整套委外核对等 Skill 目录和写法。
- MoldPilot 已有 Agent 侧提案/BPM 约定，以及供应商资料交接、进度报告、委外变更等证据登记工具，可继续承载协同证据，不与 ERP 供应商执行台账混用。

同时存在一个必须处理的结构性差异：`backend/domain_packs/mold/erp/procurement/procurement.py` 和 `erp/core/domain_models.py` 中有本地采购订单、订单行、发货、收货、库存等模型。它们不能直接成为本次迁移后的 ERP 事实来源。迁移前要逐个标记为“Agent 自有记录”或“ERP 镜像试验记录”；后一类冻结写入并逐步改为适配器读回。

## 3. 目标架构

### 3.1 分层职责

```text
用户目标
  ↓
Skill：业务步骤、证据要求、人工确认点、异常恢复
  ↓
MCP Tool：查询、提案、确认后的受控动作
  ↓
ERP Adapter：身份、权限、对象范围、版本、幂等、接口调用
  ↓
ERP：采购申请、采购决策、订单、供应商履约、入库、库存、质检权威事实
```

MoldPilot Agent BPM 与 ERP 审批流分开：MoldPilot 负责审批材料、节点、人工决定和审计；ERP Adapter 只在得到人工确认后调用 ERP 的业务接口。审批通过不能直接解释为 ERP 已下单，必须以 ERP 返回的业务回执为准。

### 3.2 建议目录

```text
backend/domain_packs/mold/
├─ erp_adapter.py                         # 增加显式采购/供应商 Adapter 方法
├─ tools/erp/procurement/
│  ├─ raw_material_tools.py               # 原材/钢料查询、拆单、决策和下单提案/执行
│  ├─ hardware_tools.py                   # 五金询价、报价、比较、定标和下单提案/执行
│  ├─ supplier_tools.py                   # 供应商接单、报价、发货、进度和数量/交期动作
│  ├─ price_tools.py                      # 价格查询、比较、草稿和提交提案
│  ├─ purchase_exception_tools.py         # 重分配、补采、拆单调整和异常动作
│  └─ contracts.py                        # 统一输入、输出、来源和回执 DTO
└─ skills/erp/procurement/
   ├─ steel_purchase/SKILL.md
   ├─ hardware_purchase/SKILL.md
   ├─ supplier_collaboration/SKILL.md
   ├─ supplier_price_governance/SKILL.md
   └─ purchase_exception_repurchase/SKILL.md
```

目录只是业务分层建议，不要求把每个源包机械地复制成一个 Python 包。工具必须在 `tool_gateway.py` 注册，Skill 必须能被该项目现有 Skill 扫描和权限过滤逻辑发现。

## 4. 工具契约设计

### 4.1 三种工具形态

每个业务动作按以下三种形态实现：

| 形态 | 示例 | 是否改变 ERP | 说明 |
|---|---|---:|---|
| `query_*` | `query_steel_purchase_context`、`query_supplier_quote_tasks` | 否 | 读取真实 ERP 对象，返回来源和快照时间 |
| `prepare_*` | `prepare_steel_split`、`prepare_hardware_award` | 否 | 生成可展示的提案/确认卡，冻结输入快照 |
| `execute_*` | `execute_steel_split`、`execute_supplier_delivery` | 是 | 只接受已确认提案 ID，调用 Adapter 并保存执行回执 |

模型不应直接调用带任意 URL、SQL、ERP Token 或自由 JSON 的通用工具。每个工具必须使用 Pydantic 输入模型、固定业务对象标识和明确权限。

### 4.2 原材/钢料工具

建议的最小工具集合：

| 工具 | 作用 | 关键校验 |
|---|---|---|
| `query_raw_material_purchase_context` | 查询申请、钢料分组、加工信息、决策、订单和后续履约 | 项目/模具范围、采购负责人、来源快照 |
| `prepare_raw_material_split` | 生成拆单或整单不拆预览 | 数量、料号、加工方式、ERP 分组版本 |
| `execute_raw_material_split` | 提交 ERP 拆单或整单不拆路由 | 人工确认、来源版本、幂等键 |
| `prepare_purchase_decision` | 准备采购决策和下单/关闭空组提案 | 供应商、价格、交期、异常项 |
| `execute_purchase_decision` | 调用 ERP 决策确认或生成订单接口 | 权限、对象版本、ERP 回执 |
| `query_delivery_address` / `prepare_delivery_address` / `execute_delivery_address` | 查询、确认和提交送货地址 | 地址版本、适用订单、人工确认 |

钢料拆单与整单不拆必须保留 ERP 的实际路由，不能在 Agent 侧只保存一个“拆单分组”而跳过 ERP 业务处理。

### 4.3 五金工具

建议的最小工具集合：

| 工具 | 作用 |
|---|---|
| `query_hardware_purchase_context` | 查询五金申请、采购分组、已有报价和采购状态 |
| `prepare_hardware_inquiry` / `execute_hardware_inquiry` | 生成并提交询价任务 |
| `query_supplier_quote_tasks` / `query_supplier_quotes` | 查询供应商报价任务和报价明细 |
| `prepare_hardware_quote_compare` | 形成供应商、价格、交期和风险比较快照 |
| `prepare_hardware_award` / `execute_hardware_award` | 定标提案和 ERP 定标提交 |
| `prepare_hardware_quote_approval` | 生成 MoldPilot BPM 审批材料 |
| `execute_hardware_order` | 在人工审批和 ERP 条件满足后生成采购订单 |

五金报价审批使用 MoldPilot 通用 BPM。ERP 端若已有报价提交或生成订单接口，只通过受控 Adapter 调用，不把 ERP 待办、审批决定接口接入 Agent BPM。

### 4.4 供应商工具

供应商工具必须区分“ERP 供应商履约事实”和“MoldPilot 协同证据”。前者进入 Adapter，后者复用现有 `full_outsource_tools.py` 等 Agent 侧证据工具。

建议集合：

- 查询供应商订单、订单状态、接单状态和拒单原因。
- 准备/执行供应商接单、拒单和拒单说明。
- 查询报价任务、报价明细，准备/执行报价提交或报价驳回。
- 查询可发货量、发货单、交期和进度。
- 准备/执行发货、交期变更、数量变更和质量补发。
- 查询供应商异常、重分配候选和补采建议。

供应商门户账号、供应商身份映射或供应商侧 API 若尚未具备，不得用 Agent 本地状态模拟“供应商已接单/已发货”。应先交付采购侧只读和提案能力，并将供应商侧执行列为接口前置条件。

## 5. ERP Adapter 规范

### 5.1 显式方法而非通用转发

在 `erp_adapter.py` 增加按业务命名的显式方法，例如：

```python
query_purchase_decision(...)
preview_raw_material_split(...)
submit_raw_material_split(...)
query_hardware_quotes(...)
submit_hardware_quote_approval(...)
create_purchase_order(...)
query_supplier_delivery(...)
submit_supplier_acceptance(...)
```

每个方法固定 ERP endpoint、HTTP 方法、权限、请求 DTO、响应 DTO 和错误码映射。禁止根据模型传入的字符串拼 URL、传 SQL 或选择任意 ERP endpoint。

### 5.2 调用前后校验

Adapter 调用必须依次完成：

1. 映射当前 MoldPilot 用户与 ERP 用户身份。
2. 使用 `verified_identity()` 校验用户、业务权限和数据范围。
3. 校验项目号、模具号、采购分组、供应商和料号属于当前业务对象范围。
4. 比对提案中的 `source_version/source_updated_at/source_hash` 与 ERP 当前对象版本。
5. 生成确定性的 `request_key`，在本地唯一约束下防止重复提交。
6. 调用 ERP 接口并记录 HTTP 状态、ERP 业务状态、`source_ref` 和 `source_as_of`。
7. 超时或网络中断时进入 `UNKNOWN`，先查询 ERP 权威状态再决定是否重试，不能直接报告成功。

### 5.3 幂等与并发

所有写操作至少需要：

- `operation_id`：一次 Agent 操作的稳定标识。
- `request_key`：同一业务动作的幂等键，建议由业务对象、动作、版本和提案 ID 计算。
- `source_version`：ERP 对象版本或更新时间。
- `actor_id`：实际确认人，而不是模型或服务账号。

若 ERP 接口本身不支持幂等键，Adapter 必须在提交前后做权威状态查询，并把“提交结果未知”保留为可对账状态。不能通过本地重复写入来掩盖 ERP 的幂等缺口。

## 6. Agent BPM 与数据模型

### 6.1 建议新增的最小记录

优先复用 MoldPilot 现有 Proposal/BPM 记录。若通用记录无法承载 ERP 回执，再新增两个轻量实体：

`ErpActionProposal`

- `id`、`operation`、`business_kind`
- `project_no`、`mold_no`、`source_ref`
- `source_version`、`source_hash`、`snapshot_json`
- `requested_by`、`required_permission`、`status`
- `created_at`、`expires_at`

`ErpExecutionReceipt`

- `proposal_id`、`operation_id`、`request_key`
- `source_system`、`source_endpoint`、`source_ref`
- `source_as_of`、`erp_status`、`response_json`
- `status`：`PENDING/SUCCEEDED/FAILED/UNKNOWN/RECONCILED`
- `actor_id`、`tool_version`、`skill_version`
- `evidence_hash`、`created_at`、`reconciled_at`

`request_key` 必须唯一。响应中可保存必要的 ERP 回执和审计字段，但不保存 ERP Token，不把完整 ERP 订单、库存和供应商台账复制到 Agent 库。

### 6.2 现有模型处置

迁移前对下列模型做归属审计：`PurchaseOrder`、`OrderLine`、`SupplierShipment`、`GoodsReceipt`、`StockBalance`、`StockMovement`、`ReceiptInspection`。

- 如果记录是 MoldPilot 自有的审批、提案或证据，保留并改名/加注释，避免与 ERP 事实混淆。
- 如果记录只是 ERP 镜像，停止作为写入目标；查询改走 Adapter，并保留 `source_ref/source_as_of`。
- `create_execution_order()` 不得继续用于迁移后的 ERP 正式下单路径；正式下单必须走 `prepare_* → 人工确认 → execute_* → ERP 回执`。
- 已存在的本地草稿不得自动模糊匹配 ERP 订单。只能在明确的业务映射表和人工确认下关联 `source_ref`。

## 7. Skill 编排规范

每个 Skill 文件只描述业务流程，不写 Python、SQL、凭据或底层 URL。必须包含：

1. 适用条件和输入证据。
2. 查询顺序和前置条件。
3. 可调用的 `query_*`、`prepare_*`、`execute_*` 工具。
4. 必须人工确认的节点及确认内容。
5. ERP 回执要求和成功判定。
6. 拒单、版本冲突、超时、候选耗尽和重复提交的恢复方式。
7. 不能做的事情，例如不能把草稿当正式订单、不能把 Agent 审批通过当 ERP 下单成功。

推荐流程：

```text
识别项目/模具/业务对象
  → 查询 ERP 权威上下文
  → 展示事实、来源、差异和风险
  → 生成带快照的操作提案
  → 等待用户确认
  → 重新校验权限、版本和范围
  → 调用 ERP Adapter
  → 保存 ERP 回执
  → 再查询确认最终状态
```

在 `tool_gateway.py` 中增加 `TOOLS` 描述、权限和业务类别；在 `SKILLS` 中增加 Skill 与工具组、可选工具和激活词；同时补齐部门映射、能力目录和工具显示名称。不要绕过集中注册直接在 Skill 文件中动态导入工具。

## 8. 分阶段实施计划

### 阶段 0：契约盘点与归属冻结

产出一份逐项矩阵：源工具/Skill、目标工具、ERP endpoint、权限、输入输出、版本字段、幂等策略、人工确认点、ERP/Agent 真相归属、验收证据。完成本地采购模型归属审计，并冻结本地正式下单写入。

### 阶段 1：只读统一上下文

先交付原材、五金、供应商的查询工具和统一 DTO：申请、采购分组、报价、订单、供应商履约、入库、库存和质检。所有结果带 `source_ref/source_as_of`，并用 fake Adapter 和 ERP 沙箱响应做契约测试。

### 阶段 2：原材/钢料执行链

实现拆单预览、整单不拆、拆单提交、采购决策、订单/空组关闭和送货地址。每个写动作都通过 MoldPilot BPM 或确认卡，ERP 返回回执后才结束 Skill。

### 阶段 3：五金询价、定标和下单

实现询价任务、供应商报价、比较、定标、五金报价审批和订单生成。审批材料冻结报价快照，审批通过后仍需 ERP Adapter 回执。

### 阶段 4：供应商履约协同

接入供应商接单/拒单、报价、发货、进度、数量/交期变更、质量补发和异常查询。供应商身份或门户能力不足时，只开放查询和提案，禁止本地伪造执行状态。

### 阶段 5：价格、补采和异常

实现价格比较与价格审批、供应商拒单重分配、候选耗尽补采和采购调整。将审批节点放入通用 BPM，不另造一套源项目审批引擎。

### 阶段 6：联调、切换和对账

在 ERP 测试环境验证真实权限、接口幂等、超时恢复和回执字段；选定一组原材、一组五金和一组供应商异常做端到端试运行；通过对账后按业务能力逐项开放。

## 9. 验收标准

| 维度 | 必须证明 |
|---|---|
| 业务契约 | 原材拆单/整单不拆、五金询价定标、供应商履约与异常链路都有工具和 Skill 对应关系 |
| 权威来源 | 订单、发货、入库、库存、质检均显示 ERP `source_ref/source_as_of`，本地记录不是权威台账 |
| 权限 | 无权限用户无法查询越权对象或执行；用户身份和 ERP 身份映射可审计 |
| 人工确认 | 每个 ERP 写动作都有可读提案、确认人、确认时间和确认快照 |
| 并发 | 版本过期会阻止执行并要求重新查询，不覆盖他人变更 |
| 幂等 | 重复点击、重试和超时恢复不会产生重复 ERP 订单或重复发货 |
| 失败恢复 | ERP 拒绝、供应商拒单、候选耗尽和网络未知状态都有明确状态和下一步 |
| 数据边界 | 没有双写本地订单/库存；Agent 只保存提案、回执、证据和必要索引 |
| 可观测性 | 日志能关联 `operation_id/request_key/source_ref/actor_id/tool_version/skill_version`，且不泄露凭据 |
| 真实联调 | 合成测试与真实 ERP 测试分开记录，不能用模拟回执冒充真实验收 |

## 10. 切换、回滚与风险

### 10.1 切换策略

- 按能力增加 feature flag 和角色白名单，先只读，再开放提案，最后开放少量真实执行。
- 切换前停止本地镜像模型的正式订单写入；未完成的本地草稿只在人工确认映射后关联 ERP。
- 不做 Agent 与 ERP 双写。切换后查询以 ERP 为准，本地只保存执行回执。
- 迁移期间保留旧查询工具作为只读对照，但不能让旧工具继续产生正式 ERP 事实。

### 10.2 回滚策略

回滚只关闭 MoldPilot 的目标能力开关，不恢复本地采购台账作为真相。对 `UNKNOWN` 的 ERP 操作先查询和对账，再决定补偿动作。已成功写入 ERP 的订单、拆单或发货不能通过删除 Agent 记录回滚，必须走 ERP 正式撤销/调整业务。

### 10.3 主要风险

1. 源项目部分实现直接依赖 ERP 数据库；迁移到 API Adapter 后，接口语义可能与源代码假设不一致，必须以 ERP 接口契约和真实联调为准。
2. MoldPilot 当前本地采购模型与 ERP 台账存在重复风险，若不先冻结写入会形成双账。
3. 供应商门户身份、报价和发货接口可能未开放，供应商侧写操作应按能力拆分延后。
4. 源项目的验证报告不是当前源代码的可靠证明，迁移验收必须重新生成 MoldPilot 侧的契约、沙箱和真实联调证据。
5. ERP 的价格、拆单和订单接口可能要求额外的业务状态或审批条件，不能仅凭工具名称推断可执行。

## 11. 首批落地任务

建议按以下顺序开始编码：

1. 建立 `contracts.py`，定义采购分组、报价、供应商履约、提案和回执 DTO。
2. 为 `erp_adapter.py` 补齐采购决策、报价、订单和供应商查询的显式方法，先只读。
3. 建立 `query_raw_material_purchase_context`、`query_hardware_purchase_context`、`query_supplier_procurement_context` 三个查询工具，并接入现有权限和对象范围校验。
4. 建立 `ErpActionProposal/ErpExecutionReceipt`，或将字段并入现有通用 Proposal/BPM 记录。
5. 编写三个 Skill：`steel_purchase`、`hardware_purchase`、`supplier_collaboration`，先完成只读和提案路径。
6. 对 `create_execution_order()`、本地 `PurchaseOrder`、`SupplierShipment` 和库存模型做归属标记，禁止它们继续充当迁移后 ERP 正式写入入口。
7. 完成阶段 0 矩阵后，再按阶段 2 至阶段 5 开放真实写操作。

## 12. 参考文件

- [产品开发约束](./PRODUCT_CONTRACT.md)
- [ERP 能力复用与开发归属核对](./ERP_CAPABILITY_REVIEW.md)
- [Mold 领域包说明](../backend/domain_packs/mold/README.md)
- [ERP Adapter](../backend/domain_packs/mold/erp_adapter.py)
- [Tool Gateway](../backend/domain_packs/mold/tool_gateway.py)
- [采购领域模型](../backend/domain_packs/mold/erp/core/domain_models.py)
- [本地采购实现（迁移前需归属审计）](../backend/domain_packs/mold/erp/procurement/procurement.py)
