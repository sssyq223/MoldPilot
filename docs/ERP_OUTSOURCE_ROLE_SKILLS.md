# 零件/工序委外：分角色 Skill 与功能说明

本文档整理历史会话与当前代码中的共识：**ERP 零件委外 / 工序委外执行链**在 MoldPilot 中如何按角色封装 Skill、如何隔离账号，以及各阶段进度从哪里读、谁办什么。

> **范围说明**  
> - **本文档对象**：`management-system` / ERP `module_entrust` 下的**零件委外、工序委外、模具委外（采购支线）**执行工单。  
> - **不在本文档**：`full_outsource_review`（整套委外合同与供应商协同）。  
> - **首期不做**：委外退换货闭环、对账、拆图合并等异常支线。

---

## 0. 先读：哪些已写清、哪些还不能当成 ERP 事实

| 事项 | 文档口径 |
| --- | --- |
| 角色推进哪一段 | §2、§3 是**业务分工草图**，便于写 Skill；**不是**从 ERP 代码逐条核对过的完整状态机 |
| Skill 对齐流程 | §4：办理只覆盖本角色阶段；**工具层必须再校验**，不能只靠 SKILL.md |
| 听不懂再追问 | §5：**办理**必须确认后再 prepare；**查询**在对象清楚时直接 query，不强制先复述确认 |
| 越权 | §6：隐藏 Skill **不能**当安全边界；每次工具调用要过权限、数据范围、单据状态 |
| 分叉 | §7：不只看 `outsource_type`；供料来源还看工序是否第一道（ERP `material_supply_service`） |
| 异常/并发/幂等 | §8：首期不重做退换货；写入类上线前必须有幂等与执行前重读状态。当前跟单工具**尚未**做到 |

下文阶段表、允许操作是**建议结构**。真实状态码、迁移条件以 `E:\management-system` 的 `module_entrust` 与接口行为为准，写办理 Skill 前必须对照代码，禁止把本文表格抄成枚举。

---

## 1. 设计原则（与 MoldPilot 一致）

| 原则 | 说明 |
| --- | --- |
| 一个 Skill 一段业务流 | 按**角色 + 流程段**拆 Skill，不做「一个 mega Skill 包办委外」 |
| 查询与办理分离 | 跟单查询只读；办理类 `prepare_*` → 确认卡 → 本人确认 → 调 ERP |
| 不复制 ERP 台账 | Agent 侧只读/受控投影；权威数据在 ERP |
| 权限三层 | **代码**（工具 `permission`）+ **库**（Grant + `agent_capability_assignment`）+ **Skill.md**（行为指引，不能替代鉴权） |
| 统一入口 | 全员同一 AI 会话入口；**不同账号**登录后 ToolSearch / 能力列表 / 可调工具不同 |
| 查询主轴 | 数量/待办问题可直接查当前责任域；单票查询可用模具号定位；**写入**必须落到 ERP 订单 ID（同一模具多单时先消歧） |
| 固定流程 | 分叉入口以 ERP `outsource_flow_policy` 为准；Skill 不得自创阶段名 |
| 对话 | 对象含糊才追问；用户否认当前理解则停办。查询不必等「复述确认」 |

---

## 2. 角色推进职责：谁负责哪一段、推进什么

委外工单按 **ERP 固定阶段**流转；下表说明**本角色在该阶段要做什么、办完后流程通常进入哪里**。Agent 只引导**当前登录角色**能办的那一格，并提示「下一步等谁」（只读说明，不越权代办）。

### 2.1 零件 / 模具委外（采购支线）

