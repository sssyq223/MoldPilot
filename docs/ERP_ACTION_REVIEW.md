# ERP 业务动作与衔接差异核对

日期：2026-09-14。依据提供源码静态核对，不执行旧系统代码，不连接生产接口。路径均相对 `work/erp_review_20260914/source/management-system-zhangwenjin/ruoyi-fastapi-backend`。

联调时间已由用户决定：现在先开发，后续用户启动 ERP 再统一联调；当前接口缺口继续做契约设计与模拟验证，不要求提前启动 ERP。本文“未联调”是证据状态，不是 Agent 开发阻塞条件。

## 范围决策

V1.1 FR-001～118 全部保留，逐条关联见 ERP_REQUIREMENT_REVIEW.md。报价、中标、合同上传、项目大节点、通用 BPM、异常及工程联络单，以及已确认新增采购与物流功能，均属于 Agent 开发。ERP 已有下单、拆单等具体业务能力继续复用；接口不适配时登记缺口，不擅自重建其业务台账，也不调用旧审批或异常流程绕过。

## 关键动作证据

| 动作 | 已核对的入口/实现 | 结论与适配要求 |
|---|---|---|
| 采购拆单预览 | `module_admin/controller/purchase_workbench_controller.py:276`；POST `/purchase/workbench/split/mode-preview`，校验当前用户采购范围和申请处理人 | 有现成预览能力，候选复用；POST 预览不等于正式拆单。字段、版本、权限仍须联调 |
| 拆单确认 | 同文件 `:300`，POST `/purchase/workbench/split/confirm`；`purchase_workbench_service.py:961` 转交 `purchase_split_workbench_submit_service.py:613` | 参数包含 autoRoute、拆分模式、预期版本、收货点。用户已确认按 ERP 原业务路径复用，人工确认展示所选路径及既有后续处理，Agent 记录并跟踪回执 |
| 钢料拆单 | `purchase_split_workbench_submit_service.py:650～655` | 钢料分支要求自动业务路由。**按用户明确决定保留原路径调用**，不要求关闭 autoRoute，不重建拆单或定标/下单业务 |
| 钢料整单不拆 | 同文件 `:666～738` | 建组后准备后台业务路由任务并提交/入队。**按用户明确决定保留原路径调用**，不强行拆成纯保存操作。请求受理与后续业务执行结果分别展示 |
| 原材/五金采购下单 | `purchase_decision_controller.py:223` POST `/purchase/decision/{group_id}/create-order`；`purchase_decision_service.py:693` | 有现成业务入口，用户要求复用。控制器检查本人接口权限及组处理归属，不能共用 ERP 超管令牌 |
| 下单前置和幂等 | `purchase_decision_order_service.py:489` 起：锁分组、已下单回执、人工核价门禁、有效采购决策、异常、供应商、交期、价格、明细、派生订单号核对 | Agent BPM 通过不会自动更新这些 ERP 状态。需明确已有接口怎样合法满足前置；超时后查询原组/订单对账，不自动重发、不伪造审批或异常处理结果 |
| 五金价格提交 | `purchase_decision_controller.py:161` POST `/{group_id}/hardware-quote`，要求 approver_id，调用提交价格审批方法 | **旧 ERP 审批入口，不调用**。Agent 自建价格审批；已有下单接口需要的有效价格来源仍需单独核对 |
| 询价发送 | `purchase_decision_controller.py:193` POST `/{group_id}/inquiry`，业务发送后调用 `sync_group_inquiry_to_agent_services` | 含旧采购 AI 同步副作用，不能按“单一询价动作”直接开放；且对供应商外发须明确授权，不由查询隐式触发 |
| 设计、图纸、BOM | `design_order_controller`、`design_upload_controller`、`design_drawing_version_controller`、`bom_controller` | 已有业务资料入口可选用；批准、提交旧审批及自动下达分支单独排除。Agent 配置新模/改模审批，不以 ERP 设计审批代替 |
| 装配开完工 | `assembly_service.py:617`、`:698` | 有齐套、状态、生命周期及对账联动，复用原执行能力；Agent 只补需求中的授权、审批、异常和前置协调，不能另记一套装配事实 |
| 试模执行 | `trial_mold_service.py:97`、`:128` | 开始/完成已有；失败分支会生成整修等后续行为，需核对是否与 Agent 异常闭环冲突。申请、资源、审批和前置在 Agent 完善，不把局部方法当作完整符合需求 |
| 发货与验收 | `trial_mold_service.py` 的 DeliveryService，create/deliver/receive | 有既有执行能力；客户签收、验收合格及项目关闭分别记录。发货车辆、物流维护与报价等 Agent 新需求独立实现 |
| 合同及付款 | `contract_service.py:35～154` | 基础 CRUD 已有；付款记录含修改、删除方法，不能直接作为不可覆盖更正工具。Agent 需开发合同上传/OCR 草稿/人工核对/BPM、合同关联和版本等流程，引用已存在的事实 |
| 计划与节点 | `project_node_controller`、`production_schedule_controller` | 有节点/实际进度接口，不能据此排除项目大节点、跨部门确认和版本计划开发。Agent 计划与 ERP 实际执行分别归属，不能重复统计 |
| 异常、设变及审批 | `workflow_controller`、`production_exception_controller`、`design_change_controller`、`module_entrust/controller/exception_controller` | 业务规则参考；不调用旧流程。工程联络单用于 Agent 异常处理全生命周期，后续整改动作才按能力目录调用既有业务 |

