# ERP ↔ MoldPilot 联动测试记录

测试日期：2026-09-29  
测试账号：`admin / admin123`  
ERP 工程：`D:\ERP-system\management-system`  
MoldPilot 工程：`D:\mold-agent\MoldPilot`  
测试文件目录：`C:\Users\LENOVO\Desktop\test\上传文件`

## 测试目标

启动 ERP 和 MoldPilot，使用一个浏览器会话完成文件进入、资料识别、项目/模具核对、业务提案、人工确认、ERP 受控动作及回执核对。每项测试必须记录 MoldPilot 会话、操作建议/确认回执、ERP 接口结果和数据库状态；没有证据的项目不勾选完成。

## BPM 迁移前置状态（2026-10-08）

- [x] MoldPilot BPM DSL 已支持流程/节点表单、逐行办理范围、业务任务标识、并集角色选人、并行会签组元数据和审批后 ERP 业务动作声明。
- [x] ERP 的 17 条审批定义已导入 MoldPilot `workflow_definition`，各生成一个 `version=1` 的已发布定义；导入脚本为 `scripts/migrate_erp_workflows.py --publish`。
- [x] 17 条模板全部通过 BPM DSL 校验和 BPMN 编译校验。
- [x] 有效人员组已按部门同步，停用账号已从审批候选池排除；设计类同序审批已拆成独立审批池。
- [ ] 按用户要求，T01-T08 的 ERP↔MoldPilot 实际联动测试在流程发布后执行；流程发布前本次没有执行 ERP 写入或联动测试。

## 测试边界

- 只使用 MoldPilot `admin` 账号；不创建其他账号，不绕过确认卡。
- 不执行数据库迁移，不修改 ERP 源码，不直接改写 ERP 数据库。
- 上传资料先进入 MoldPilot 的文档/资料链；ERP 写入动作只通过已注册的受控接口和人工确认卡。
- 测试文件按内容识别，不把文件名直接当成业务事实。

## 测试文件清单

- `M250238-P4-五金请购单.CSV`
- `M250238-P4-五金请购单66.xlsx`
- `M250238-P4料单.csv`
- `M250238-P4小件费模拟料单.xlsx`
- `M260063-P1模具核算清单.xlsx`
- `M260063-P6模具核算清单.xlsx`
- `M260144-P3-2026.8.10料单.xlsx`
- `M260144-P3-模具 五金清单2026.8.10.xlsx`

## 功能验收清单

### T01 环境、健康检查和管理员登录

- [x] ERP 前端/API 可访问，MoldPilot API/Web 可访问。
- [x] 仅用 `admin / admin123` 登录成功。
- [x] 记录 ERP 端口、MoldPilot 端口、当前会话标题/ID。
- 结果：通过。ERP `18080/9099`、Agent `19080/19081` 可访问；MoldPilot `8001/5173` 可访问；MoldPilot 管理员登录成功。
- 证据：内置浏览器会话 `http://127.0.0.1:5173/`；当前联调会话标题 `附件：M250238-P4-五金请购单66.xlsx`，会话 ID `a104e77c-ebb5-4735-b428-4379b4092c5d`。
- 阻塞：ERP `/api/agent/procurement/health` 可达但返回“采购 AI 回调令牌无效”，业务接口是否可用仍需有效 ERP 会话/令牌。

### T02 文件上传与接收台账

- [~] 从测试目录选择资料上传到 MoldPilot。
- [x] 文件归属、SHA-256、版本和会话关联可查询。
- [ ] 重复上传不会重复建立有效资料事实。
- 结果：部分通过。首个 XLSX 已保存并关联到 MoldPilot 会话；重复上传相同内容生成了第二个 `FileObject`，当前未证明幂等去重。
- 证据：文件 `M250238-P4-五金请购单66.xlsx`，大小 `10116` 字节，SHA-256 `6309cb2c675579f96e83f1c432ba16186e70d2d70de872b185d66037beff1246`；同一会话下出现文件 ID `60b72bba-399d-4ed7-8167-8904bb77767b` 与 `a1076cba-19ce-42a7-8b84-881b6128e5e1` 两条记录。
- 阻塞：重复上传幂等规则未满足或未实现；暂不勾选整项。

### T03 XLSX/CSV 资料识别与人工核对