| 阶段（待办/状态） | 主责角色 | 推进动作（人工） | 办完后典型流向 |
| --- | --- | --- | --- |
| 待采购填报价 | 委外采购员 | 填我方报价、直接接单区间 | 待发询价 / 待报价 |
| 待发询价 / 待报价 | 委外采购员 | 选加工商、发询价；跟进报价 | 加工商报价中 |
| 加工商报价 | 委外加工商 | 提交报价 | 区间内→待接单；超区间→待下单 |
| 待下单（超区间） | 委外采购员 | 填成交价、提交下单审批 | 审批中 |
| 审批中 | 采购主管 → 总经理 | 王群、李辉按节点通过或驳回 | 全部通过→待接单；驳回→采购改成交价 |
| 待接单 | 委外加工商 | 接单或拒单 | 接单→履约；拒单→采购重选 |
| 拒单 / 全部拒单 | 委外采购员 | 重选加工商、重发询价或重派 | 回到询价或队列 |
| 供料（我方供料） | 委外仓管 | 原料发货 | 加工商确认来料 |
| 生产 / 成品发货 | 委外加工商 | 确认来料、成品发货 | 仓管待入库 |
| 成品入库 | 委外仓管 | 回厂入库登记 | 质检待检 |
| 入库检验 | 委外质检 | 检验结论、异常确认 | 合格→已交付；不合格→异常支线 |
| 已交付 | — | 跟单只读 | 结束 |

### 2.2 工序委外（差异）

| 说明 | 内容 |
| --- | --- |
| 无采购填价/询价/主管下单审批 | 决策后直接**候选队列逐家发单** |
| 采购员 | 队列耗尽后重派/协调；中间一家拒单由 ERP 自动转下一家，不必每次重选 |
| 加工商 | 只办接单/拒单；拒单后不要自己选下一家，下一家用同一套接单/拒单 |
| 仓管 | 接单后办理备料完成（物料库/半成品库）。确认后 ERP 自动收货，加工商不用再确认来料 |
| 加工商履约 | 备料完成后直接成品发货 |
| 质检 | 成品回厂后与零件委外相同 |

### 2.3 六类角色（账号与权限）

与 ERP 岗位对齐，**加工商多家共用一套 Skill**，数据范围按 `supplier_id` 隔离。

| 角色 key | 组织角色名 | 部门 | 职责摘要 | 演示账号（seed） |
| --- | --- | --- | --- | --- |
| `erp_outsource_buyer` | 委外采购员 | 采购 | 委外**责任域**内：填我方报价与区间、选商发询价、跟成交价、拒单后重选等 | `xuguili` / 徐桂利 |
| `erp_outsource_approval` | 委外采购主管 | 采购 | 零件/模具**超区间下单审批**第一岗；可读跟单，不代办采购填价 | `wangqun` / 王群 |
| `erp_outsource_gm` | 总经理 | 管理 | 零件/模具**超区间下单审批**第二岗；可读跟单 | `lihui` / 李辉 |
| `erp_outsource_processor` | 委外加工商 | 加工商 | 报价、接单/拒单、收料、生产、成品发货、异常上报（**仅本供应商**） | `SUP000001` |
| `erp_outsource_warehouse` | 委外仓管 | 仓储 | 委外**原料发出**、**成品/半成品回厂入库**（不含退换货闭环） | `xuehaifeng` / 薛海峰 |
| `erp_outsource_quality` | 委外质检 | 质检 | 回厂检验结论、质检异常确认 | `zhaodianye` / 赵殿烨 |

权限码（Grant 目录，见 `erp_outsource_roles.py`）：

- 采购员：`erp_outsource_buyer.read` / `erp_outsource_buyer.execute`
- 主管 / 总经理：`erp_outsource_approval.read` / `erp_outsource_approval.approve`，另含 `erp_outsource_buyer.read`（跟单只读）
- 加工商：`erp_outsource_processor.read` / `erp_outsource_processor.execute`（scope：`supplier_id`）
- 仓管：`erp_outsource_warehouse.*` + 通用 `warehouse.read`
- 质检：`erp_outsource_quality.read` / `erp_outsource_quality.execute`

各角色另配 `project.read`，用于定位项目/模具上下文。

**未纳入本期角色 Skill 包、但 ERP 存在的岗位**（如排产生管推委外、委外主管全链总控）：若后续需要，可单独加 Skill，不与上述角色混装。

---

## 3. 固定流程（Skill 必须对齐的 ERP 顺序）

Skill 编写时**必须引用本节顺序**，不得自创阶段名；办理类工具只允许在**当前单处于本角色可办状态**时 prepare（先 query 校验 `flowStatus` / stage）。

### 3.1 零件委外（模具委外采购支线同）

分叉依据 ERP `outsource_flow_policy`：**决策完成之后**零件/模具走询价审批链，工序走候选队列。