## 接口缺口与处理

| 缺口 | 当前处理 | 关闭缺口所需证据 |
|---|---|---|
| GAP-ERP-01 钢料两种路径的归属 | **范围问题已由用户确认解决：拆单与不拆单均按现有 ERP 路径复用**，保留内部业务路由，不要求新增独立入口 | 后续仅验证接口参数、本人权限、所选路径、后台结果和幂等；未联调不等于不能复用 |
| GAP-ERP-02 Agent 审批与 ERP 下单前置 | 不将 Agent 审批结果伪写为 ERP 内部审批，记录两侧状态差异 | 可用现有业务 API 支持真实业务状态及批准资料，或用户对具体衔接方式作出新决定 |
| GAP-ERP-03 其他接口的业务后续 | 钢料两条原业务路径已获用户明确复用决定，不再要求拆除内置业务路由。其他动作按其实际业务范围核对；旧审批/异常接口不作为 Agent BPM 实现 | 调用链与隔离联调证明业务后续符合实际确认范围；不将 ERP 业务路由与本项目 BPM 混为一谈 |
| GAP-ERP-04 本地新采购与通用订单能力 | 辅材/办公用品/试模料在 Agent 补业务，接口支持性未证实前不双侧建单 | 逐类明确支持字段、状态、唯一订单归属、执行回执与重复请求处理 |
| GAP-ERP-05 财务历史覆盖/删除 | 不将现成修改/删除付款直接封装给 Agent | 原接口或 Agent 新受控追加/冲正机制满足不可覆盖历史及唯一事实来源 |

以上是具体 ERP 接入约束，不阻止 Agent 的通用 BPM、报价、中标、合同资料、大节点及工程联络单开发。未真实联调的接口保持未验收；如在现有接口中确实无法解决，提供具体证据后讨论，不要求 ERP 继续开发，也不默认修改旧系统。

## 对通用 BPM 的开发输入

1. 审批模板与业务动作分离。管理员配置人员、条件和路线；动作仅从已登记、已验证的能力目录选择，业务来源由服务端固定。
2. 合同会话上传→OCR→草稿入库→上传人核对→发起 BPM→各节点办理→满足归档条件后归档。原件与字段来源、草稿与正式快照分别保留。
3. 普通节点具备管理员策略、用户明确委托、本人席位及当前授权时才可支持 Agent 代办；强制人工节点不能转授。当前代码尚未实现完整代办策略，不把普通工具许可视为审批委托。
4. BPM 结束只代表审批结束；业务动作和异常整改分别有结果与失败状态，不以聊天回复或远程请求已发送代替完成。