- [ ] 识别表头、明细行、料号、数量、单位和金额/币种（若存在）。
- [ ] 公式、缺列、重复行或类型问题进入待核对状态。
- [x] 人工确认前不生成采购/ERP 正式单据。
- 结果：未完成。上传后的自动解析任务未进入可核对结果。
- 证据：前端执行链路显示 `ERP_DESIGN_MCP_FAILED`；补齐本地 MCP 入口后，直接调用仍返回“用户token已失效，请重新登录”。ERP 数据库只读核对显示 `design_upload_session=0`、`purchase_request=0`，未产生 ERP 正式记录。
- 阻塞：已在本地运行目录补齐仓库已有控制 MCP 脚本入口并写入 ERP 本地令牌配置；实际调用继续被 ERP 拒绝为“用户token已失效，请重新登录”，说明该设计上传接口要求 ERP 用户 JWT，不能用采购服务令牌替代。

### T04 项目、模具与合同/资料对象匹配

- [ ] 以 `M250238-P4`、`M260063-P1/P6`、`M260144-P3` 等线索查询候选。
- [ ] 显示 ERP 来源、核对时间、项目/模具候选和冲突原因。
- [ ] 多候选不自动绑定；确认卡冻结对象版本。
- 结果：未完成。
- 证据：带附件的只读查询再次被路由到“解析新模设计上传清单”，前端显示 `TOOL_FORBIDDEN` 与 `ERP_DESIGN_MCP_FAILED`。
- 阻塞：MCP 入口已恢复，但 ERP 设计接口要求有效用户 JWT；当前 ERP 登录启用验证码，尚未取得 `admin` 的 ERP 用户会话。

### T05 报价/中标/承接或采购资料进入业务提案

- [ ] 根据资料类型进入对应 MoldPilot Skill/Tool。
- [ ] 生成结构化 Proposal，显示来源、资料版本、权限和限制。
- [ ] 未经本人确认不创建正式业务事实。
- 结果：未完成。
- 证据：本轮附件解析在提案生成前即因 `ERP_DESIGN_MCP_FAILED` 失败，未生成可供人工确认的 Proposal。
- 阻塞：ERP 设计 MCP 已能启动，但 ERP 端拒绝无效用户令牌；不能把失败结果当作 ERP 业务提案成功。

### T06 正式开工与基线计划门禁

- [ ] 核对客户开工条件、ERP 项目/模具映射和正式开工状态。
- [ ] 检查设计、采购、加工、装配、试模、交付六类计划节点。
- [ ] 缺前置条件时阻断，不伪造计划或执行状态。
- 结果：未完成。
- 证据：本轮没有取得新的 ERP 项目/模具映射或有效基线回执。
- 阻塞：ERP 业务查询回调令牌无效，且设计上传需要用户 JWT；无法核对六类计划节点。

### T07 ERP 采购受控动作与回执

- [ ] 查询 ERP 采购申请/分组/供应商/订单上下文。
- [ ] 如资料和权限满足，生成受控采购动作确认卡。
- [ ] 确认后核对 ERP 返回单号、状态、版本和 MoldPilot `ERPOperation`。
- [ ] 验证重复提交、版本变化、超时/未知结果的处理。
- 结果：未完成，未执行写入。
- 证据：未出现受控采购确认卡、ERP 单号或 MoldPilot `ERPOperation` 回执；这是保护性阻断。
- 阻塞：ERP 设计 MCP 已启动但缺 ERP 用户 JWT；本轮未满足正式写入前置条件。

### T08 执行、交付、财务与关闭回读

- [ ] 查询 ERP 制造/装配/试模/发运/签收/验收/付款相关事实。
- [ ] 验证 MoldPilot 不把局部完成或审批通过误判为项目完成。
- [ ] 验证 ERP 状态、MoldPilot 状态和审计回执一致；无法核对时进入待处理。
- 结果：未完成。
- 证据：未取得 ERP 制造、装配、试模、发运、验收或付款回读；未把局部结果误判为完成。
- 阻塞：ERP 业务接口凭据/用户会话/回调状态未打通，无法做双向对账。

## 总结