```text
排产推送 → 待决策 / 拆图匹配
  → 【采购员】填我方报价 + 直接接单区间
  → 【采购员】选加工商发询价
  → 【加工商】报价
      ├─ 区间内 → 免审定标下单
      └─ 超区间 → 【采购员】填成交价 → 【采购主管】下单审批
  → 【加工商】接单 / 拒单 → 拒单则【采购员】重选加工商
  → 【仓管】原料发货（我方供料时）
  → 【加工商】确认来料 → 生产（无需操作） → 成品发货
  → 【仓管】成品入库
  → 【质检】入库检验 / 异常
  → 已交付
```

### 3.2 工序委外

```text
排产推送 → 待决策 / 匹配
  → 建候选加工商队列，逐家发单（无询价、无采购主管下单审批）
  → 【加工商】接单 / 拒单（常自动转下一家）
  → 【仓管】备料完成（发货=收货，加工商不确认来料）
  → 【加工商】生产（无需操作）→ 成品发货
  → 【仓管】成品入库 → 【质检】→ 已交付
```

对话设计时需**分开说明**两类链路，避免把「待填报价/待报价/待下单/审批中」强加在工序单上。

---

## 4. Skill 与流程节点绑定（怎么写 Skill）

每个角色 **1 个（或查询+办理 2 个）Skill**，配置规则如下：

| 规则 | 说明 |
| --- | --- |
| 流程锚点 | SKILL.md 开头列出**本角色可触达的阶段**（与 §2、§3 一致），并写「不可办阶段应提示找谁」 |
| 工具白名单 | `tool_gateway` 只挂本角色 `permission` 下的 query / prepare；禁止跨角色工具 |
| 办理顺序 | **先** `query_*` 读当前状态与可办性 → **再** `prepare_*` 出确认卡 → 用户确认后才写 ERP |
| 状态校验 | prepare 前必须 query 返回的单据处于允许状态；否则只澄清，不 prepare |
| 查询 Skill | 如 `erp_outsource_followup_query`：只读，覆盖 §2 全链**看进度**，不代替任何 prepare |

| 规划 Skill（办理向） | 绑定角色 | 覆盖流程段 |
| --- | --- | --- |
| `outsource_followup_query` | 采购员、采购主管 | 全链**只读**跟单（已实现） |
| `outsource_processor_query` | 加工商 | 本供应商只读待办（已实现） |
| `outsource_buyer_ops` | 采购员 | 填价、发询价、成交价、拒单重选（已实现） |
| `outsource_approval_ops` | 采购主管、总经理 | 下单审批通过/驳回（已实现，共用一套） |
| `outsource_processor_ops` | 加工商 | 报价、接单、拒单（已实现） |
| `outsource_processor_fulfillment` | 加工商 | 零件确认收料（已实现） |
| `outsource_processor_product_ship` | 加工商 | 成品发货：按已收数量发货；回厂目标按业务规则（零件/模具/工序末道→成品库，非末道工序→半成品库） |
| `outsource_warehouse_ops` | 仓管 | 原料发货 / 工序备料完成（已实现） |
| `outsource_warehouse_inbound` | 仓管 | 回厂到货确认 + 仓储入库（已实现） |
| `outsource_quality_ops` | 质检 | 领取质检任务、提交全检合格（已实现） |

---

## 5. 查询、追问、确认、办理（状态分开）

不要把所有查询都做成「用户确认复述后才能 query」。听不懂再追问，指的是**缺对象或多义**，不是每句话都先确认一遍。

| 步骤 | 何时发生 | 是否写 ERP |
| --- | --- | --- |
| 理解 | 模型把口语收成：查还是办、模具号、零件还是工序 | 否 |
| 追问 | 办理对象无法唯一定位、同一模具多张单、查/办分不清、用户话互相矛盾 | 否 |
| Query | 数量/待办可查责任域全量；单票用模具号、工单号或查询结果中的 ID 定位 | 只读 |
| Prepare | 用户**明确要办理**，且 query 证明当前状态允许本角色操作 | 只出确认卡 |
| Execute | 用户确认卡片；后端**再次**校验权限与状态后调 ERP | 是 |
| 否认 | 用户否定模型理解或拒绝确认卡 | 停，不 prepare / 不 execute |