- 已完成：`1 / 8`（T01）
- 部分完成：`1 / 8`（T02）
- 未完成/阻塞：`6 / 8`（T03-T08 的完整链路仍不可验收）
- MoldPilot 前端可查看会话：`附件：M250238-P4-五金请购单66.xlsx`，ID `a104e77c-ebb5-4735-b428-4379b4092c5d`
- 最终结论：服务和 MoldPilot 登录已打通，文件接收可用；ERP MCP 入口已补齐但 ERP 设计接口要求验证码登录后的用户 JWT，当前仍无法证明 MoldPilot → ERP 的解析、受控写入和数据库同步。

## 本轮实际联动复测（2026-10-09 08:29-08:30，迁移完成后）

### 复测范围与会话

- [x] 只启动/操作 MoldPilot；ERP 保持用户已启动的实例，不修改 ERP 源码或数据库。
- [x] 仅用 MoldPilot `admin / admin123` 登录内置浏览器。
- [x] 使用测试文件 `C:\Users\LENOVO\Desktop\test\上传文件\M250238-P4-五金请购单66.xlsx`，大小 10,116 字节。
- [x] 上传原件已保存并关联会话；文件 SHA-256：`6309cb2c675579f96e83f1c432ba16186e70d2d70de872b185d66037beff1246`。
- [x] 会话标题：`附件：M250238-P4-五金请购单66.xlsx`。
- [x] MoldPilot 会话 ID：`228fb949-aaa4-4a8a-b873-49a7bab5855b`。
- [x] MoldPilot Run ID：`d89209a2-f9af-43c8-9970-091ce2f84502`。

### 复测结果

- [x] T01 环境与登录：MoldPilot API `http://127.0.0.1:8001/api/health` 返回 200；Web `http://127.0.0.1:5173/` 返回 200；ERP `http://127.0.0.1:18080/` 返回 200；管理员登录成功。
- [~] T02 文件上传：原件已保存，前端显示“附件已进入当前会话”，文件进入后台解析；本轮只验证单次上传，不重新判定重复上传幂等性。
- [ ] T03 资料识别与核对：MoldPilot 任务进入 ERP 解析工具，但未取得表头、明细或预览回执。
- [ ] T04 项目/模具匹配：未取得 ERP 项目/模具候选或版本冻结证据。
- [ ] T05 采购资料 Proposal：未生成可确认的结构化业务 Proposal。
- [ ] T06 BPM 业务审批启动：本轮未生成可办理的业务审批实例，未触发审批节点。
- [ ] T07 ERP 受控写入与回执：未出现 ERP 单号、成功状态或 MoldPilot `erp_operation` 记录。
- [ ] T08 双库同步与回读：未取得 ERP 业务事实回读，因此不能判定两个系统已联动成功。

### 实际失败回执

前端执行链路和 `ai_run.result` 一致：

```text
ERP POST /design/upload/parse failed (200): 用户token已失效，请重新登录
错误码：ERP_DESIGN_MCP_FAILED
Run 终态：FAILED
```

本轮失败发生在 ERP 解析接口，早于项目匹配、MoldPilot BPM 业务实例和 ERP 正式导入；没有绕过登录、验证码或确认卡重试，也没有把失败误记为业务成功。

### 两边数据库只读核对

- MoldPilot `ai_conversation` 已产生本轮会话，`ai_run.status=FAILED`，失败 Run ID 为 `d89209a2-f9af-43c8-9970-091ce2f84502`。
- MoldPilot `erp_operation` 总数仍为 `0`，`project_erp_mapping` 总数仍为 `0`。
- MoldPilot 迁移来源的 17 条 `workflow_definition` 均为 `PUBLISHED`（每个流程一个已发布定义）。
- ERP 数据库（`127.0.0.1:5432/erp`）只读统计：`design_upload_session=0`、`design_order_draft=0`、`wf_process_instance=0`、`wf_todo_task=0`、`purchase_request=0`、`erp_data_import_batch=0`、`integration_outbox=0`。
- 结论：本轮确认“上传到 MoldPilot 成功、调用 ERP 被用户 JWT 拒绝、两边未发生业务写入”；无法证明 ERP↔MoldPilot 的业务联动已通。

### 本轮结论