```text
对象清楚的查询：理解 → Query → 按 ERP 结果回答
对象不清：追问（一次 1～2 个点，优先模具号）→ 再 Query
办理：Query（锁定订单 ID + 状态）→ Prepare → 用户确认 → Execute
用户否认：改理解或停止，不写入
```

| 场景 | 行为 |
| --- | --- |
| 「模具 M123 待填报价有哪些」 | 直接 query，不必先问「对吗」 |
| 「现在有委外订单吗」且无模具号 | 直接查询当前责任域待办并回答数量；不要先追问模具号 |
| 同一模具多张委外单且用户要办理 | 列出候选，用户选定 **订单 ID** 后再 prepare |
| 想办但状态不允许 / 无权限 | 说明原因，不 prepare |

已实现的 `erp_outsource_followup_query` 仍偏「先复述再查」。后续改 Skill 时按上表收紧：**只读且对象明确则直接查**。

---

## 6. 权限：Skill 隔离不等于业务安全

MoldPilot 现有机制能挡住「没授权的工具名」：

1. **Capability**：账号没挂 Skill/工具，ToolSearch 搜不到。  
2. **Grant + `require()`**：工具声明了 `permission`，调用时没有 ALLOW 则拒绝。  
3. **维度**：`DIMENSIONS` 含 `project_id`、`category`、`warehouse_id`、`supplier_id`，供范围过滤。

三层权限之外，委外工具还必须执行行级范围和确认前重读：采购员按 ERP `purchase_buyer_scope=outsource` fail-close；加工商按稳定 `partner_code` 精确匹配当前邀请/落标供应商；审批按角色节点过滤；仓管写入仍由 ERP token 与任务权限复核。所有 prepare 在确认前重读 ERP 当前状态并校验确认卡摘要。

每次 **Query / Prepare / Execute** 在工具实现里按序检查（Skill 文案不能代替）：

```text
登录身份
  → Grant 允许该 permission
  → 数据范围（责任域 / supplier_id / warehouse_id）
  → 若是写入：ERP 当前状态仍允许该操作，且订单 ID 与用户选定一致
  → 调 ERP，把 ERP 成功/失败原样返回
```

对照实际系统，下列规则要写进工具，而不是只写在 Skill：

| 场景 | 应落地的规则（实现时以 ERP 接口为准） |
| --- | --- |
| 加工商看单、报价、接单 | 仅本 `supplier_id`；不能改其他供应商的询价/报价行 |
| 采购员查询与办理 | 委外责任域（`purchase_buyer_scope` / outsource）；主管跟单可读范围大于采购员执行范围，**不能**代填价 |
| 主管审批 | 仅审批中的下单；无映射角色组时 fail-close（零条、不可办理）；是否禁止审批自己提交的单，以 ERP 审批人规则为准 |
| 仓管发料/入库 | 仅授权 `warehouse_id` 与该供料任务上的物料；供料来源不是仓管的单不出现在发料待办 |
| 质检 | 仅已入库且待检的记录 |
| 拒单是否可撤回 | 以 ERP 是否提供撤回接口为准；没有接口则 Agent 不提供「改拒单」 |

---

## 7. 流程分叉：类型 × 供料 × 当前阶段

零件/工序分叉的**代码入口**是 `outsource_flow_policy.py`（不是 Skill 表格）：

- `part` / `mold`（以及类型缺失）：决策后 `await_buyer_quote`（采购填价再人工发询价）。  
- `operation`：决策后 `dispatch_queue`（不询价、加工商侧不展示填报价，`hides_processor_inquiry`）。

供料**不是**「接单后一律仓管发料」。`MaterialSupplyService`：

| 条件 | 允许供料来源 |
| --- | --- |
| 零件委外 | 物料库 `material_stock`，或采购直发 `purchase_direct` |
| 工序 + 第一道工序 | 同上 |
| 工序 + 非第一道 | 仅半成品库 `semi_finished_stock` |

因此工具判断「下一步是谁」时同时看：

`outsource_type` + 是否走填价流/队列流 + 供料来源（及是否第一道工序）+ 当前 `flowStatus` / 订单 stage。

任一缺失则追问或只展示 ERP 已返回字段，不默认仓管发料。

§2 的阶段表只作导航。允许操作与退出条件**未**与 ERP 状态码逐条对齐，办理 Skill 开工前用真实接口补一张对照表，再写 `prepare_*`。

---

## 8. 异常、并发、一致性

首期仍不做退换货、对账闭环。但办理类一旦调用 ERP 写接口，必须：

| 风险 | 要求 |
| --- | --- |
| 重复点击 / 网络重试 | 每个 `HumanIntent` 写 `ERPOperation`：`DISPATCHING → SUCCEEDED/REJECTED/UNKNOWN`；同 intent 已成功返回原结果，UNKNOWN 不自动重放 |
| 两人同时办同一单 | Execute 前重读状态；状态已变则失败并说明，不覆盖 |
| Agent 没收到 ERP 响应 | `ERPOperation=UNKNOWN`，阻止同一确认自动重试；以 ERP 查询回读为准，不让模型宣布成功 |
| 拒单、来料/图纸/交期异常、接口失败、无权限、状态不允许 | 向用户返回 ERP 或鉴权的真实原因；不在 Agent 另建一套异常台账代替 ERP |

完整「识别异常 → 通知责任人」工作流依赖 ERP 已有异常接口，不在第一阶段跟单查询里实现。

---

## 9. 进度与状态从哪里读（ERP 权威）

| 来源 | 用途 |
| --- | --- |
| 采购人员待办看板分类 | 与 UI Tab 对齐：全部 / 待采购填报价 / 待报价 / 待下单 / 审批中 / 待接单 / 全部拒单 / 已交付 / 异常待办 |
| `OutsourceProjectFlowStatus` / workbench `flowStatus` | 决策后、接单前的阶段码（如 `pending_buyer_quote`、`pending_order_approval`） |
| `entrust_outsource_orders.stage` 等 | 接单后履约阶段（供料、发货、入库、交付） |
| 订单行 / BOM 投影 | 零件号、名称、数量、工序、图号 |
| 价格字段 | 我方报价、直接接单区间上下限、加工商报价、成交价等（按节点是否已形成裁剪展示） |

MoldPilot 内状态词汇表：`backend/domain_packs/mold/erp/procurement/erp_outsource_followup_status.py`。

跟单查询已接 ERP 只读库：待办看板支持责任域全量数量查询，单票进度支持模具号/批次定位；查询结果提供 `nextAction`，但不会自动执行写入。

---

## 10. 分角色：Skill / 工具规划与实现状态

### 10.1 委外采购员 · `erp_outsource_buyer`

**业务办理（已实现；独立重选接口除外）**

| 场景 | 引导式操作（prepare + 确认卡） |
| --- | --- |
| 待采购填报价 | 填写我方报价、直接接单区间 |
| 待发询价 / 待报价 | 选择加工商、发出询价；跟进报价 |
| 待下单 | 超区间填写成交价并提交审批 |
| 全部拒单 / 拒单后 | 重选加工商、重发询价 |
| 责任域 | 仅 `purchase_buyer_scope` / 品类 outsource 内订单 |

**查询（已实现）**

| Skill / 工具 | 状态 | 说明 |
| --- | --- | --- |
| `outsource_followup_query` | ✅ | 责任域待办看板；有没有/有几个直接查，单票进度要模具号 |
| `query_erp_outsource_followup_board` | ✅ | 待办看板 + 零件/价格；工具内校验 `erp_outsource_buyer.read` |
| `query_erp_outsource_order_progress` | ✅ | 单票时间线 |

**办理（已实现）**

| 工具 | 确认后 |
| --- | --- |
| `prepare_erp_outsource_buyer_quote` | POST `/entrust/inquiry/{id}/buyer-quote` |
| `prepare_erp_outsource_inquiry_send` | POST `/entrust/inquiry/{id}/send` |
| `prepare_erp_outsource_final_deal` | POST `/entrust/inquiry/{id}/final-deal-price` |
| `prepare_erp_outsource_reselect` | 无独立 HTTP，确认卡 `PREPARE_ONLY` |

**账号能力**：`assign_erp_outsource_query_capabilities.py` 给 `xuguili` 挂跟单 + 采购办理；`wangqun` / `lihui` 挂跟单 + 审批办理。

---

### 10.2 委外采购主管 · `erp_outsource_approval`