- 已完成：T01；T02 的单次上传部分。
- 阻塞：ERP 设计解析接口仍要求有效 ERP 用户 JWT；ERP 根页面可访问不等于业务会话有效。
- 待处理：需要在 ERP 内完成一次合法用户登录并保持有效 JWT（如有验证码需由用户手动完成），再复跑 T03-T08；在此之前不应迁移或宣称审批联动已验收。

## 根因定位补充（2026-10-09）

ERP 页面正常登录与本次 MoldPilot 调用失败不矛盾：

1. ERP 前端登录后把当前用户 JWT 保存在 `Admin-Token`，并在请求中发送 `Authorization: Bearer <当前JWT>`；嵌入 ERP 的 Agent 页面还会通过 `postMessage` 传递当前 `token`/`handoff`。
2. 本次测试打开的是独立 MoldPilot `127.0.0.1:5173`，没有经过 ERP 嵌入页，因此没有收到 ERP 浏览器的 JWT 或 handoff。
3. MoldPilot 的设计 MCP 是独立 Node 进程，读取 `backend/domain_packs/mold/mcp/erp-design-upload/.env`，当前配置为 `ERP_DESIGN_UPLOAD_TOKEN=change-me`。它向 `127.0.0.1:9099` 发送的是这个固定值，而不是 ERP 浏览器里刚登录的 JWT。
4. ERP `/design/upload/parse` 按当前用户身份和设计权限校验 token；固定占位值被 ERP 识别为过期/无效，于是返回“用户token已失效”。ERP 页面能打开只说明浏览器 Cookie/JWT 有效，不能让独立 MoldPilot 后台自动共享它。

因此本次不是 ERP 数据库或审批流迁移异常，断点在“独立 MoldPilot 测试会话没有 ERP 用户身份传递 + MCP 仍使用占位 token”。修复应采用 ERP 嵌入页的 token/handoff 传递，或实现受控的 ERP 用户凭证桥接；不建议把一次性浏览器 JWT 长期写死在 MCP `.env`。

## 架构要求修正（2026-10-09）

用户已明确两个系统独立运行：MoldPilot 使用自身 `admin / admin123` 登录，业务动作由 MoldPilot 直接连接 ERP PostgreSQL 写入；ERP 浏览器 JWT、Cookie、handoff 和 ERP 页面登录状态不参与 MoldPilot→ERP 写入。

当前代码与该要求不一致：

- MoldPilot `.env` 只有 `MOLD_DATABASE_URL`（`moldpilot`）和 `MOLD_ERP_BASE_URL=http://127.0.0.1:9099`，没有 ERP 数据库连接配置。
- 当前设计上传适配实际启动 Node MCP，以 `ERP_DESIGN_UPLOAD_TOKEN` 调用 ERP HTTP 接口；本地配置是占位 token，因此失败发生在 HTTP 鉴权层，ERP 数据库没有被写入。
- ERP 本机配置已确认：`DB_HOST=127.0.0.1`、`DB_PORT=5432`、`DB_DATABASE=erp`。

后续实现应改为 MoldPilot 侧的 ERP PostgreSQL 适配器：使用独立的 `MOLD_ERP_DATABASE_URL`，在单一事务中写入 ERP 所需业务表，并用 `erp_operation` 保存原生 ID、请求哈希和回执，按业务键/文件哈希实现幂等。设计上传至少涉及 ERP 的 `design_upload_session`、`purchase_request`、`purchase_request_detail`；如需让 ERP 待办页显示流程，还要按迁移后的状态策略处理 `wf_process_instance`、`wf_instance_node` 和 `wf_todo_task`。不能只插入一张表后宣称 ERP 联动成功。

此前“通过 ERP 嵌入页传 JWT/handoff”的建议不适用于本架构，已作废；本轮尚未对 ERP 数据库执行写入。

## 截图复核：设计上传 DXF（2026-10-08）

用户提供的截图对应会话 `附件：M250238-P4-修改1.dxf`（conversation `62708d4a-a191-4f50-ae11-4f54e5b4a0e0`）。数据库证据如下：