**业务办理（已实现，与总经理共用 `outsource_approval_ops`）**

| 场景 | 说明 |
| --- | --- |
| 审批中 · 采购主管节点 | 通过 / 驳回；确认后调 ERP `/workflow/tasks/{id}/approve|reject` |
| 跟单 | 可读责任域进度，**不**代办填价、发询价、成交价 |

### 10.2b 总经理 · `erp_outsource_gm`

与主管共用同一套审批 Skill。查询只露出「总经理审批」节点。账号 `lihui` / 李辉。

---

### 10.3 委外加工商 · `erp_outsource_processor`

**业务办理（已实现 `outsource_processor_ops`）**

| 场景 | 说明 |
| --- | --- |
| 待报价（零件/模具） | `prepare_erp_outsource_processor_quote`；确认后 `POST /entrust/inquiry/invitation/{id}/quote` |
| 报价后区间内 | ERP 免审定标；重新查询出现待接单则提醒接单 |
| 报价后超区间 | 加工商等待；采购填成交价 → 王群 → 李辉；通过后再待接单 |
| 待接单 | `prepare_erp_outsource_processor_accept` / `_reject` |
| 零件/模具拒单 | ERP 回采购重选；采购用 `outsource_buyer_ops` |
| 工序拒单 | ERP `advance_dispatch_to_next_supplier`；下一家用同一套接/拒单 |
| 履约收料 | 见 `outsource_processor_fulfillment` |
| 成品发货 | 见下节 `outsource_processor_product_ship` |
| 异常 | **本期不做** |

**成品发货（已实现 `outsource_processor_product_ship`）**

| 细节 | ERP 事实 |
| --- | --- |
| 写入 | 确认后 `POST /entrust/fulfillment/product-shipment`，body：`order_id` + `lines[{order_part_id,qty}]`，可选物流/运单 |
| 可发数量 | `min(订单qty − 已占用发货, 已收原料 − 已占用发货)`；自找料 `material_required=false` 不卡原料 |
| 部分发货 | 仓库可先发一部分原料；加工商只确认已收到的行。未收完不得按整单数量发成品，ERP `_shippable_product_qty` 会拒超发 |
| 入库目标 | ERP `processor-inbound-v1`：**零件/模具 → 成品库；工序且明确末道 → 成品库；非末道、末道缺失或无法确认 → 半成品库并提示数据缺失**。`is_end_operation` 在发货行固化，入库只读快照。确认卡展示 ERP 行级结果，不以整单默认成品库；ERP 未返回目标则禁止提交 |
| 发货行落库 | ERP 创建发货明细时写入 `is_end_operation`，供下一步仓库分组 |
| 拒单转家 | 本 Skill 不办。工序有 `dispatch_queue_json`：同一张工单换 `supplier_id`，`dispatch_index+1`，阶段回 `pending_accept`；上一家待办取消；下一家接单后才重生该家供料。上一家未发完成品**不转走**。队列耗尽 → `rejected`，采购重派。零件/模具无队列，采购重选后再询价 |

**查询 / 办理工具**

| 工具 | 说明 |
| --- | --- |
| `query_erp_outsource_processor_product_ship` | 本加工商可发货行：订单/已收/已发/可发 + 首道/末道 T/F + 入库目标 |
| `prepare_erp_outsource_processor_product_ship` | 确认卡；`lines` 空=按可发全发；超已收 → `STATE_BLOCKED` |

**下一步（仓管入库，已实现）** 见 §10.4。

**查询（已实现）**

| Skill / 工具 | 说明 |
| --- | --- |
| `outsource_processor_query` | 仅本 `supplier_id` 可见工单；裁剪内部价、其他供应商信息；行上带 `nextAction` |
| `query_erp_outsource_processor_board` / `_progress` | 权限 `erp_outsource_processor.read` |

**隔离**：Grant scope `supplier_id`；加工商共用一套查询 + 一套办理 Skill。`SUP000001` 由 assign 脚本挂载。

---

### 10.4 委外仓管 · `erp_outsource_warehouse`

**业务办理（已实现 `outsource_warehouse_ops`）**

| 场景 | 说明 |
| --- | --- |
| 待发料（零件/模具） | `prepare_erp_outsource_warehouse_ship` → `POST /entrust/material-supply/warehouse-tasks/confirm-shipped`；之后加工商确认收货 |
| 待备料（工序） | 同一接口；确认后 ERP 自动收货并通知取货，加工商不用确认来料 |
| 热处理工序 | 必须带 `lineWeights` 实际重量 |
| 采购直发 | **不是**仓库待办，本 Skill 不办 |
| 回厂收货 | 见 `outsource_warehouse_inbound`：先 `POST /entrust/arrival-confirm/{id}/confirm`，再 `POST .../confirm-inbound`。部分行/部分数量可以；Agent 将待入库量封顶为“已确认到货−已入库−已退回”，拒收本期不办 |
| 入库目标 | ERP `processor-inbound-v1` 行级结果：零件/模具及明确末道→成品库；其余→半成品库。确认卡禁止在缺目标时提交；回执读 `inboundDetails` |

---

### 10.5 委外质检 · `erp_outsource_quality`

**业务办理（已实现 `outsource_quality_ops`）**

| 场景 | 说明 |
| --- | --- |
| 待领取 | `prepare_erp_outsource_quality_claim` → `PUT /quality/inspection/{id}/claim` |
| 待领取或质检中 / 合格 | `prepare_erp_outsource_quality_pass` → `PUT /quality/inspection/{id}/submit`（full + qualified）；ERP 允许 pending 直接提交并同时记录领取人 |
| 范围 | 仅 `source_type=processor_inbound`；厂内工序质检不办 |
| 不合格 / 退货 | **本期不办** |

---

## 11. 已实现 vs 路线（按依赖顺序）

```text
已有
  采购员 / 主管 / 总经理 / 加工商 / 仓管 / 质检 permission、演示账号
  采购/主管跟单查询（责任域看板）
  加工商查询（supplier_id + 裁内部价）
  采购办理：prepare → 确认卡 → ERP HTTP（填价/发询价/成交价）
  下单审批：主管 + 总经理共用一套 Skill，按节点过滤
  重选：确认卡 PREPARE_ONLY（无独立 HTTP）
  加工商办理：报价/接单/拒单 + 收料 + 独立成品发货（ERP 实际规则定库、部分收只能部分发）
  仓管供料：原料发货 / 工序备料完成
  仓管回厂：到货确认 + 仓储入库
  质检：领取 + 全检合格

下期
  到货拒收/破损、质检不合格退货、UNKNOWN 操作的 ERP 回读与人工核销
```

---

## 12. 账号隔离：管理员要配什么

1. **Grant**：按上表给账号 `erp_outsource_*` 权限；加工商加 `supplier_id` scope。  
2. **Capability**：在「智能体能力」中启用该角色对应的 **SKILL** 与 **TOOL**（采购/主管跟单已可由 seed 脚本写入）。  
3. **Skill 正文**：必须包含 **§5 对话契约** + 本角色 **§2 流程段**；只引导本角色可办步骤。  

用户问「这单到哪了」时：有跟单 Skill 的账号会走 `erp_outsource_followup_query`；无权限账号 ToolSearch 不应露出委外办理 Skill。

---

## 13. 相关代码与文档索引