- 首轮运行 `9f8c38f7-6556-4703-a4ff-126096244dfc` 只完成文件名/类型确认，状态为 `SUCCEEDED / CLARIFICATION`。
- “确认上传”后的运行 `737337aa-e3ae-4b9f-904d-f62ee8d203b0` 状态为 `FAILED`，错误码为 `CONTEXT_BUDGET_EXCEEDED`。
- 该运行唯一的 `ai_step` 来源为 `agent_proposal`，明确写入“当前只是准备确认，尚未上传”，并说明“本轮只生成确认卡，未执行 ERP 设计正式动作”。
- MoldPilot `erp_operation` 总数为 `0`；ERP 端 `design_upload_session`、`wf_process_instance`、`wf_todo_task` 等业务表仍为 `0`。

因此截图中的“已提交审批”是会话展示/提案文案，不能作为 ERP 已写入或审批实例已创建的成功回执；本次 DXF 没有完成 MoldPilot→ERP 的正式上传与数据库联动。
@追加模型与直连修复记录（2026-10-09）

- 现场查询 `http://192.168.0.22:8000/v1/models` 返回 `Qwen3-30B-A3B-Instruct.max_model_len=64000`；30B 仅表示参数规模，不代表上下文长度。
- MoldPilot `.local/model-config.json` 和 `.env` 已将当前环境模型上下文窗口从 `32768` 调整为 `60000`，最大输出保持 `4096`，安全预算为 `55904` token，留出输出空间。
- 新增 MoldPilot→ERP 本机服务工作进程：使用 ERP 自己的 `.venv` 和 PostgreSQL 配置，DXF 修模上传、设计清单解析、审批配置读取和设计清单导入均不再发送 ERP 浏览器 JWT 或占位 MCP token。
- 直连执行会按 ERP `sys_user` 账号映射操作人（当前本地 `admin` 对应 ERP `user_id=1`），并复用 ERP 正式 service，保持 ERP 原有校验、幂等和审批创建规则。
- 已用本地 Excel 测试文件直连解析，ERP 已生成 `design_upload_session.id=8`，回执为 `M250238-P4`、`hardware` 料单，说明直连数据库路径可用；尚未导入采购申请。

## 直连修复后的回归结果（2026-10-09）

- 模型服务 `/v1/models` 返回 `Qwen3-30B-A3B-Instruct`，服务端 `max_model_len=64000`；MoldPilot 本地工作窗口设为 `60000`，最大输出 `4096`，预留后的输入预算为 `55904`。
- `MOLD_ERP_DIRECT_ENABLED=true` 时，设计 DXF、清单解析、审批配置和清单导入走 MoldPilot 本机启动的 ERP service worker。worker 使用 ERP 自己的 `.venv` 与 PostgreSQL `127.0.0.1:5432/erp`，不发送 ERP 浏览器 JWT，也不调用占位 MCP token。
- 使用 `M250238-P4-五金请购单66.xlsx` 解析成功，ERP 会话 `id=19` 返回 9 行、总数量 51、`canImport=true`。当前机器没有 `Z:/模具五金图纸/附图订购`，所以附图行保留“无图”提示；这属于图库路径缺失提示，不阻止本次导入。
- 通过同一会话导入成功：ERP `purchase_request.id=8`、单号 `PR20261009102252`，来源为 `design_upload/hardware`，审批实例 `wf_process_instance.id=10`、流程 `design_new_model_approval`，当前节点“设计主管审批”；待办 `wf_todo_task.id=328` 已分配给 ERP 用户“于孟”（`user_id=10002`）。
- 兼容处理：本地 ERP 数据库的 `additional_processing_fee` 表缺少当前 ORM 的可选规则字段，MoldPilot 直连 worker 对该可选查询返回空规则，核心清单、请购和审批写入仍由 ERP 正式 service 完成。

## 全部迁移审批流复测（2026-10-09）

17 条迁移流程已使用独立临时项目在 MoldPilot BPM 内逐条提交、逐级审批并回滚测试数据，结果为 **17/17 通过**。详细节点、审批人、表单校验和 ERP 采购申请回读见 [ERP_MIGRATED_WORKFLOW_E2E_TEST_20261009.md](ERP_MIGRATED_WORKFLOW_E2E_TEST_20261009.md)。

本次真实上传的最终回读为：MoldPilot subject `94b8c2e8-c3ce-4941-b21c-50f0bc337c41` / instance `ea1bfbce-15e3-44c3-a37d-a5b2838b86ae`，ERP purchase request `PR20261009150932`（id `9`）。ERP 旧设计审批投影已停用，采购申请标记为 `approved` 并可供采购人员查看。