| 路径 | 内容 |
| --- | --- |
| `backend/domain_packs/mold/erp/procurement/erp_outsource_roles.py` | 角色定义与 permission 码 |
| `backend/domain_packs/mold/skills/erp/procurement/outsource_followup_query/SKILL.md` | 采购/主管跟单查询 |
| `backend/domain_packs/mold/skills/erp/procurement/outsource_processor_query/SKILL.md` | 加工商查询 |
| `backend/domain_packs/mold/skills/erp/procurement/outsource_buyer_ops/SKILL.md` | 采购办理 |
| `backend/domain_packs/mold/skills/erp/procurement/outsource_processor_ops/SKILL.md` | 加工商报价 / 接单 / 拒单 |
| `backend/domain_packs/mold/skills/erp/procurement/outsource_processor_fulfillment/SKILL.md` | 加工商收料 |
| `backend/domain_packs/mold/skills/erp/procurement/outsource_processor_product_ship/SKILL.md` | 加工商成品发货 |
| `backend/domain_packs/mold/skills/erp/procurement/outsource_warehouse_ops/SKILL.md` | 仓管发料 / 备料 |
| `backend/domain_packs/mold/skills/erp/procurement/outsource_warehouse_inbound/SKILL.md` | 仓管回厂收货入库 |
| `backend/domain_packs/mold/skills/erp/procurement/outsource_quality_ops/SKILL.md` | 质检领取 / 合格 |
| `backend/domain_packs/mold/tools/erp/procurement/erp_outsource_warehouse_inbound_tools.py` | 仓管到货 / 入库 |
| `backend/domain_packs/mold/tools/erp/procurement/erp_outsource_quality_tools.py` | 质检领取 / 合格 |
| `backend/domain_packs/mold/skills/erp/procurement/outsource_approval_ops/SKILL.md` | 主管 / 总经理下单审批 |
| `backend/domain_packs/mold/tools/erp/procurement/erp_outsource_query_tools.py` | 跟单 / 加工商查询工具 |
| `backend/domain_packs/mold/tools/erp/procurement/erp_outsource_buyer_tools.py` | 采购 prepare / 确认 |
| `backend/domain_packs/mold/tools/erp/procurement/erp_outsource_processor_tools.py` | 加工商报价 / 接单 / 拒单 |
| `backend/domain_packs/mold/tools/erp/procurement/erp_outsource_processor_fulfillment_tools.py` | 加工商收料 |
| `backend/domain_packs/mold/tools/erp/procurement/erp_outsource_processor_ship_tools.py` | 加工商成品发货 |
| `backend/domain_packs/mold/tools/erp/procurement/erp_outsource_warehouse_tools.py` | 仓管发料 / 备料 |
| `scripts/assign_erp_outsource_query_capabilities.py` | 按角色挂 Skill/工具 |
| `backend/domain_packs/mold/erp/procurement/erp_outsource_followup_status.py` | 采购待办 Tab ↔ flowStatus |
| `backend/domain_packs/mold/tool_gateway.py` | Skill / 工具注册 |
| `scripts/seed_erp_outsource_people.py` | 实名演示账号与跟单能力挂载 |
| `skills/erp/procurement/full_outsource_review/` | 整套委外（非本文执行链） |

---

## 14. 修订记录

| 日期 | 说明 |
| --- | --- |
| 2026-09-23 | 初稿：五角色、双流程、状态来源、跟单 Skill |
| 2026-09-23 | 增补角色推进、Skill 绑定、追问契约 |
| 2026-09-23 | 对照《示例》：区分查询与办理确认；写明 Skill 隐藏≠安全；补供料分叉与写入幂等；阶段表降为草图 |
| 2026-09-23 | 落地：加工商查询 Skill、采购办理确认卡与 ERP HTTP；仓管/质检查询本期不做 |
| 2026-09-23 | 新增总经理 `lihui`；主管与总经理共用 `outsource_approval_ops` |
| 2026-09-23 | 落地加工商办理 `outsource_processor_ops`：报价、区间内外交接、接/拒单、工序拒单自动转下一家 |
| 2026-09-23 | 落地仓管供料与加工商履约：零件发料+收货协同，工序备料完成即生产/成品发货 |
| 2026-09-23 | 独立成品发货 Skill：读首道/末道 T/F，入库目标按 ERP 实际写入规则；可发量封顶已收原料；写清拒单转家 |
| 2026-09-23 | 仓管回厂到货+入库、质检领取+全检合格；写入仍走 ERP HTTP |
| 2026-09-23 | Review 优化：办理先查、委外口语可达、加工商标识精确匹配、审批 fail-close、到货量封顶、文档对齐 ERP 实际写入 |
| 2026-09-23 | 二次 Review：入库目标改回业务矩阵（零件/模具/工序末道→成品库，非末道→半成品库）；正式动作词只收祈使句，站点名词（成品发货/备料完成/到货确认/质检合格）不再把计数问句判成办理；确认写入缺幂等标识时 fail-close；未落单前的审批中/待接单行按邀请可见；ERP 责任域校验按 BIGINT 匹配 |
| 2026-09-23 | 对齐 ERP `processor-inbound-v1`：确认卡展示行级库别与缺失警告；缺目标禁止入库；回执读 `inboundDetails`，不再默认成品库 |
