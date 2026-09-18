# 开发覆盖情况

更新：2026-09-18。此表记录已实现与未实现的差异，不缩减 V3.6 全量范围，不把业务功能分产品阶段，也不替代正式验收。

## 持续开发：人工审批代理闭环（2026-09-18）

- 通用 BPM 节点新增显式 `allow_proxy` 开关；人工代理与 Agent 自动审批委托、席位转交、前/后加签相互独立。未开启代理的节点不会出现在授权选项中，也不会因存在历史授权而放宽审批席位。
- 管理员可在既有“审批授权”设置中按流程、节点、原审批人、代理人、允许决定和有效期维护人工代理；不新增 ERP 式菜单或独立业务页面。授权要求双方账号有效、原审批人与代理人不同，并拒绝直接或间接代理环路。
- 审批待办只解析当前节点的一跳有效代理。代理人仍须具备该业务材料的读取和审批权限，不能代理审批自己发起的申请，不能替原席位转交或加签，也只能提交授权范围内的决定。直接席位始终优先于代理关系。
- 代理提交沿用本人二次确认和冻结版本校验；审批动作记录实际操作人，同时在席位快照与审计中保留原审批人、代理授权和 `HUMAN_PROXY` 身份。撤销或权限变化会递增安全版本，使此前准备的确认失效。
- 验证：本地 PostgreSQL 完整后端回归 476 项通过，Vue 类型检查、mold/template 两套生产构建、Python 编译、两条 Alembic head 和正式 `moldpilot` 数据库升级/漂移检查通过。内置浏览器实际完成“管理员按节点授权代理同意 → 代理人收到原审批席位待办 → 详情只开放同意 → 确认弹窗提示代理身份 → 提交并完成审批”；最终页面显示“超级管理员（代 浏览器转交验收员） · 同意”并保留意见，控制台无 warning/error。验收使用本机合成采购材料，不代表真实采购下单、ERP 执行或正式业务验收。

## 持续开发：审批前加签与后加签闭环（2026-09-18）

- 通用 BPM 节点新增 `add_sign_policy`，由流程设计者显式配置允许的前加签/后加签方式和合格人员池；发布时校验策略、人员存在且账号有效，未配置的节点不会临时开放加签。
- 当前席位负责人可在原审批工作区准备加签。前加签会先冻结原席位，新增席位通过后原席位恢复；后加签由原席位先审批，新增席位随后办理。新增席位属于同一阶段的强制票，必须明确通过，不能被 ALL/ANY 的原始票数规则静默绕过。
- 准备和本人二次确认时都会重新校验实例版本、冻结快照哈希、原席位版本、阶段状态、目标账号和实时业务授权；加签确认会使更早准备的审批决定失效，避免旧确认越过新增依赖。驳回或退回会同步关闭尚未行动的关联席位。
- 加签发起人、原因、先后关系、原席位、序号和处理状态写入席位、分配快照和 `approval.seat.added` 审计事件。审批详情永久显示加签历史；动态加签席位不进入 Agent 自动审批，必须由被加签人本人处理。
- 前端仅扩展既有流程模板与审批工作区，没有新增 ERP 式菜单或页面。验证：本地 PostgreSQL 完整后端回归 470 项通过，Vue 类型检查、mold/template 两套生产构建、两条 Alembic head 与正式库升级/漂移检查通过。内置浏览器实际完成“管理员发起审批 → 原审批人发起前加签 → 加签人收到待办并通过 → 原席位恢复并通过 → 审批完成”；最终页面保留两份审批意见与加签记录，控制台无 warning/error。浏览器使用本机合成采购材料，不代表 ERP 执行或正式业务验收。

## 持续开发：审批席位受控转交闭环（2026-09-18）

- 通用 BPM 节点新增 `allow_transfer` 显式开关；只有当前待审批席位负责人可发起转交，转交仅更换同一席位的负责人，不新增票数、不改变会签/或签语义，也不修改既有审批意见。
- 目标人员在准备与本人二次确认时均重新校验账号状态、当前节点席位占用、模具领域的业务审批权限和完整材料读取权限；审批实例版本、冻结快照哈希、席位版本或目标资格发生变化都会阻断旧确认。
- 转交沿用通用人工确认意图，确认前不写库；确认后席位与审批实例版本递增，转交人、接收人、原因和版本写入分配快照及审计事件。审批详情永久展示转交历史，原审批人不再看到决定按钮，接收人从原待办继续办理。
- 前端能力嵌入既有审批工作区和流程模板编辑器，没有新增 ERP 式菜单或页面；转交先展示目标与原因确认框，完成后历史仍可回看。
- 验证：项目本地 PostgreSQL 下完整后端回归 465 项全部通过；Vue 类型检查、mold 生产构建、template 生产构建和差异检查通过。内置浏览器创建可转交流程并实际完成“管理员发起审批 → 席位转给浏览器验收用户 → 目标用户收到待办 → 打开同一材料并查看转交历史 → 二次确认同意 → 审批完成”的闭环；最终页面同时保留目标用户审批意见和席位转交记录，控制台无 warning/error。该验收使用本机合成采购材料，不代表真实采购下单、ERP 执行或正式业务验收。

## 架构治理：领域包抽离复审与交付物流应用服务迁移（2026-09-17）

- 已暂停新增业务功能，优先处理通用 `LLM + Harness + Tool + Skill` 宿主与模具 ERP 领域包的边界。独立子智能体复审结论为“部分独立”：当前可动态替换产品外壳、系统策略、Skill、工具目录、提案处理映射、领域路由和 ERP HTTP 适配器，但尚不能仅替换 `domain_packs/mold` 就切换成车辆或工装业务。
- 本批将交付物流应用服务从 `app/delivery_logistics_tools.py` 迁至 `domain_packs/mold/delivery_logistics.py`；路线/报价 schema、查询投影、业务校验、proposal 准备与本人确认后的写入均由领域包持有。领域工具网关和 proposal handler 直接指向包内实现，`app` 同名文件仅保留旧调用兼容 facade。
- 新增稳定的 `agent_core.host_ports` 宿主能力契约，宿主模型、授权、时钟、配置、内容哈希和确认策略由 `AGENT_HOST_PORTS_MODULE` 独立装配；通用 `DomainError` 与 `StrictModel` 也移入 Agent Core，`app` 只保留兼容导出。交付物流服务不再直接导入任何 `app.*` 实现。
- 模具共用的项目定位输入与匹配规则已迁入 `domain_packs/mold/contracts.py`；尚未迁出的业务材料、采购订单与工程联络读取被集中到 `legacy_read_ports.py`，不再散落在物流应用服务中。该文件是显式迁移债务，不代表依赖已经消失。
- Harness 领域语义继续抽离：ToolSearch schema 示例、领域检索补词、按需工具提示、权限模式说明和 Ollama 结构化 ReAct 指引全部由活动 `harness_policy` 注入；`agent_core` 不再写死项目计划、工程联络、项目号、合同号或“当前有权项目”等模具示例。模板包拥有独立的中性策略，换业务包无需修改 Harness。
- 确认卡呈现契约已迁入领域清单：动作标题裁剪、业务值标签及“回执字段 → 工作区目标”详情链接由 `proposal_presentation` 声明，并由 Agent Core 在启动时校验。前端组件重命名为通用 `ProposalCard`，不再导入模具值词典，也不再把所有详情按钮强制解释成工程联络单；宿主只分发领域包给出的工作区目标。template 包使用完全空的中性呈现契约。
- ORM 注册边界已建立：项目、材料、采购申请模型迁入 `domain_packs/mold/models.py`，业务材料和工程联络模型分别迁入包内 `domain_models.py`、`contact_models.py`，联络单附件关系迁入 `attachment_models.py`；`app.models` 只在活动 pack 明确导出时兼容暴露这些类。template 包的空模型注册表已由独立进程门禁验证，不加载任何 `domain_packs.mold` 模块，SQLAlchemy metadata 不再包含项目、采购、供应商、模具、工程联络和物流表。
- ORM 基础契约已从 `app.models` 抽到不加载领域包的 `app.model_base`，消除了 `app.models ↔ pack.models` 循环；通用审批实例改为 `resource_type/resource_id`，不再外键引用采购申请或业务材料，资料绑定也不再在数据库约束中写死两种模具资源。可审批资源类型由 active pack 的 `resources.py` 注册并在写入前校验。
- 权限词汇与数据范围维度已改为 active pack 契约：宿主只保留账号、流程设计、文件和审计等通用权限，项目、采购、工程联络、仓库等权限及 `project_id/category/warehouse_id` 维度由 mold 包提供；template 包加载后只有 6 项通用权限、零业务范围维度。
- 新增 PostgreSQL 正向迁移，将旧 `request_id/subject_id` 数据无损归一为通用审批资源引用，并移除业务外键和资料绑定业务类型约束；用户头像表也从 API 运行时 DDL 纳入 Alembic/ORM 正式管理，历史复合/部分索引补齐 ORM 声明。正式库已升级至新 head，`alembic check` 无模型漂移。
- 迁移入口现由 active pack 契约选择：`scripts/migrate.py` 对 mold 保持既有 `alembic.ini/alembic_version`，对 template 使用独立 `alembic-core.ini/alembic_core_version`。通用宿主基线是带 SHA-256 校验的冻结 PostgreSQL SQL，不依赖运行时模型偷偷建表；在 `moldpilot_test` 的隔离 schema 中已完成空库升级、current、autogenerate check、降级，并确认只生成 27 张宿主表和版本表，零模具业务表。直接误用不属于当前 pack 的迁移环境会明确阻断。
- 已增加架构门禁，验证领域 handler 指向包内实现、宿主 facade 不再承载物流业务类、领域网关不反向调用旧实现；`AGENT_BUSINESS_PACK=template` 仍能以 `Agent Workbench` 启动且不注册模具 HTTP 路由。
- 当前仍不是完整领域抽离：template 已有独立通用迁移链，但 mold 的历史迁移仍是 core 与模具表混合的兼容链，新建的非空车辆/工装 pack 仍需提供自己的领域迁移仓库；物流及多数工具/API 实现仍依赖或位于 `app.plan_tools`、`app.domains`、`app.procurement`、`app.contacts` 等宿主模块；`uiText.ts`、`businessForms.ts`、快捷问题和具体工作区面板仍静态包含模具语义。业务包仍不能单独发布，也还不能仅替换目录就得到完整的车辆/工装前后端。
- 后续拆分顺序固定为：先为非空 pack 补齐“core 基线 + pack 自有迁移”的组合/安装协议并逐步退役 mold 混合历史链；随后按业务纵切迁移遗留 API/tool/service 与物流上游 read providers；最后把前端快捷问题、业务表单和工作区面板改为 pack registry/lazy-load，并删除兼容 facade。只有 template 可独立迁移建库、启动前后端且 bundle 不含模具语义后，才可认定业务包可完整替换。
- 验证：确认卡领域元数据契约定向回归 7 项通过，Vue 类型检查和生产构建通过；完整后端回归在仓库隔离临时目录下 447 项通过，Python 编译/import smoke 通过。内置浏览器重启真实后端后复核已确认物流卡：标题由领域元数据从“确认物流路线”归一化为“物流路线已确认”，卡片在折叠执行链中永久保留，详情字段可回看，已确认后的“暂不执行/已确认执行”按钮均禁用；本批没有新增业务功能或传统 ERP 页面。
- 本轮 ORM/权限契约验证：template 独立进程成功启动，`Base.metadata.sorted_tables` 与全部 PostgreSQL `CREATE TABLE` 可编译，未加载 mold 模块/表/权限/范围维度/审批资源类型；正式 PostgreSQL 已升级至 `d5b8f3c20e71 (head)`，`alembic check` 返回无新增迁移；隔离临时目录下完整后端回归 447 项通过、Python compileall 通过。内置浏览器重启真实后端后，会话历史和已确认物流正文完整保留，确认卡仍位于执行链与正文之间，详情弹层字段完整且两个执行按钮禁用，控制台无 warn/error。本轮未增加业务功能或 ERP 页面。
- 本轮迁移分包验证：新增 PostgreSQL 空 schema 集成门禁实际执行 template 的 `upgrade head → current → check → downgrade base`，确认 `a10c0e000001` 基线可逆且不创建任何模具表；迁移 URL 统一保证进程级 `AGENT_MIGRATION_URL`/旧别名优先于 `.env`，测试夹具显式覆盖并恢复新变量，避免配置新变量后测试误向正式库执行迁移。并行加入的 ERP 设计 MCP 保留原功能，但其 HTTP bridge、工具适配器与 9 个 Skills 已迁入 mold pack，并通过 active-pack manifest/gateway 接入；旧 `d4e5f6a7b8c9` 用户头像分支改为兼容标记，与数据安全的 `d5b8f3c20e71` 汇合到唯一 `e6a7b8c9d0e1 (head)`，正式 PostgreSQL 已升级且 `alembic check` 无漂移。最终完整后端回归 455 项全部通过、前端类型检查与生产构建通过；真实后端重启后内置浏览器成功恢复历史会话、已处理确认卡和最终正文，控制台无 warn/error。本轮没有新增传统 ERP 页面。

## 持续开发：FR-065～067 物流路线、核准报价与结算价办理闭环（2026-09-17）

- 扩展 `LogisticsRoute` 与 `LogisticsQuote` PostgreSQL 模型：固定路线/项目实际路线保存地点、承运商、车型、重量、运输方式、计价单位、含税方式、有效期、确认人和来源；报价保存计价方式、比价数量/摘要、对账依据、来源、创建/批准人及被替代报价 ID。
- 新增 `prepare_logistics_route` 对话办理工具。仓库权限范围内使用真实项目 ID/版本准备路线确认卡，本人确认前不写库；全局固定路线和项目实际路线保持显式区分，不把路线确认推断为发货、签收、验收或物流费用完成。
- 新增 `prepare_logistics_quote` 对话办理工具。采购价格审批权限基于当前有效路线准备通用报价或项目本次结算价格；项目价格重新核验项目版本，多家比价必须至少两家并保存比较摘要，默认报价有效期上限为可配置的 183 天。
- 价格变化不覆盖历史：有效期重叠时必须明确 `supersedes_quote_id`，本人确认后原报价转为 `CANCELLED`、新报价转为 `EFFECTIVE` 并保留替代链；未明确替换或存在多个重叠价格时阻断。
- `query_delivery_logistics_context` 现在同时返回路线历史、当前有效路线、有效报价、项目结算价候选、待审批/过期报价及来源/确认/对账元数据。只有路线没有报价时明确返回缺口，过期路线上的报价不会被当作当前有效价格。
- 不新增 ERP 式页面：能力只通过 ToolSearch 按需开放和通用会话确认卡办理；路线使用 `warehouse.configure`，报价使用 `purchase_price.approve`，只读物流查询不会开放两个 `prepare_*` 工具。
- 通用确认恢复协议同步修复：本人确认后，宿主以结构化 `ProposalResolution` 权威回执唤醒同一 Agent Run，Harness 禁止已解决操作重新输出 `AWAITING_APPROVAL`，确认前结论转入可折叠过程，确认后的 Agent 回执成为唯一正文。已确认卡永久保留在过程与正文之间，可重新查看完整字段，但执行/取消按钮禁用，避免重复操作。
- 验证：完整 PostgreSQL 后端回归 443 项通过，Python 编译检查和 Vue 类型检查/生产构建通过；迁移链 `f8c4d2a6b731 → a9d5e1f7c842` 已在隔离测试库完成降级/升级校验，正式 `moldpilot` 库已升级至 head。内置浏览器使用 Qwen3.8-27B 完成 `BROWSER-OUT-001` 实际物流路线准备、本人确认、数据库写入与 Agent 回执恢复：折叠态仅显示已确认卡和“物流路线已确认登记完成”正文，展开态保留工具过程、确认前说明和“本人已确认”权威回执；确认卡详情字段可回看且动作按钮禁用。真实仓库/采购签字、发运单与承运商对账单、物流费用生成、ERP/财务联调和正式业务验收仍保持 `NOT_VERIFIED`。

## 持续开发：供应商资料核验反馈闭环（2026-09-17）

- 新增追加式 `SupplierMaterialVerification` 与 PostgreSQL 迁移，供应商对一条已批准资料交接的回复独立保存为 `RECEIVED`、`ACCEPTED`、`NEEDS_CLARIFICATION` 或 `REJECTED`；回复不会覆盖或修改原资料交接记录。
- 新增 `prepare_supplier_material_verification` 对话办理工具。准备和本人确认时均重新核验项目版本、供应商、已生效整套委外合同、资料交接归属/批准状态、回复日期、附件权限和来源防重；待澄清或退回必须填写原因和跟进日期。
- 整套委外查询新增供应商资料核验明细及派生状态，明确“供应商已收到”不等于“供应商已核验接受”，并将未核验、仅收到、待澄清和退回资料作为缺口或告警返回。
- Harness 工具开放门控复用统一的正式动作判定器，修复“登记供应商资料核验”已被识别为办理动作、但另一套开放词表又隐藏全部业务工具的架构不一致；同时规定只有用户本轮请求可以开放 `prepare_*`，模型自行生成的 ToolSearch 搜索词不能把只读请求升级为写入操作。该修复适用于全部领域包动作，不为本功能编写自然语言兜底。
- 验证：PostgreSQL 集成测试覆盖确认前不写库、本人确认后追加核验、已收到不等于已接受、重复来源、日期倒置及退回必填项阻断；能力目录和 Harness 定向测试共 103 项通过，覆盖办理请求定向激活与只读请求禁止按准确名称/场景搜索开放 `prepare_*`。完整后端回归 430 项通过，Vue 类型检查和生产构建通过，PostgreSQL 迁移位于 `e7b3c1d5a920 (head)`。内置浏览器使用 Qwen3.8-27B 完成 BROWSER-OUT-001 真实只读核验查询，用时 1 分 2 秒，执行链路只开放并调用“读取整套委外上下文”，返回一段最终结论且未准备、确认或写入任何业务操作。真实供应商回复、附件安全、ERP/外部渠道接入和业务验收仍保持 `NOT_VERIFIED`。

## 持续开发：供应商节点填报频率与证据规则（2026-09-17）

- 新增版本化 `SupplierProgressPolicy` 与 PostgreSQL 迁移，按项目、供应商、已生效整套委外合同、阶段和可选计划任务保存填报频率、生效/首次应报日期、必需证据类型、制定依据和来源引用；替换规则保留历史版本，不覆盖旧规则。
- 新增 `prepare_supplier_progress_policy` 对话办理工具。准备与本人确认阶段都会重新校验项目版本、合同/供应商/计划节点一致性、当前生效规则版本、重复来源和精确范围权限；确认前不写库，不新增传统 ERP 页面或供应商门户。
- `prepare_supplier_progress_report` 增加结构化证据类型；存在生效规则时缺少必需证据会以 `SUPPLIER_PROGRESS_EVIDENCE_MISSING` 阻断，不能用自由文本依据绕过。整套委外查询同时返回当前规则、最新上报、下次应报日期、逾期状态、完成状态和缺证据项。
- ToolSearch 继续按场景收窄：普通“供应商节点上报”只暴露查询与上报登记；明确准备“上报频率/证据模板规则”时暴露查询、规则配置和节点上报这一组相邻能力，不加载整套委外的全部办理工具。
- 流式执行轨迹同步修复：后端为助手轮次输出稳定 `message_key`，前端使用 `requestAnimationFrame` 每绘制帧最多揭示一个可见字符；轨迹前插工具活动或消息从流式转为已落盘时继续排空原缓冲，不再把 4～5 字或整段文本一起补齐。
- 验证：供应商规则、证据阻断、逾期派生、规则替换、能力目录和 ToolSearch 收窄均已由 PostgreSQL/API/Harness 定向测试覆盖；流消息稳定身份由 API 回归测试覆盖，Vue 类型检查与生产构建通过。内置浏览器先复现旧版 `1、2、207` 字符跳变，修复后逐帧采样两段工具旁白的最大增量均为 1；同时修复“只读查询最近上报”被名词“上报”误判为正式操作的意图门控。使用 Qwen3.8-27B 真实完成只读整套委外查询，折叠态只显示“用时 2分41秒”、一段结论和建议，展开态保留 ToolSearch 与查询依据，随后已恢复折叠。供应商侧接入、ERP 联调和真实业务验收仍保持 NOT_VERIFIED。

## 持续开发：供应商节点上报证据对话办理闭环（2026-09-16）

- 新增 `prepare_supplier_progress_report` 工具，基于真实项目版本、供应商、已生效整套委外合同、可选计划任务、阶段、状态、进度、跟进日期、问题摘要、来源引用和证据，准备供应商节点上报登记 proposal。
- 该能力不新增供应商门户或传统 ERP 页面：仍在对话框内展示确认卡，用户本人确认后才写入 `SupplierProgressReport`；不代表我方已经收货/质检、客户已经验收或 ERP 生产节点已经完成。
- 准备和确认阶段都会重新校验项目版本、供应商有效性、合同归属/供应商一致性/生效状态、计划任务归属及标识名称一致性、采购跟进人有效性、重复来源，以及 `project.read` / `full_outsource_contract.read` / `full_outsource_contract.execute` 精确项目分类权限。
- 风险、阻塞和返工状态强制填写问题摘要与下次跟进日期；完成状态与 100% 进度必须相互一致；未来上报日期、倒置跟进日期和重复来源均阻断，防止模型凭自然语言写入矛盾节点事实。
- Harness 对场景工具的排序改为“查询前置固定保留、办理候选独立评分”，并增加通用办理意图门禁：普通“项目计划”查询不会暴露建立计划工具，“供应商节点上报”会只暴露整套委外查询与节点上报工具，不为单一流程写特例或自然语言兜底。
- 前端通用 proposal 卡片已映射 `supplier_progress_report` 到 `/api/full-outsource-proposals`，能力设置文案补充“准备供应商节点上报”。
- 验证：PostgreSQL 集成测试覆盖 proposal 不直接写库、本人确认后写入上报、计划节点关联、录入/导入来源、重复来源和风险必填项阻断；Harness 测试覆盖动态工具收窄。完整后端回归 383 项通过，Python 编译检查和前端 Vue 类型检查/生产构建通过。内置浏览器使用 Qwen3-30B 真实完成 `ToolSearch → 整套委外上下文查询 → 模型复核 → 再次查询 → 最终结论`，折叠态只保留“用时 18 秒”和一段结论，展开态按真实顺序显示三次工具活动；此前已通过确认卡写入并从 PostgreSQL 读回 `BROWSER-SPR-20260916` 上报。FR-071 的具体填报频率、证据模板、供应商侧接入和真实业务验收仍保持 NOT_VERIFIED。

## 持续开发：供应商资料交接证据对话办理闭环（2026-09-16）

- 新增 `prepare_supplier_material_handoff` 工具，基于真实项目版本、供应商、已生效整套委外合同、资料标题/类型、交接日期、交接对象、交接渠道和交接依据，准备供应商资料交接证据登记 proposal。
- 该能力不新增传统 ERP 页面：仍在对话框内展示 proposal 卡片，用户本人确认后才写入 `SupplierMaterialHandoff`；不创建供应商门户，不代表供应商已核验，不触发 ERP 发货、生产或验收执行。
- 准备和确认阶段都会重新校验项目版本、供应商有效性、整套委外合同归属/供应商一致性/生效状态、资料文件访问权限、`project.read` / `full_outsource_contract.read` / `full_outsource_contract.execute` 精确项目分类权限，以及重复来源引用。
- 整套委外 Skill 已同步办理规则：默认只读，只有用户明确要求登记客户资料、设计图纸、技术规范或质量标准交接证据，且上下文有真实项目/合同/供应商 ID 与依据时才准备卡片。
- 前端通用 proposal 卡片已映射 `supplier_material_handoff` 到 `/api/full-outsource-proposals`，能力设置文案补充“准备供应商资料交接”。
- 验证：`tests/test_full_outsource_tools.py` 覆盖 proposal 不直接写库、本人确认后写入已批准资料交接记录、重复来源阻断和正式获准交接缺少整套委外合同阻断；`tests/test_capability_catalog.py` 覆盖能力目录元数据。

## 持续开发：整套委外合同签署文件对话办理闭环（2026-09-16）

- 新增 `prepare_contract_signing_record` 工具，基于真实项目版本、已生效整套委外合同、供应商、签署状态、签署日期、签署文件标题/引用和签署依据，准备合同签署证据登记 proposal。
- 该能力不新增传统 ERP 页面：仍在对话框内展示 proposal 卡片，用户本人确认后才写入 `ContractSigningRecord`；不发起电子签署，不修改合同审批状态，不确认付款或收款。
- 准备和确认阶段都会重新校验项目版本、合同归属、合同类型、供应商角色、合同生效状态、签署文件访问权限、`project.read` / `full_outsource_contract.read` / `full_outsource_contract.execute` 精确项目分类权限，以及重复来源引用。
- 合同 Skill 与整套委外 Skill 已同步办理规则：默认只读，只有用户明确要求登记线下签署文件、签署扫描件或签署状态证据，且上下文有真实项目/合同 ID 与依据时才准备卡片。
- 前端通用 proposal 卡片已映射 `contract_signing_record` 到 `/api/contract-proposals`，能力设置文案补充“准备合同签署记录”。
- 验证：`tests/test_contract_tools.py` 覆盖 proposal 不直接写库、本人确认后写入已签署记录、重复来源阻断和已签署缺少签署日期阻断；`tests/test_model_harness.py` 继续验证合同场景只激活合同小工具包，不误激活报价/项目档案等邻近工具。

## 持续开发：供应商扣款责任/结算依据对话办理闭环（2026-09-16）

- 新增 `prepare_supplier_deduction_settlement` 工具，基于整套委外或财务上下文中的真实项目、项目版本、供应商、已生效整套委外合同、工程联络扣款线索和正式责任/结算依据，准备供应商扣款责任/结算 proposal。
- 该能力不新增传统 ERP 页面：仍在对话框内展示 proposal 卡片，用户本人确认后才写入 `SupplierDeductionSettlement`；不会执行收付款，不自动抵扣供应商付款，也不代表客户对我方扣款已完成。
- 准备和确认阶段都会重新校验项目版本、供应商、合同归属和生效状态、合同供应商一致性、工程联络单/事项归属、`full_outsource_contract.read` / `finance.confirm` 精确项目分类权限、重复来源引用和重复结算单号。
- 整套委外 Skill 与财务 Skill 已同步办理规则：默认只读，只有用户明确要求登记扣款责任或结算依据，且上下文有真实业务 ID 与依据时才准备卡片。
- 前端通用 proposal 卡片已映射 `supplier_deduction_settlement` 到 `/api/finance-proposals`，能力设置文案补充“准备供应商扣款结算”。
- 验证：`tests/test_finance_context_tools.py` 覆盖 proposal 不直接写库、本人确认后写入已结算供应商扣款记录、重复来源阻断和已结算缺少结算依据阻断；`tests/test_finance_context_tools.py tests/test_full_outsource_tools.py tests/test_capability_catalog.py` 共 18 项通过，前端 `npm run build` 通过，内置浏览器刷新后控制台无新增 warn/error。

## 持续开发：供应商实际付款确认对话办理闭环（2026-09-16）

- 新增 `prepare_supplier_payment_confirmation` 工具，基于 `query_finance_context` 返回的真实项目、项目版本、已审批供应商付款申请和授权占用余额，准备供应商实际付款确认 proposal。
- 该能力不新增传统财务页面：仍在对话框内展示 proposal 卡片，用户本人确认后才调用人工命令 `finance.confirm` 写入 `PaymentConfirmation` 并扣减本次付款申请 `reservation`；不会执行银行转账，不把付款申请审批通过当成已付款，也不代表供应商全部付清或项目关闭。
- 准备和确认阶段都会重新校验项目版本、付款申请归属、申请生效状态、币种、授权余额、重复付款流水号以及当前用户的 `supplier_payment.read` / `finance.confirm` 精确项目分类权限，避免过期卡片或越权确认继续写入。
- 前端通用 proposal 卡片已映射 `supplier_payment_confirmation` 到 `/api/finance-proposals`，沿用对话内确认流程；财务上下文 Skill 只在财务场景小工具集中暴露该准备工具，避免一次性把大量工具喂给模型。
- 验证：`tests/test_finance_context_tools.py` 覆盖供应商实付确认 proposal 不直接写库、本人确认后写入付款确认并扣减授权余额、重复流水号阻断和实付超出授权余额阻断；`tests/test_domains.py tests/test_manufacturing.py tests/test_finance_context_tools.py tests/test_capability_catalog.py` 共 25 项通过，前端 `npm run build` 通过，内置浏览器加载工作台、权限模式框内切换不跳转且控制台无 warn/error。

## 持续开发：客户实际回款确认对话办理闭环（2026-09-16）

- 新增 `prepare_customer_receipt_confirmation` 工具，基于 `query_finance_context` 可见事实中的真实项目、项目版本、已生效销售合同和收款节点，准备客户实际回款确认 proposal。
- 该能力不新增传统财务页面：仍在对话框内展示 proposal 卡片，用户本人确认后才写入 `CustomerReceiptConfirmation`；不会执行收款、不开票、不计算收入/利润，也不把合同节点、系统提醒或关闭清单当成实际回款。
- 新增人工命令 `customer_receipt.confirm` 和通用 `/api/business/command-intents` 路由，复用 HumanIntent 二次确认；领域规则阻断非生效销售合同、非本合同节点、币种不一致、重复银行流水号、节点累计超额和合同累计超额。
- 前端通用 proposal 卡片已映射 `customer_receipt` 到 `/api/finance-proposals`，人工命令名称补充“确认客户实际回款”。
- 验证：`tests/test_finance_context_tools.py` 覆盖回款确认 proposal 不直接写库、本人确认后写入台账、重复流水号阻断和节点累计超额阻断；`tests/test_finance_context_tools.py tests/test_domains.py tests/test_manufacturing.py tests/test_agent_api.py tests/test_capability_catalog.py` 共 33 项通过，前端 `npm run build` 通过，内置浏览器加载工作台且控制台无 warn/error。

## 持续开发：合同登记对话办理闭环（2026-09-16）

- 新增 `prepare_contract_record` 工具，基于 `query_contract_context` 返回的真实项目、项目版本和销售合同/整套委外合同审批流程，准备合同登记 proposal。
- 该能力不新增传统 ERP 菜单页面：仍在对话框内展示 proposal 卡片，用户本人确认后才创建 `sales_contract` 或 `full_outsource_contract` 业务材料并提交 Agent BPM；审批生效前不视为正式合同，不确认收付款，不触发 ERP 合同执行。
- 销售合同必须关联有效客户，整套委外合同必须关联有效委外供应商；付款节点金额合计不能超过合同金额；同项目同类型的未关闭重复合同号会阻断；替代合同仍要求先完成财务归属核对，不能直接覆盖历史合同。
- `query_contract_context` 在用户具备准备工具时返回按合同类型区分的 `workflow_options`，模型不需要猜流程 ID；合同 Skill 已补充办理规则，要求先查上下文再准备建议，查询请求仍保持只读。
- 前端通用 proposal 卡片已映射 `sales_contract` / `full_outsource_contract` 到 `/api/contract-proposals`，并把付款节点等数组对象显示为可核对的多行内容，而不是 `[object Object]`。
- PostgreSQL `moldpilot_test` 覆盖工具 schema、合同 workflow_options、proposal 不直接建单、本人确认后创建合同材料并提交 BPM、付款节点入库、重复合同号阻断和无效客户阻断；前端构建通过。

## 持续开发：Harness 按场景动态工具暴露治理（2026-09-16）

- 参考 `D:\pi-desktop` 中“模型能看见的工具就会尝试，因此要在发送给模型前控制可见工具”的架构原则，调整 MoldPilot harness，不再把大批业务工具一次性列入模型上下文。
- ToolSearch 从“工具名列表搜索”升级为“能力/场景包激活”：按 Skill 的必需工具和可选工具组成小工具集；搜索准确工具名时仍只激活单个工具，搜索业务场景时才激活对应工具包。
- ToolSearch 新增 `activation_queries` 强匹配：合同、报价、开工、计划、设计、制造、装配试模、交付物流、整套委外、设变、财务、治理、采购价格、工程联络、项目暂停/关闭等场景先按明确触发词选择最佳场景包；没有强匹配时才回退到文本评分，避免“合同登记”同时激活报价承接、报价评估、项目档案等相邻工具。
- Skill 新增 `activation_tools` 精选候选子集：后台能力目录仍保留完整 `optional_tools` 供授权、设置和能力说明使用，但候选子集不会再原样整包发给模型。ToolSearch 会在命中的场景内按本次查询语义重新排序，保留取证所需的查询前置，并且每次最多激活 4 个工具，与 PI Desktop 的延迟工具上限一致。
- 工程联络单候选包保留列表、上下文、处理方案、复验、关闭和反馈六个高频闭环能力；搜索“工程联络关闭”时实际只激活 `query_contact_cases`、`query_contact_context`、`prepare_contact_close`，发起、附件、分派、指定验收负责人、撤销事项等仍可通过准确工具名按需激活，不新增传统菜单页面。
- ToolSearch 增强通用中文连续短语切分和末尾意图加权，像“工程联络关闭”“合同登记”这类无空格短语可在命中场景后区分具体动作；规则不绑定某一个业务流程，不使用自然语言业务兜底。
- 按需工具提示从最多 48 项缩小到最多 12 项能力/工具摘要，超出部分只显示剩余数量，降低 8192 上下文窗口下的工具干扰和选错工具概率。
- 按需目录只展示可读场景名称与 `ToolSearch query=...` 示例，不再把 Skill key 暴露成类似可调用函数的标识；若模型仍把已登记 Skill key 当成函数名，Harness 只允许一次结构化纠正并引导回 ToolSearch，不对任意未知函数做自然语言兜底。
- 工具去重签名改为可打印的 SHA-256 标识，修复签名中的 NUL 字符无法写入 PostgreSQL `jsonb`、导致 ToolSearch 成功后保存 checkpoint 报 HTTP 500 的根因。
- `skill_context` 现在向 harness 提供 `tools` / `optional_tools` / `activation_tools` 元数据，worker 仍通过 MCP 发现全量授权工具，但模型每轮只看到当前激活的小工具集和 ToolSearch。
- 新增工作台技术排障路由防护：当前端、模型、harness、接口、HTTP 500、数据库、Redis、Docker、Navicat、GitHub、构建、部署、日志、上下文窗口等请求不构成明确业务查询/办理时，harness 在模型调用前直接隐藏 ToolSearch 和全部业务工具；“用模型查询项目计划”这类明确业务查询仍可按场景包激活。
- 该改动用于解决模型把技术排障/模型配置问题误导到业务工具链的问题；不是新增业务兜底，也不放宽工具内部权限、版本、审批确认和证据校验。
- 本轮意图门控改为只以 `current_prompt` 决定是否开放业务工具：纯寒暄、感谢、确认短语即使会话历史存在项目也看不到 ToolSearch；“你好，帮我看看 SMOKE-M001 的计划”“老弟，看下这个项目”等本轮包含业务动作的复合句仍开放工具。`recent_requests` 只在本轮明确出现“帮我看看 / 继续 / 这个呢”等省略式动作时补全业务对象，不能单独触发工具；“查一下天气”等无关动作也不会因为历史项目而误开业务工具。
- 对照 `D:\pi-desktop\packages\agent-runtime\src\runtime.ts`、`apps\desktop\src\lib\assistant-turns.ts` 和 `ActivityGroup.tsx`，Harness 改为由模型决定每一轮产生 0、1 或多个 tool call：同一 assistant 消息的整批工具结果依次持久化为 tool 消息并回灌，模型下一轮仍请求工具就继续，不再按单批 5 个或累计 8 个工具强制收口；只保留总工具数、模型轮次、任务时限和上下文窗口安全预算。模型随 tool_calls 返回的可见阶段说明会按 provider 原始顺序进入折叠详情；系统提示要求阶段说明与工具调用同一条消息发送，避免“只报进度就停止”。隐藏思维链不投影、不补写、不伪造。
- Qwen3-30B 真实浏览器任务验证了多轮回灌和折叠归并，但当前 Instruct 模型的三条工具调用 assistant 消息均返回 `content: null`，因此展开区只显示真实 ToolSearch/业务工具活动，没有虚构“模型思考会话”；若后续 provider 返回可见 content 或正式 reasoning summary，现有轨迹会原样显示可见内容。
- 运行轨迹把 ToolSearch 投影为独立的“本轮已启用”Harness 活动，不再伪装成缺少业务时间戳的普通工具结果；时间格式化增加空值与非法值保护，修复后台已成功但前端因 `Invalid time value` 冻结在“用时 2 秒 / 正在等待模型回复”的问题。终态失败中的未返回工具改为“调用中断”，并在一处显示可读失败原因与错误码，避免“执行未完成 / 正在调用”和失败文案重复。
- 验证：`tests/test_model_harness.py` 覆盖通用技术问题不调用业务工具、技术请求隐藏 ToolSearch、准确工具名只激活单工具、场景搜索最多暴露 4 个工具、工程联络关闭只暴露查询前置与关闭动作、合同登记不暴露签署动作、Skill key 误调用可纠正、PostgreSQL `jsonb` checkpoint 可持久化、模型自定六工具批次、跨多轮工具结果回灌及可见阶段说明持久化，以及带“模型”字样的明确业务查询仍允许 ToolSearch；API 测试覆盖 ToolSearch、模型可见阶段说明、工具活动原始顺序和终态中断展示。本功能块完整后端回归、前端生产构建和浏览器控制台检查结果见顶部验证记录。

## 持续开发：项目基线计划对话办理闭环（2026-09-16）

- 新增 `prepare_project_plan_baseline` 工具，基于 `query_project_plan_context` 返回的真实项目、项目版本、完整任务清单和 `baseline_workflow_options.id`，准备初始项目大节点/基线计划 proposal。
- 该能力不新增传统 ERP 菜单页面：仍在对话框内展示 proposal 卡片，用户本人确认后才创建 `project_plan` 业务材料并提交 Agent BPM；审批生效前不下达 ERP 执行任务，也不把内部计划变成客户承诺交期。
- 基线计划只允许正式开工后的 ACTIVE 项目准备；项目未正式开工、已有有效计划、或已有待处理计划/计划变更申请时会阻断，避免把重编计划误做成初始计划。
- 基线计划预览会重新执行领域校验，校验任务日期、依赖、大节点覆盖、项目版本和审批流程；当流程绑定资料模板时继续沿用资料核对包机制。
- 项目计划 Skill 已补充约束：当前没有有效计划且用户要求建立初始计划时，必须先查询计划上下文并使用真实 ID/版本/流程；不得凭自然语言、截图或历史对话构造计划。
- PostgreSQL `moldpilot_test` 覆盖工具 schema、proposal 不直接建单、本人确认后创建 `project_plan` 并提交 BPM、重复计划阻断；前端构建与内置浏览器加载/控制台错误检查通过。

## 持续开发：报价承接/拒单对话办理闭环（2026-09-16）

- 新增 `prepare_quote_acceptance_decision` 工具，基于 `query_quote_acceptance_context` 返回的真实项目、项目版本和报价承接审批流程，生成承接或拒单 proposal。
- 承接时必须确认最终加工方式 `INTERNAL` 或 `FULL_OUTSOURCE`；拒单时禁止填写加工方式。工具会阻止已存在有效承接/拒单、待处理承接/拒单申请，以及已开工项目重复准备承接。
- 该能力不新增传统 ERP 菜单页面：仍在对话框内展示 proposal 卡片，用户本人确认后才创建 `quote_acceptance` 业务材料并提交 Agent BPM；审批生效前不正式承接、不拒单、不正式开工、不修改合同或项目状态。
- Skill 约束已更新：必须先查询报价承接上下文，不得凭自然语言、截图或历史对话构造项目或流程；合同、承接、拒单和正式开工继续作为不同事实处理。
- PostgreSQL `moldpilot_test` 覆盖 schema、承接 proposal 不直接建单、人工确认后提交 BPM、拒单参数校验和重复有效决定阻断；前端构建通过。

## 持续开发：正式开工通知对话办理闭环（2026-09-16）

- 新增 `prepare_internal_start` 工具，基于 `query_internal_start_readiness` 返回的真实项目、项目版本、已生效承接记录和正式开工审批流程，生成正式开工通知 proposal。
- 该能力不新增传统 ERP 菜单页面：仍在对话框内展示 proposal 卡片，用户本人确认后才创建 `internal_start` 业务材料并提交 Agent BPM；审批生效前项目仍为 DRAFT，不下达设计、采购、生产、装配或试模任务。
- Skill 约束已更新：承接、销售合同、内部正式开工和项目计划是不同事实；不得凭自然语言、截图或历史对话构造开工通知；合同晚到不必然阻塞具备依据的开工，但必须保留依据并后续补合同核对。
- 前端 proposal 卡片接入 `/api/internal-start-proposals/{step_id}`，沿用现有人工确认/授权模式，不增加新的 ERP 式页面。
- PostgreSQL `moldpilot_test` 覆盖 schema、正式开工 proposal 不直接建单、人工确认后提交 BPM、项目审批前仍保持 DRAFT、项目版本冲突拒绝旧 proposal；前端构建与内置浏览器加载/控制台错误检查通过。

## 持续开发：项目计划变更部门影响确认会话闭环（2026-09-16）

- 新增 `prepare_plan_department_confirmation` 工具，部门负责人或指定确认人可基于 `query_project_plan_context` 返回的真实 `department_confirmations.id/version` 准备确认 proposal。
- 该能力不新增传统 ERP 菜单页面：仍走对话框内 proposal 卡片与人工确认链路；本人确认后只写入 `plan_department_confirmation=CONFIRMED`，不修改项目计划、不替代计划变更 BPM、不写入 ERP 执行进度。
- 计划变更 Skill 已补充约束：不得凭自然语言、部门名称或历史对话构造确认项，必须先查询计划上下文取得确认项 ID 和版本。
- Proposal 状态查询已支持非审批类 `CONFIRMED` 回执，避免部门确认完成后前端仍显示为未完成。
- PostgreSQL `moldpilot_test` 覆盖工具 schema、部门负责人确认、人工确认后写库、版本冲突拒绝确认；前端构建与内置浏览器加载/控制台错误检查通过。

## 持续开发：FR-077 委外交付、客户验收、付款扣款和关闭追溯上下文（2026-09-16）

- `query_full_outsource_context` 复用交付物流侧已有的 `customer_delivery_signature` 与 `customer_acceptance_record` 数据，不新增重复业务表；整套委外上下文新增 `analysis.customer_delivery_acceptance`。
- 派生状态新增 `has_customer_signature`、`has_customer_acceptance_record`、`has_failed_customer_acceptance`、`has_customer_recheck_passed`、`has_customer_acceptance_deduction`、`has_customer_acceptance_contract_change`，并继续保留供应商付款、供应商扣款结算和关闭清单追溯。
- 工具明确区分供应商发货、我方收货、客户签收、客户验收、供应商付款、供应商扣款、项目关闭：客户签收不会被当成客户验收，付款申请不会被当成项目关闭，客户验收扣款必须与供应商扣款/财务结算联动核对。
- 客户验收/复验/扣款记录受 `project_close.read` 与 `query_project_closure_context` 约束；有限权限用户仍可看到客户签收事实，但不会泄露客户验收失败原因、扣款金额、合同变化要求等敏感验收记录。
- PostgreSQL `moldpilot_test` 验证覆盖客户签收、有条件通过验收、验收扣款、合同变化、交期影响、供应商已结算扣款、供应商付款申请，以及订单权限/验收权限隔离。
- FR-077 仍为 NOT_VERIFIED：真实客户回款、发票、正式财务付款/扣款入账、项目关闭清单业务签署、ERP 财务联调和生产附件安全验收尚未完成正式验收。

## 持续开发：FR-074～075 委外设变议价与交期任务影响上下文（2026-09-16）

- 新增 `outsource_change_negotiation` PostgreSQL 迁移与领域模型，保存项目、供应商、委外合同、工程联络单/任务、客户报价、供应商报价、议定金额、币种、交期影响天数、任务影响摘要、是否需要合同变化、状态、客户/供应商/议价依据和批准人。
- `query_full_outsource_context` 新增 `analysis.outsource_change_negotiations`，并将 `has_approved_outsource_change_negotiation`、`has_open_outsource_change_negotiation`、`has_contract_change_negotiation` 纳入派生状态。
- 有委外设变/整改影响项但没有已批准议价记录时，工具提示新增费用、交期和任务影响仍需采购、项目和供应商确认；草稿、议价中或仅达成未审批的记录不会被视为已落实费用或交期变更。
- PostgreSQL `moldpilot_test` 验证覆盖客户报价、供应商报价、议定金额、交期影响、任务影响、需合同变化、合同权限下可见议价依据和订单权限隔离。
- FR-074～075 仍为 NOT_VERIFIED：真实设变追加/变更合同审批、原版本保护、供应商当前执行评估、正式议价审批、交期重排和 ERP/财务联动尚未完成正式验收。

## 持续开发：FR-076 供应商责任确认与扣款结算上下文（2026-09-16）

- 新增 `supplier_deduction_settlement` PostgreSQL 迁移与领域模型，保存项目、供应商、委外合同、工程联络单/任务、扣款原因、责任归属、扣款金额、币种、结算状态、责任依据、结算依据、确认人和来源。
- `query_full_outsource_context` 新增 `analysis.supplier_deduction_settlements`，并将 `has_confirmed_supplier_deduction`、`has_settled_supplier_deduction`、`has_pending_supplier_deduction` 纳入派生状态。
- 存在扣款/费用影响线索但没有责任已确认的供应商扣款结算依据时，工具会提示不能仅凭延期或质量问题自动认定供应商扣款；责任已确认但未结算时提示同步供应商结算或财务依据。
- `prepare_supplier_deduction_settlement` 已提供对话内办理入口，用户本人确认后才写入责任已确认或已结算扣款记录；不执行收付款，不自动抵扣供应商付款。
- PostgreSQL `moldpilot_test` 验证覆盖责任确认、已结算扣款、合同权限下可见扣款依据、订单权限隔离和整套委外上下文汇总。
- FR-076 仍为 NOT_VERIFIED：真实责任确认流程、整改/复验关闭、客户对我方扣款与我方对供应商扣款联动、财务结算写入和 ERP 财务联调尚未完成正式验收。

## 持续开发：FR-073 整套委外合同签署文件上下文（2026-09-16）

- 新增 `contract_signing_record` PostgreSQL 迁移与领域模型，保存合同业务单、模板名称、签署方式、签署状态、签署日期、签署文件标题/文件引用、供应商签署人、采购核对人、批准人、证据和来源。
- `query_full_outsource_context` 新增 `analysis.contract_signing_records`，并将 `has_signed_full_outsource_contract_file`、`has_unsigned_contract_signing_record` 纳入派生状态。
- 有生效整套委外合同但无已签署文件/签署依据时返回 gaps；草稿、审核中、驳回或取消的签署记录进入 warnings，不会被当作正式签署合同。
- `prepare_contract_signing_record` 已提供对话内办理入口，用户本人确认后才写入签署文件/状态证据；不发起电子签署、不修改合同审批状态、不确认付款。
- 该能力仅覆盖模板/人工审核签订/签署文件上传证据，不接入在线电子签署服务。
- PostgreSQL `moldpilot_test` 验证覆盖已签署合同文件证据、合同权限下可见签署依据、订单权限隔离、整套委外上下文汇总、对话 proposal 不直接写库、本人确认后写入签署记录、重复来源和缺少签署日期阻断。
- FR-073 仍为 NOT_VERIFIED：真实合同模板生成、采购主管提交、总经理审批、正式签署文件上传、附件安全和 ERP/文件存储联调尚未完成正式验收。

## 持续开发：FR-072 客户资料交接与供应商核验上下文（2026-09-16）

- 新增 `supplier_material_handoff` PostgreSQL 迁移与领域模型，保存项目、供应商、委外合同、资料文件或资料标题、资料类型、审批状态、交接日期、交接对象、交接渠道、依据、来源系统和核验人。
- `query_full_outsource_context` 新增 `analysis.supplier_material_handoffs`，将客户资料/设计资料交接与委外合同、供应商节点上报、订单发货收货、验收整改分开展示。
- 派生状态新增 `has_approved_supplier_material_handoff` 与 `has_draft_or_revoked_supplier_material_handoff`；有生效委外合同但无获准资料交接依据时返回 gaps，草稿或撤回记录进入 warnings，不作为正式交接依据。
- `prepare_supplier_material_handoff` 已提供对话内办理入口，用户本人确认后才写入资料交接证据；不创建供应商门户、不代表供应商已核验、不触发 ERP 发货或生产执行。
- PostgreSQL `moldpilot_test` 验证覆盖有效资料交接、订单权限隔离下仍可按合同权限查看资料交接依据、整套委外上下文汇总、对话 proposal 不直接写库、本人确认后写入资料交接记录、重复来源和正式获准交接缺少合同阻断。
- FR-072 仍为 NOT_VERIFIED：真实客户资料上传、附件安全、采购向供应商提供资料的正式审批/回执、供应商核验反馈和 ERP/文件存储联调尚未完成正式验收。

## 持续开发：FR-071 供应商节点上报与采购跟进上下文（2026-09-16）

- 新增 `supplier_progress_report` PostgreSQL 迁移与领域模型，用于保存授权人员录入/导入的供应商阶段上报，不默认要求供应商门户。
- `query_full_outsource_context` 新增 `analysis.supplier_progress_reports`，按项目汇总供应商、合同、计划节点、阶段、上报日期、状态、进度百分比、下次跟进日期、问题摘要、证据和采购跟进人。
- 派生状态新增 `has_supplier_progress_report`、`has_supplier_progress_risk`、`has_overdue_supplier_progress_followup`；供应商节点风险、阻塞、返工或超过下次跟进日期会进入 warnings，提醒采购跟进并同步项目。
- 本轮将 `tests/test_full_outsource_tools.py` 迁移到 PostgreSQL `moldpilot_test`，覆盖有效整套委外路径、合同、计划节点、供应商进度上报、风险/逾期跟进、订单权限隔离和多候选不自动决定。
- FR-071 仍为 NOT_VERIFIED：真实供应商节点模板、填报频率、证据模板、供应商侧接入方式、采购/项目协同回执和 ERP 联调尚未完成正式验收。

## 持续开发：FR-068～069 客户签收、客户验收与复验扣款上下文（2026-09-16）

- 新增 `customer_delivery_signature` / `customer_acceptance_record` PostgreSQL 迁移与领域模型，区分客户签收、移模签收、客户质量验收、复验、责任判断、整改期限、扣款金额、交期影响和合同变化要求。
- `query_delivery_logistics_context` 新增 `analysis.customer_delivery_acceptance`，并把 `has_customer_signature`、`has_customer_acceptance`、`has_failed_customer_acceptance`、`has_customer_recheck_passed`、`has_customer_acceptance_deduction` 和合同变化线索纳入派生状态。
- 客户签收不再硬编码为 false；但签收仍不会自动推断为客户验收通过，客户验收失败且无复验通过时会提示继续跟踪问题、责任、整改和复验。
- 本轮继续使用 PostgreSQL `moldpilot_test` 验证，`tests/test_delivery_logistics_tools.py` 覆盖客户签收与验收分离、验收失败扣款/合同变化/交期影响警示，以及普通仓库视角不泄露验收失败原因和扣款金额。
- FR-068～069 仍为 NOT_VERIFIED：真实客户签字材料、移模业务规则、回款/归档触发、财务扣款、合同变更、供应商整改和 ERP/财务联动尚未完成正式验收。

## 持续开发：FR-065～067 固定物流路线、报价与结算价上下文（2026-09-16）

- 新增 `logistics_route` / `logistics_quote` PostgreSQL 迁移与领域模型，保存项目/全局路线、出发地、接收地、承运商、车型/运输方式、计价单位、含税方式、报价有效期、状态、审批证据和项目本次结算价候选。
- `query_delivery_logistics_context` 新增 `analysis.logistics_pricing`，可返回当前有效路线报价、项目结算价候选、待审批/过期报价和派生状态；没有结构化路线或有效报价时只给缺口，不编造承运商、车型或价格。
- 本轮验证改用 PostgreSQL `moldpilot_test`，`tests/test_delivery_logistics_tools.py` 覆盖供应商发货、仓库收货、入库检验、出库移动、有效路线报价、项目结算价候选、多候选不自动决定和无订单权限不泄露物流单号；不再为该功能新增 SQLite 验收依据。
- 该日只读上下文的缺口已由 2026-09-17 顶部记录补齐为对话确认办理链路；真实仓库/采购签字、物流费用单、承运商对账、ERP/财务联调和正式业务验收仍为 NOT_VERIFIED。

## 持续开发：FR-118 正式验收证据登记机制（2026-09-16）

- 新增配置 `MOLD_ACCEPTANCE_EVIDENCE_FILE`，默认 `.local/acceptance-gates.json`，用于登记部署拓扑、用户规模、响应时间、可用性、备份频率、恢复目标、日志保留、生产附件存储和模型运行边界的正式验收证据。
- 新增 `scripts/acceptance_gates.py`，可生成本地验收模板或查看当前确认状态；模板文件不提交到 Git，避免代码提交冒充业务/实施签字。
- `query_operations_readiness_context` 会读取该证据文件：只有 gate 填写 `confirmed=true`、确认人、确认时间和至少一条 `evidence_refs` 时才标记为 confirmed；格式不完整的确认会进入 `invalid_gate_keys`，不会通过。
- 当前本机机器前提已就绪，但 `.local/acceptance-gates.json` 仍需由实施/业务负责人按实际验收结果填写；未填写前 `acceptance_status` 保持 `NOT_VERIFIED`。

## 持续开发：PostgreSQL 备份恢复 Docker 客户端模式（2026-09-16）

- `scripts/backup_postgres.py` 与 `scripts/restore_postgres.py` 新增 `--client-mode auto|native|docker`，默认 `auto`：优先使用本机 `pg_dump` / `pg_restore`，本机未安装时可使用 Docker 临时 `postgres` 客户端镜像。
- 新增配置 `MOLD_PG_CLIENT_IMAGE`，默认 `postgres:18-alpine`；Docker 模式下本机 `127.0.0.1` / `localhost` PostgreSQL 会映射为 `host.docker.internal`。
- 密码仍只通过 `PGPASSWORD` 环境变量传入客户端；脚本不在命令行参数、日志或 readiness 结果中输出数据库密码。
- `query_operations_readiness_context.backup_restore` 新增 `docker_pg_client`、native/docker 两组可运行状态和脚本支持的 `client_modes`，避免仅因 Windows 未安装 PostgreSQL 客户端而无法表达可交付路径。
- 同步修正 Docker 探测：Docker CLI 存在但 Linux engine pipe 不可用或返回 Internal Server Error 时，readiness 不再误判为 Docker daemon 可用。
- 历史曾使用 Docker PostgreSQL 客户端镜像完成一次备份/隔离恢复演练；当前用户已要求删除 Docker 容器和镜像，后续本机验收不得再把 Docker 镜像作为默认前提。当前 PATH 未检测到 native `pg_dump` / `pg_restore`，需安装 PostgreSQL 客户端或显式配置路径后重新演练。
- 当前 `query_operations_readiness_context` 返回 `backup_restore.status=BACKUP_TOOLING_READY`；正式 RPO/RTO、备份频率、异地位置和恢复演练记录仍需实施验收确认。

## 持续开发：日志保留 dry-run 与受控清理工具（2026-09-16）

- 新增 `scripts/log_retention.py`，默认 dry-run，只连接 `.env` 中的 PostgreSQL `moldpilot`，拒绝 SQLite 和非 `moldpilot` 数据库。
- 真实执行必须同时传 `--execute` 与 `--i-understand-this-will-prune-logs`；审计日志会先归档 JSONL 到 `.local/log-archives` 再删除，登录会话只清理过期/超期会话。
- 模型运行日志保留采用“归档后脱敏”而不是删除会话：归档 `ai_step.result` 与 `ai_run.checkpoint` 后用保留标记替换，不删除用户 prompt、最终业务摘要或会话记录。
- `query_operations_readiness_context` 的 `log_retention` 结果新增 `retention_script`，返回脚本路径、dry-run 默认、执行确认条件、归档目录和处理范围。
- 本机 `.env` 已配置开发验收用保留天数：审计 365 天、应用 180 天、访问 90 天、模型 180 天；`scripts/log_retention.py` dry-run 通过且无可清理旧数据。
- 当前 `query_operations_readiness_context` 返回 `log_retention.status=LOG_RETENTION_POLICY_CONFIGURED`；生产保留期限、部署层应用/访问日志采集、轮转、脱敏、归档和删除策略仍需用户/实施确认。

## 持续开发：移除交付运行链路 SQLite 兜底（2026-09-16）

- 运行时数据库入口 `make_engine` 现在直接拒绝 `sqlite` URL，API/Worker/交付脚本必须通过 `MOLD_DATABASE_URL` 连接 PostgreSQL。
- 会话置顶归档字段和用户头像资料表的运行时补齐逻辑只支持 PostgreSQL，不再保留 SQLite 兼容分支，避免本地浏览器或开发验证绕过 `moldpilot`。
- 浏览器验收种子脚本 `scripts/create_browser_smoke_fixture.py` 已改为只向 PostgreSQL 写入合成 `SMOKE-*` 数据；脚本拒绝 SQLite 文件路径，默认读取 `.env` 中的 `MOLD_DATABASE_URL`，并生成唯一项目号避免重复跑时污染已有业务流程。
- 默认开发库名、测试库名和数据库角色维护脚本统一到 `moldpilot` / `moldpilot_test` / `moldpilot_restore`；README 明确数据库测试只能指向 `moldpilot_test`。
- 本轮仍保留历史单元测试中直接构造的 SQLite 内存测试作为待迁移技术债；它们不得作为交付验收依据，真实开发/浏览器/数据库核对以 PostgreSQL/Navicat 基线为准。

## 持续开发：本机 Redis 与 PostgreSQL-only 测试基线收口（2026-09-16）

- Redis 默认运行口径改为复用用户本机 `D:\Redis`，配置默认连接 `redis://127.0.0.1:6379/0`，新增 `MOLD_REDIS_HOME=D:\Redis`。Docker Redis 容器和镜像已从本机删除，Docker 只保留为显式 `--backend docker` 的可选备用路径。
- `scripts/dev_redis.py` 默认 native 模式，直接检查/启动 `D:\Redis\redis-server.exe`；同时修复脚本直接运行时的 `backend` import 路径，并把 Python Redis 客户端统一为 RESP2 `protocol=2`，兼容本机 Redis 5.0.14.1。
- 本机实测：`scripts/dev_redis.py status` 返回 `host=127.0.0.1`、`port=6379`、`native_redis_server_exists=True`、`redis_reachable=True`；`init-stream --execute` 已创建 `mold:business-events:v1` 与 `notifications-v1`，复查 `stream_exists=True`、`group_ready=True`。
- 新增 `tests/pg_db.py` PostgreSQL-only 测试工厂，拒绝 SQLite URL，仅允许清理隔离库 `moldpilot_test`；把一批历史工具/领域测试从内存 SQLite 切到 PostgreSQL，并按真实外键补齐 `file_object` / `ai_conversation` 等合成数据。
- 验证：`pytest` 对本轮迁移的 81 项测试在 PostgreSQL `moldpilot_test` 上通过；`py_compile` 覆盖 Redis 脚本、运行配置、消息 worker、运行就绪工具和迁移后的测试入口。

## 持续开发：本地 Redis 启动与 stream 初始化脚本（2026-09-16）

- 新增 `docker-compose.redis.yml`，提供显式 Docker 备用 Redis；当前本机不作为默认运行路径。
- 新增 `scripts/dev_redis.py`，默认 `status` 只读核对本机 Redis 端口、Redis PING、业务事件 stream 和通知消费组；`start --execute` 才会启动本机 Redis，`init-stream --execute` 才会创建 `message_worker` 所需 stream/group。
- 当前环境已切换为本机 `D:\Redis`：`scripts/dev_redis.py status` 返回 `redis_reachable=True`、`stream_exists=True`、`group_ready=True`。
- 该脚本已消除 `readiness_summary.machine_blockers.redis` 的本机运行阻断；正式交付仍需目标环境 Redis 持久化、容量、告警、重试、死信和故障恢复演练。

## 持续开发：PostgreSQL 隔离恢复脚本基线（2026-09-16）

- 新增 `scripts/restore_postgres.py`，默认 dry-run，只读取 `MOLD_RESTORE_DATABASE_URL` 指向的隔离恢复库，拒绝 SQLite，并默认拒绝恢复到主库 `moldpilot`。
- 真实恢复必须同时传入 `--execute` 与 `--i-understand-this-will-change-target-db`；恢复到主库还需额外 `--allow-primary-target`，避免误覆盖当前业务库。
- `.env.example` 新增 `MOLD_RESTORE_DATABASE_URL=.../moldpilot_restore`、`MOLD_PG_DUMP_PATH` 和 `MOLD_PG_RESTORE_PATH`；运行就绪工具和脚本会优先使用显式路径，其次查 PATH 和常见 PostgreSQL 安装目录。
- 运行就绪工具的 `backup_restore.restore_script` 会返回恢复脚本是否存在、恢复目标是否配置、`pg_restore` 是否可用。
- 本轮仅完成受控恢复入口和 dry-run 核对；正式 RTO/RPO 仍需实际备份文件、PostgreSQL 客户端工具、隔离恢复库和恢复演练记录。

## 持续开发：运行就绪阻断项汇总（2026-09-16）

- `query_operations_readiness_context` 新增 `readiness_summary`，把数据库、迁移、Redis、部署运行前提、备份恢复工具链、日志保留、生产附件存储和模型运行配置汇总为机器可验证的 `machine_blockers` 与已满足的 `ready_items`。
- 汇总同时输出 `acceptance_gaps`，保留部署拓扑、用户规模、响应时间、可用性、备份频率、恢复目标、日志保留、生产存储和模型运行边界等仍需人工/实施验收的门槛。
- 当前本机 PostgreSQL/moldpilot、Alembic head、Redis stream/group 和模型运行配置已核对；备份恢复工具链因 native `pg_dump` / `pg_restore` 未在 PATH 中检测到，不能再宣称机器阻断项已全部清空。
- `overall_status` 仍为 `BLOCKED`，原因是正式实施/业务验收缺口尚未清空；避免模型把机器前提就绪说成整体交付完成。
- 该汇总供 Agent 回复交付状态时引用，不新增页面、不替代正式压测、恢复演练、生产部署和业务验收。

## 持续开发：部署运行前提只读核对（2026-09-16）

- `query_operations_readiness_context` 新增 `deployment_runtime`，只读探测 Python 运行时、Node/npm、Docker CLI、Docker daemon、Docker compose、前端 `web/dist/index.html` 和后端 API/Agent/消息 Worker 入口文件。
- 探测不会启动 API、Worker 或 Docker 容器，不会构建前端，也不会修改本机进程；只返回命令是否可用、版本摘要、前端构建产物和入口文件是否存在。
- 部署拓扑 gate 的当前证据不再只说“能读取配置”，而是区分 Docker/Node/前端构建产物/后端入口是否满足；缺任一项时保持 `DEPLOYMENT_RUNTIME_INCOMPLETE`。
- 这仍不等于生产验收完成：正式交付还需要目标环境的 API、前端、数据库、Redis、对象存储、Worker 部署边界、健康检查、回滚和告警演练。

## 持续开发：Redis 消息链路只读核对（2026-09-16）

- `query_operations_readiness_context` 的 Redis 核对从“只看 URL 是否配置”升级为只读运行探测：执行 `PING`、`INFO server`、`XINFO STREAM` 和 `XINFO GROUPS`，返回 Redis 是否可达、服务端版本、业务事件 stream、通知消费组是否存在。
- 探测不会创建 stream/group，不会发布、消费或 ACK 任何消息；不可达时只返回错误类型，不暴露 Redis URL、密码或服务端响应正文。
- 可用性 gate 的当前证据现在会区分数据库可读与 Redis 消息链路未就绪，避免把配置了 `MOLD_REDIS_URL` 误判为消息链路可用。
- 本机实际核对仍需以当前运行环境为准；若返回 `REDIS_UNREACHABLE`、`REDIS_REACHABLE_STREAM_NOT_INITIALIZED` 或 `REDIS_REACHABLE_STREAM_EXISTS_GROUP_MISSING`，FR-118 的 Redis/消息运行验收继续保持 NOT_VERIFIED。

## 持续开发：日志保留期限运行基线（2026-09-16）

- 新增运行配置 `MOLD_AUDIT_LOG_RETENTION_DAYS`、`MOLD_APP_LOG_RETENTION_DAYS`、`MOLD_ACCESS_LOG_RETENTION_DAYS`、`MOLD_MODEL_LOG_RETENTION_DAYS`；默认值为 `0`，表示尚未确认，不会被当作交付验收通过。
- `query_operations_readiness_context` 新增 `log_retention`，只读返回审计日志、应用日志、访问日志、模型调用日志保留天数是否已配置，并返回当前 `audit_event` 表行数、最早和最新审计时间。
- 当任一日志保留期限未配置时，工具会写入 limitations，要求明确保留天数、脱敏、归档、检索和删除策略；配置天数本身仍不代表日志采集链路或合规验收完成。
- 本机实际核对显示四类日志保留期限仍为未配置，因此 FR-118 的日志保留门槛继续保持 NOT_VERIFIED。

## 持续开发：PostgreSQL 备份工具链基线（2026-09-16）

- 新增 `scripts/backup_postgres.py`，只读取本机 `.env`，拒绝 SQLite，默认将 PostgreSQL 逻辑备份写入 `.local/backups`，通过 `PGPASSWORD` 环境变量向 `pg_dump` 传递密码，不在控制台或命令参数中暴露数据库密码。
- `--dry-run` 可在不生成备份文件的情况下核对数据库配置、目标库名和 PostgreSQL 客户端工具可用性；本机当前 dry-run 如实返回 `pg_dump_available=False`，因此不能把备份演练标记为完成。
- `query_operations_readiness_context` 新增 `backup_restore`，只读返回备份脚本是否存在、`pg_dump` / `pg_restore` 是否可用、是否具备本机备份和隔离恢复演练的工具前提；工具链不完整时会写入 limitations。
- FR-118 的备份频率和恢复目标门槛仍为未确认：还需要安装/指定 PostgreSQL 客户端工具，执行正式备份，准备隔离恢复库，记录 RTO/RPO 和对象存储/模型配置恢复证据。

## 持续开发：运行就绪工具接入 PostgreSQL/Navicat 基线（2026-09-16）

- `query_operations_readiness_context` 的数据库核对结果新增 `baseline`，明确返回期望引擎 PostgreSQL、期望库名 `moldpilot`、当前配置库名、是否 SQLite、是否匹配交付库名和 `delivery_ready`。
- 运行时健康检查新增实际 SQLAlchemy 方言和 PostgreSQL `current_database()` 只读事实；本机实测返回 `dialect=postgresql`、`current_database=moldpilot`、`matches_expected_database=True`。
- 数据库核对结果新增 `migrations`，只读比较数据库 `alembic_version` 与仓库 Alembic head；本机实测数据库版本和仓库 head 均为 `d2f0a9b1c3e4`，状态为 `MIGRATIONS_MATCH_REPOSITORY_HEADS`。
- 当数据库配置不是 PostgreSQL、库名不是 `moldpilot`，或实际会话没有连到 `moldpilot` 时，工具会在 limitations 中明确警告“不能再用 SQLite 结果作为交付依据”。
- 当数据库迁移版本不等于仓库 head 时，工具会提示先用迁移账号核对或执行 `alembic upgrade head` 后再验收。
- 本轮验证只运行 `scripts/verify_postgres_baseline.py` 与运行时工具直连实际 PostgreSQL；没有新增 SQLite 测试或把 SQLite 结果作为验收依据。

## 持续开发：PostgreSQL 与 Navicat 验证基线修正（2026-09-16）

- 撤回未提交的 SQLite 单元测试思路，后续开发业务库以 `.env` 中 `MOLD_DATABASE_URL` 指向的 PostgreSQL 为准。
- 当前本机开发库已核对为 `127.0.0.1:5432/moldpilot`，`admin` 超级管理员存在且启用。
- 新增 `database/verify_moldpilot_navicat.sql`，可在 Navicat 连接 `moldpilot` 后运行，用于核对当前库、连接用户、admin 账号、关键表行数和 Alembic 迁移版本；脚本不展示密码哈希。
- 新增命令行校验脚本 `scripts/verify_postgres_baseline.py`，复用 Navicat SQL，拒绝 SQLite，检查当前连接库名、admin 超级管理员状态和 Alembic head 一致性，作为后续本地验收基线。
- README 和 `.env.example` 删除旧的 `55432/agent_db` 说明，改为以 `.env` / `moldpilot` 为权威，并明确不要再使用 SQLite 作为开发业务库。

## 持续开发：对话依据中的设计改版与计划复核展示（2026-09-16）

- “查看完整依据”弹窗复用业务事实组件，新增项目计划表、设计改版影响和计划复核候选的高亮展示，不新增传统 ERP 菜单或独立页面。
- 当工具结果包含 `analysis.revision_impact` 时，前端显示比较状态、图纸版本、BOM 差异汇总、路线变化、任务关联变化和受影响计划任务，帮助用户在对话证据里直接看清改版影响。
- 当工具结果包含 `analysis.plan_change_candidates` 时，前端显示候选状态、下一步工具、计划基线、受影响节点、证据缺口和防护口径，明确这只是计划复核候选，不是已调整计划。
- 项目大节点/计划任务表继续从 `analysis.tasks` 渲染，保持对话工作台形态；正式计划变更仍须先查计划上下文、本人确认 proposal 并通过 BPM 生效。

## 持续开发：设计改版到计划变更的桥接候选（2026-09-16）

- `query_design_route_context` 在 `revision_impact` 存在 BOM/数量/路线/任务关联差异时新增 `analysis.plan_change_candidates`，把设计改版影响结构化为项目计划复核候选。
- 候选只提供计划上下文复核种子：项目 ID、项目版本、可能的当前有效计划 ID、受影响任务 key、设计版本来源和改版意图；不会直接生成完整 `prepare_project_plan_change.tasks`，也不会自动修改计划。
- 种子状态为 `READY_TO_QUERY_PLAN_CONTEXT` 时，也必须先调用 `query_project_plan_context`，以返回的真实 `previous_id`、完整任务清单和 `workflow_options` 为准；BOM 数量、采购/委外路线或任务关联变化不能自动推导节点日期、自动顺延全部节点或修改客户承诺交期。
- “设计BOM与路线上下文核对”Skill 增加可选计划桥接规则，硬依赖仍只有 `query_design_route_context`；`query_project_plan_context` 与 `prepare_project_plan_change` 是可选能力，不会阻断纯设计核对。
- 新增 SQLite 单元测试覆盖设计改版差异生成计划上下文查询种子、能力目录可选依赖不变成硬依赖。后续仍需把计划上下文复核结果与本人确认的计划变更 proposal 串成端到端浏览器验收流。

## 持续开发：设计图纸/BOM/路线改版影响评估（2026-09-16）

- `query_design_route_context` 新增 `analysis.revision_impact`，按当前生效设计路线与上一版可比较设计路线输出图纸版本、BOM 新增/移除、数量变化、路线变化和计划任务关联变化。
- 改版影响只比较当前用户可见的 Agent 设计路线事实；不会生成图纸、上传设计成果、同步 ERP BOM、自动调整计划或下达采购/加工任务。
- 当没有生效设计版本或当前可见范围没有上一版可比较设计时，工具返回明确状态和限制说明，避免模型用自然语言兜底猜测“没有变化”。
- 有计划工具权限时，改版影响会把被新增、移除或变化的 BOM 项关联到可见计划任务；无计划权限时不泄露计划任务名称。
- 更新“设计BOM与路线上下文核对”Skill：模型可以引用 `revision_impact` 解释改版差异，但不得把影响评估说成正式设计同步、计划变更或业务执行完成。
- 新增 SQLite 单元测试覆盖上一版与当前生效版的 BOM 新增、数量变化、计划任务影响，以及缺上一版时的明确状态。FR-043～047 仍未完成真实设计上传/OCR、ERP 设计/BOM 同步、正式业务验收和计划变更审批联动闭环。

## 持续开发：设变到计划变更的 Skill 路由治理（2026-09-16）

- `change_intake_review` Skill 增加工程联络影响计划候选的使用规则：看到 `plan_adjustment_candidates` 时只能作为候选证据，必须先核对 `evidence_gaps`，不能直接声称计划已调整，也不能直接拼 `prepare_project_plan_change` 参数。
- 能力目录新增 `optional_dependencies` 元数据，`change_intake_review` 的硬依赖仍只有 `query_change_intake_context`；`query_project_plan_context` 和 `prepare_project_plan_change` 只是可选桥接能力，不会让没有计划变更权限的用户失去只读设变核对 Skill。
- 当可选计划工具可用且用户明确要求办理计划调整时，Skill 要求先按 seed 回查 `query_project_plan_context`，以真实 `project_id`、`project_version`、当前有效 `previous_id`、完整任务清单和 `workflow_options` 为准，再进入“项目计划变更”Skill。
- 新增能力目录单元测试覆盖可选依赖不会被误当作硬依赖。

## 持续开发：工程联络影响计划调整候选（2026-09-16）

- `query_change_intake_context` 新增 `analysis.plan_adjustment_candidates`，把工程联络事项中影响计划节点、WIP 任务、返工/重发/暂停/取消或登记交期影响天数的记录结构化为计划调整候选。
- 候选只做事实桥接：精确匹配当前可见计划任务 ID、任务标识或任务名称，返回联络单、事项、影响说明、交期影响、匹配计划任务、候选状态、证据缺口和推荐下一步工具；不按模糊文本猜任务，不自动顺延计划，不跳过项目负责人核对和 BPM 审批。
- 每个候选新增 `plan_change_prepare_seed`：冻结项目 ID、项目版本、匹配到的当前计划 `previous_id`、候选任务 key、联络事项来源和变更意图，并强制说明必须先调用 `query_project_plan_context` 获取完整任务清单和 `workflow_options`；不会直接拼装 `prepare_project_plan_change.tasks`。
- 若缺少已审批生效的联络单处理方案、未精确匹配计划任务或责任部门尚未反馈执行依据，候选状态保持 `NEEDS_CONTEXT` 并输出缺口；证据齐全时才标记 `READY_FOR_PLAN_CHANGE_PREPARE`，其 seed 也只到 `READY_TO_QUERY_PLAN_CONTEXT`，供模型随后读取真实计划上下文再准备 `prepare_project_plan_change`。
- 新增 SQLite 单元测试覆盖客户设变联络事项映射到计划返工任务、按权限推荐计划变更工具、准备种子包含 `previous_id`/项目版本/完整任务列表约束、无联络读取权限时不泄露候选，以及缺处理方案/缺计划匹配时 seed 保持 `NEEDS_CONTEXT`。

## 持续开发：项目计划时间线与看板数据契约（2026-09-16）

- `query_project_plan_context` 的 `analysis.visualization` 新增 `project_plan_visualization_v1` 数据契约，按当前有效计划输出可渲染时间线、未开始/进行中/已完成看板列，以及逾期、依赖阻塞、客户交期风险三个风险泳道。
- 时间线行保留任务 ID、标识、名称、负责人、计划/实际时间、计划工期、前置依赖、等待前置节点和风险标记；这只是事实结构化，不会重排任务、不登记开完工、不把未知实际进度补成自然语言结论。
- 看板数据会附带 `external_progress`，标明 ERP 实际进度是否已通过只读引用解析；ERP 未配置、未登录、无模具引用或异常时只返回状态，不把未知进度当作无风险或无待办。
- 该契约用于对话依据和右侧面板渲染，不新增传统 ERP 菜单页面；最终交互式甘特图的工作日/节假日、资源负荷、齐套率和拖拽改期规则仍需适配验收。
- 新增 SQLite 单元测试覆盖时间线排序、看板列、逾期/依赖阻塞/客户交期风险泳道，以及 ERP 未配置和 Mock ERP 已解析两种外部进度状态。

## 持续开发：工程联络附件关联通知与审计（2026-09-16）

- 工程联络单附件关联继续走会话上传原件、`prepare_contact_attach` 操作建议和本人确认，不允许模型直接把私有会话文件挂到业务对象；关联后仍按 `contact.read` 重新校验下载和会话附件可见性。
- `contact.attachment_added` 事件现在按联络单发起人、指定复验负责人、有效协作事项创建人、处理人和责任部门负责人计算协作收件人；通知投递前 message worker 仍会二次校验收件人对该联络单的读取权限，通知正文不携带附件内容。
- 联络单操作审计从单纯记录 revision 扩展为冻结 `record_kind` 与具体记录明细；附件关联审计包含附件版本、文件名、sha256、前序版本、文件 ID 和通知收件人，便于追溯材料版本。
- 前端智能体把头像上传入口收敛到设置页“账号信息”，账号下拉保持轻量；本轮保留其 UI 改动并纳入构建验证。
- 新增测试覆盖附件关联后只通知有业务读取权限的协作参与人、不通知操作人本人，且审计明细冻结文件名与 sha256。附件正文解析、OCR/手写识别、正式附件保留策略和生产对象存储恢复演练仍待验收。

## 持续开发：项目计划变更会话提案闭环（2026-09-16）

- 新增 `prepare_project_plan_change` 会话工具和“项目计划变更”能力。模型必须先通过 `query_project_plan_context` 获取真实项目、项目版本、当前有效计划 `previous_id` 和任务清单，再准备计划变更建议；不能只凭自然语言线索改计划。
- `query_project_plan_context` 在用户具备计划变更 prepare 能力和相应读/提交权限时返回 `workflow_options`，并允许该能力读取当前有效 `plan_change` 作为计划基线；没有通用 `query_plan_change` 工具时也不会退回猜流程 ID 或漏判当前有效变更计划。
- 计划变更 prepare 阶段只返回 `project_plan_change` proposal，不创建业务材料、不关闭原计划、不修改任务日期。提案卡沿用统一 `confirmation_policy`，本人确认后才创建 `plan_change` 业务材料并提交 Agent BPM；审批生效前原计划和执行任务不改变，客户承诺交期也不自动修改。
- 计划变更预览重新执行领域校验：必须关联有效原计划，项目版本和审批模板必须匹配，已开工任务不能删除，已完成任务不能重排，任务依赖不能成环或违反日期顺序。确认前再次对比 display 哈希，资料变化会阻断旧提案。
- 计划变更提案已接入通用资料核对包：`workflow_options` 会返回资料模板流程并标记 `material_required`；若审批模板要求资料，`prepare_project_plan_change` 必须传入本人已确认且与模板匹配的 `material_review_id`，确认提交后由 `submit_subject` 生成 `MaterialBinding` 并把资料哈希、文件 sha256、review_hash 和 material_data 冻结进审批快照。
- 计划变更审批生效时新增 `plan.change.effective` 事件，按新增、删除、日期/名称/负责人变化的任务计算受影响节点负责人并写入 Outbox；消息 worker 会在投递通知前重新校验收件人对该 `plan_change` 的读取权限。
- 计划变更影响范围新增 `affected_departments` 结构化矩阵：按新增、删除、责任人变更和日期/名称调整汇总部门、责任人、任务标识和变更类型。提案预览会展示受影响部门，审批生效事件和审计详情会冻结同一份矩阵，后续可用于部门确认卡、通知和看板渲染。
- 计划变更审批生效后会按 `affected_departments` 创建 `plan_department_confirmation` 部门确认项：优先派给组织目录中的部门负责人，没有部门负责人时派给受影响任务责任人兜底。`query_project_plan_context` 在有 `plan_change.read` 权限时返回确认状态；`/api/plan-department-confirmations/{id}/confirm` 只允许指定确认人、部门负责人或超级管理员在版本匹配时确认，并记录审计和通知。
- 计划上下文新增 ERP 执行进度只读引用契约：通过现有 `ERPIdentity` 和 `ERPClient` 的固定白名单路径读取 `/system/projectNode/list` 与 `/system/productionSchedule/list`，按模具号和项目号返回 `erp_execution_progress`。返回值只保留节点、工单、状态、计划/实际时间、进度和 ERP 原生引用，不写入 Agent 计划任务，也不把未知/异常当作“无进度”。
- `/api/project-plan-proposals/{step_id}` 和 `/intent` 提供浏览器确认入口，并把来源 Run 的 `agent_permission_mode` 传递到 BPM 提交；显式授权模式下仅在后续流程节点满足委托条件时才可能自动同意。
- 新增 SQLite 单元测试覆盖计划变更上下文返回有效变更计划和审批流程、proposal 不写业务、HumanIntent 确认后才创建 `plan_change` 并提交 BPM、`delegated_auto` 传递到 `submit_subject`，资料模板流程缺少已确认核对包时阻断、带核对包确认后冻结为 `material_binding`，以及计划变更生效后通知受影响节点负责人、生成部门确认项、派发部门确认通知并完成负责人确认。另新增 Mock ERP 测试覆盖未配置时不兜底、已登录时按模具号读取 ERP 项目节点/生产进度并过滤非契约字段，并覆盖计划时间线/看板数据契约。真实 ERP 环境联调、甘特图/看板完整交互样式仍待验收。

## 持续开发：资料模板 XLSX 解析与核对确认（2026-09-15）

- 新增保守 XLSX 资料预览解析器和 `/api/material-templates/{id}/xlsx-preview`。用户可把本人有权访问的 XLSX 原件、已发布资料模板和临时列映射提交给后端，得到待人工核对的 `material_data` 草稿、模板版本、文件版本、问题列表和限制说明。
- 新增 `material_template_xlsx_mapping` 版本表、迁移 `e18f0a6b9c2d` 和 `/api/material-templates/{id}/xlsx-mappings`。管理员可为已发布资料模板保存 Excel 映射版本，预览接口不传临时映射时会使用最新保存映射，并在审计和响应中返回映射版本、映射哈希和创建人。
- 新增 `material_review` 核对包表、迁移 `a2b7c9d4e5f6` 和 `/api/material-templates/{id}/xlsx-reviews`。用户可把 XLSX 解析结果持久化为本人核对包，记录资料模板哈希、映射哈希、文件 sha256、结构化 `material_data`、issues 和 `review_hash`；无 issues 的核对包可通过 `/confirm` 人工确认并记录确认人、确认时间和审计事件。
- 新增 `material_binding` 提交绑定表、迁移 `b3c8d1e2f4a7` 和 `SubmitInput.material_review_id`。正式提交带资料模板的流程时，后端只接受本人已确认且结构匹配的核对包；确认提交后会创建绑定记录，并把 `material_data`、绑定 ID、模板/映射哈希、文件 sha256、review_hash 和确认信息冻结进审批实例快照。
- 解析器只读取 OOXML 包内已有单元格值，不执行宏、外部链接或公式；含公式、缺列、类型不匹配、重复行标识、缺少稳定行标识等都会进入 `issues`，状态为 `NEEDS_REVIEW`。金额、数值、日期和布尔值复用资料规则解释器的类型约束，避免把 Excel 文本随意转换为业务事实。
- 预览、映射和未确认核对包仍不创建业务材料绑定；正式提交不接受客户端传入的任意 `material_data`。后续仍需补字段级权限、前端核对/选择界面，以及多份资料模板/附件内容核对。
- 新增测试覆盖成功解析临时映射、保存映射版本后复用预览、模板版本哈希冲突阻断、公式单元格/重复行号进入待核对、干净核对包确认成功、有 issues 的核对包禁止确认，以及已确认核对包提交时冻结为 `material_binding`、未确认核对包继续阻断提交。

## 持续开发：Agent 自动审批委托基础（2026-09-15）

- 新增 `AgentApprovalDelegation` 策略表、迁移 `c3f2a91d4b6e` 和自助 API。用户只能为自己的流程节点创建/撤销 Agent 自动同意委托；委托变更会更新 `security_version` 并进入授权指纹。
- BPM 节点新增显式布尔标记 `agent_auto_approval` 和可选安全条件 `agent_auto_policy.condition`。只有本轮会话明确选择 `delegated_auto`、流程节点允许自动审批、节点安全条件明确满足、当前席位用户存在有效委托、当前仍有审批权限、材料完整且规则允许 `APPROVE` 时，运行时才会调用同一套审批决定逻辑自动同意。默认“每次询问”模式即使存在历史委托也不自动审批；未配置、标记为 false、安全条件不满足或资料不足的节点继续强制人工办理。
- 自动审批写入 `ApprovalAction.user_snapshot.actor_type=AGENT_DELEGATED` 与 `delegation_id`，审计事件同步记录 actor_type/delegation_id；它不是模型自然语言同意，也不绕过人员席位、业务权限、资料版本、驳回规则或流程路由。
- 前端已在输入框工具栏接入 Agent 权限模式下拉，选择不会跳转设置页，会按当前用户本机持久化并随 `/api/runs` 写入本轮 Run checkpoint；worker claim、运行历史和中间 checkpoint 均保留 `agent_permission_mode`，Harness 会把本轮权限模式写入模型系统上下文，用于区分“每次询问”和“按授权自动审批”的运行语境。
- 会话中的正式操作建议新增后端统一 `confirmation_policy`，在卡片和确认弹窗中明确显示“必须本人确认”“本人确认后提交审批”或“本人确认后授权节点可自动审批”。工程联络方案、项目暂停/恢复、项目终止/关闭等 proposal 从来源 Run 继承 `agent_permission_mode`；本人确认后提交 BPM 时会继续把 `delegated_auto` 传入审批提交，默认 ask 仍不触发自动审批。
- 设置页提供个人自动审批授权/撤销，并在审批流程配置节点中提供 `agent_auto_approval` 开关和安全条件编辑；授权选项只来自已发布且明确允许自动审批的节点。
- 新增 SQLite 单元测试覆盖默认“每次询问”不触发自动审批、显式 `delegated_auto` 才允许节点自动同意、强制人工节点不被委托绕过、节点安全条件阻断自动审批、委托撤销改变授权指纹、API 只暴露/接受显式自动审批节点、输入框 Agent 权限模式可进入 Run 历史与 worker 上下文，以及会话 proposal/intent 将 delegated_auto 传递到 BPM 提交。管理员策略模板、批量委托、通知摘要和正式验收仍待补齐。

## 持续开发：业务能力目录后端化（2026-09-15）

- 新增后端 `capability_descriptor`，`/api/capabilities` 与管理员能力分配接口统一返回工具/Skill 的名称、业务类别、部门、类型、人工确认模式和依赖工具。前端设置页和管理员能力页优先使用后端元数据，旧本地映射仅作为兼容兜底。
- 能力目录继续用于智能体工作台的工具与 Skill 组织，不新增 ERP 式菜单；部门仅用于分类和参与关系展示，不授予数据权限，实际可用性仍由能力启用、业务权限、数据范围和 Skill 依赖取交集决定。
- 新增单元测试覆盖后端目录元数据；专用 PostgreSQL API 用例仍按既有测试环境保护跳过。完整在线可编辑的业务流/部门/资料输入/回执/失败恢复目录仍未交付。

## 持续开发：运行交付就绪核对（2026-09-15）

- 新增 `query_operations_readiness_context` 只读工具和“运行交付就绪核对”Skill，用于核对 FR-118 的部署拓扑、用户规模、响应时间、可用性、备份频率、恢复目标、日志保留、生产附件存储和模型运行边界。
- 工具只返回脱敏运行事实、数据库健康检查、Redis 配置形态、文件存储形态、模型上下文/超时配置、Worker/凭据布尔状态和运行计数；不泄露数据库密码、Redis 密码、API Key、S3 密钥或 Worker 密钥。
- 验收门槛明确区分“当前配置存在”和“正式验收通过”。部署、用户规模、响应时间、可用性、备份频率、恢复目标和日志保留期限在没有实施方案、压测、恢复演练或生产验收前继续标记未确认，不承诺 SLA、性能、准确率、RTO 或 RPO。
- 新增 SQLite 单元测试覆盖工具/Skill 注册、超级管理员可用、FR-118 七项门槛未验收状态和敏感信息脱敏。FR-118 仍为 NOT_VERIFIED：Docker/生产拓扑、真实 Redis、对象存储恢复、备份策略、RTO/RPO、日志保留和压测验收尚未完成。

## 持续开发：工程联络单办理状态诊断（2026-09-15）

- `ContactCase` 明细和 `query_contact_cases` 工具新增 `progress_summary`，由运行时根据联络单模式、协作事项、反馈、复验、最新处理方案和关闭记录派生办理状态，而不是让模型按自然语言猜测。
- 状态诊断覆盖历史补录、草稿、待分派、待反馈、待复验、待方案审批、复验过期和可关闭等场景，并返回 `blockers` 与 `next_actions`，明确“线下记录、处理反馈或方案审批通过都不单独等同于整改完成、复验合格或联络单关闭”。
- 该能力继续保持工程联络单为 Agent 独立业务对象，不调用 ERP 旧异常流程，不新增常驻 ERP 式菜单；它用于会话和工作区判断下一步该由谁办理、为什么还不能关闭。
- 新增联系人测试覆盖历史补录不自动认定最终关闭，以及线上联络单从草稿、待分派、待反馈到待复验的状态诊断。FR-082～090 仍为 NOT_VERIFIED：完整生产岗位矩阵、真实 ERP 执行回执、成本/计划/合同正式联动和业务验收仍未完成。

## 持续开发：整套委外协同上下文核对（2026-09-15）

- 新增 `query_full_outsource_context` 只读工具和“整套委外协同上下文核对”Skill。用户按项目、合同、供应商、委外节点、质量延期、整改复验、扣款、付款或结算线索提问时，模型可核对最终加工方式、有效整套委外合同、项目计划节点、供应商发货/我方收货、工程联络异常、设变整改影响、供应商付款和客户验收/关闭清单。
- 工具明确报价委外金额、整套委外合同、供应商节点上报、我方收货、客户验收、供应商付款和扣款结算是不同事实，不创建供应商门户、不生成或签署合同、不下达委外、不登记扣款、不确认付款或验收。
- Skill 同步用户口径：不默认供应商门户；在线电子签署已取消，保留模板、人工审核签订及签署文件上传；质量或延期扣款必须结合合同、责任确认、整改/复验和结算依据，不能只凭延期自动认定全部由供应商承担。
- 首版测试覆盖有效整套委外路径、委外合同、供应商执行/收货检验、质量延期扣款联络、供应商付款、客户验收清单、多候选不自动决定和无订单工具不泄露发货单；2026-09-16 已迁移到 PostgreSQL `moldpilot_test`，并补充供应商节点上报、风险/阻塞和逾期跟进验证。FR-070～077 仍为 NOT_VERIFIED：真实供应商节点模板/频率、合同签署文件、客户资料交接、设变议价、责任确认、复验关闭、扣款结算和 ERP 财务联调尚未完成验收。

## 持续开发：装配试模上下文核对（2026-09-15）

- 新增 `query_assembly_trial_context` 只读工具和“装配试模上下文核对”Skill。用户按项目、装配任务、试模申请、机台资源、试模报告或异常线索提问时，模型可核对项目计划节点、设计 BOM 路线、装配任务下发、装配执行确认、试模申请、试模结果和工程联络异常上下文。
- 工具明确实际装配开完工、试模开始/完成等执行事实应复用 ERP 或正式业务回执；Agent 只展示当前可见事实和证据缺口，不下达装配工单、不登记装配开完工、不安排试模、不修改 ERP 装配/试模数据。
- 派生状态区分装配计划节点、装配工单、装配开工、装配完工、试模申请、试模结果、试模通过/未通过和未关闭异常。试模通过不等于客户验收、出厂放行或项目关闭；试模未通过会提示工程联络、整改责任和重新验证依据。
- 新增 SQLite 单元测试覆盖装配/试模计划与设计路线聚合、装配完工、试模未通过、工程联络异常、多候选不自动决定和无试模权限不泄露报告。FR-062～063 仍为 NOT_VERIFIED：真实 ERP 齐套率/关键件口径、钳工主管确认、装配工单联调、试模资源可用性、试模报告附件解析、出厂自检资料和执行回执联调尚未完成验收。

## 持续开发：交付物流上下文核对（2026-09-15）

- 新增 `query_delivery_logistics_context` 只读工具和“交付物流上下文核对”Skill。用户按项目、发货、物流、出库、客户签收、客户验收或验收异常线索提问时，模型可核对交付计划节点、正式订单发货、仓库收货、入库检验、库存移动、试模结果、结项清单和工程联络异常上下文。
- 工具明确采购发货、仓库收货、入库检验、出库、客户签收和客户验收是不同事实，不确认交付、不维护物流报价、不登记客户签收或验收，也不修改 ERP 仓储或发货数据。
- 派生状态区分供应商发货、仓库收货、入库检验、不合格数量、出库移动、试模通过/未通过、客户签收、客户验收和未关闭交付/质量异常。试模通过或供应商发货不等于客户签收，客户签收也不等于质量验收合格。
- 同步修正模型执行循环：相同工具和相同参数已取得证据后不再重复执行，而是进入最终回答阶段；最终回答若不是约定 JSON，只触发有上限的协议纠错回合，不把自然语言强行包装成业务结果。
- 首版测试覆盖供应商发货、仓库收货、入库检验、出库移动、试模通过、客户验收清单、客户验收质量联络、多候选不自动决定和无订单权限不泄露物流单号；2026-09-16 已把交付物流测试迁移到 PostgreSQL `moldpilot_test`，并补充固定路线/有效报价/项目结算价候选、客户签收、客户验收失败、复验缺口、扣款和合同变化线索验证。FR-064～069 仍为 NOT_VERIFIED：真实出厂检验、客户签字材料、复验执行回执、财务扣款和 ERP 出入库/发货/财务联调尚未完成验收。

## 持续开发：制造工序与质检上下文核对（2026-09-15）

- 新增 `query_manufacturing_quality_context` 只读工具和“制造工序与质检上下文核对”Skill。用户按项目、计划任务、工序、物料或工程联络线索提问时，模型可核对项目计划任务、加工/工序类任务、实际开始/完成日期、设计 BOM 路线、装配/试模和工程联络异常上下文。
- 工具明确计划任务实际日期只能作为当前 Agent 可见的执行事实，不等同于完整现场报工、实际耗时、设备、人员班组或检测报告。任务完成不自动代表检验合格，方案审批也不代表整改和复检已经完成。
- 内部加工、采购、委外路线分开展示；存在采购或委外路线时提示内部制造结论须结合局部委外交接、收货和检验依据。设计路线和物料只在用户具备设计路线工具/权限时返回，不通过制造上下文泄露 BOM 或物料编号。
- 新增 SQLite 单元测试覆盖制造任务实际开工、未完工状态、质量/返工联络事项、制造上下文缺口、多候选不自动决定和无设计权限不泄露物料/BOM。FR-058～061 仍为 NOT_VERIFIED：真实 ERP 制造报工、工时设备人员明细、检测报告、仓库签收、采购通知、入库检验和不合格整改复检闭环尚未完成验收。

## 持续开发：中标接收与客户规则上下文核对（2026-09-15）

- 新增 `query_bid_intake_context` 只读工具和“中标接收与客户规则核对”Skill。用户按项目 ID、项目编号/名称、合同、承接、开工或模具线索提问时，模型可核对客户分类、客户规则键、模具关联、可见销售合同、承接/拒单和正式开工上下文。
- 工具不读取邮箱、不连接客户平台、不上传合同或模具图片；海尔等客户平台自动对接不作为已具备能力。未见客户邮件、平台文件、人工上传来源或模具图片时返回 `gaps`，防止模型编造中标资料来源、合同来源或 UG 图片依据。
- 客户分类只来自当前可见项目档案与客户规则，不能靠项目名称或单个字段猜测海信、海尔或其他客户规则。合同、承接、拒单和开工分别展示，合同存在不等于已承接，承接存在不等于已开工。
- 模具关系需要项目业务档案读取权限；未授权时不会通过中标上下文泄露内部模具号。新增 SQLite 单元测试覆盖客户分类/合同/模具/承接上下文、多候选不自动决定、合同号和模具号权限隔离。FR-013～018 仍为 NOT_VERIFIED：真实邮件/文件接收、合同上传、模具图片材料、待处理记录或开工草稿创建、人工客户规则维护和岗位通知矩阵尚未完成验收。

## 持续开发：报价评估与加工方式上下文核对（2026-09-15）

- 新增 `query_quote_evaluation_context` 只读工具和“报价评估与加工方式核对”Skill。用户按项目 ID、项目编号/名称、报价、承接、合同或模具线索提问时，模型可核对报价阶段金额/币种/依据文本、最终加工方式、项目档案当前加工方式、可见销售合同、可见整套委外合同和计划任务摘要。
- 工具明确区分综合证据文本与正式结构化成本核算、粗略工艺分析、项目工期估算及客户反馈资料；当前会返回 `gaps`，不把综合证据文本或下游合同事实说成已完成完整报价评估。
- 加工方式按报价阶段、承接确认和执行中变更分层核对。若报价/承接记录与项目档案当前 `execution_mode` 不一致，或历史记录出现多个加工方式，工具只提示需要核对当前有效依据，不自动切换项目方式或下推任务执行。
- 合同上下文仍受 `query_sales_contract` / `query_full_outsource_contract` 或合同上下文能力约束，未授权时不泄露合同号。新增 SQLite 单元测试覆盖金额/加工方式/客户下游事实派生状态、结构化评估缺口、多候选不自动决定和合同权限隔离。FR-008～012 仍为 NOT_VERIFIED：完整报价资料接收、正式成本/工艺/工期评估表单、客户提交反馈、执行中加工方式变更审批及报价成本与实际成本映射尚未完成验收。

## 持续开发：采购价格与订单跟踪上下文核对（2026-09-15）

- 新增 `query_procurement_price_context` 只读工具和“采购价格与订单上下文核对”Skill。用户按项目、料号、料品名称、价格单、供应商、采购申请或采购订单线索提问时，模型可核对当前可见的正式料品、采购价格版本、设计BOM采购/委外需求、采购申请、正式订单、发货、收货、检验和异常上下文。
- 工具明确不创建料品、不询价、不议价、不下单、不收货、不入库；已有 ERP 采购/仓储/物流执行仍以对应正式回执为准。没有记录不能推断供应商未生产、未发货或质量合格。
- 返回 `effective_prices`、`open_price_reviews`、`design_procurement_needs_without_visible_price`、`order_tracking`、`warnings` 和 `derived_status`，区分无有效采购价、价格审批未完成、设计采购需求未匹配价格、正式订单未完全发货和供应商发货异常。
- 缺少设计路线、采购申请或正式订单工具时写入 limitations，不通过价格上下文泄露隐藏订单号、采购申请或联络材料。新增 SQLite 单元测试覆盖价格/设计需求/申请/订单跟踪聚合、权限隔离、多候选和无有效价格提醒。FR-048～054 仍为 NOT_VERIFIED：完整料号编码规则、通用/分类/专用价格优先级、真实询比议价材料、资产采购合同台账、客户供试模料判断、ERP 发货/收货/入库联调和不合格闭环验收尚未完成。

## 持续开发：设计BOM与加工路线上下文核对（2026-09-15）

- 新增 `query_design_route_context` 只读工具和“设计BOM与路线上下文核对”Skill。用户按项目 ID、项目编号/名称、设计单号、图纸版本、BOM 物料、计划任务或工程联络线索提问时，模型可核对当前可见设计版本、BOM 明细、内部加工/采购/委外路线、关联计划任务和工程联络影响。
- 工具仅汇总 Agent 已登记的设计路线业务事实和当前授权范围内的计划/联络上下文；不生成图纸、不上传设计成果、不同步 ERP BOM，也不把采购、加工、装配或试模执行视作已完成。
- 返回 `route_summary`、`linked_plan_tasks`、`engineering_contact_impacts`、`warnings` 与 `derived_status`，明确区分无生效设计、多生效设计、未完成设计审批、内部加工未关联计划任务以及工程联络影响。缺少计划或工程联络工具时写入 limitations，不通过上下文工具泄露隐藏计划或联络标题。
- 新增 SQLite 单元测试覆盖生效设计/BOM/路线汇总、计划与工程联络影响聚合、计划/联络权限隔离、多候选不自动决定、无生效设计提醒。FR-043～047 仍为 NOT_VERIFIED：真实设计上传/OCR或ERP设计成果适配、设计委外完整排程、试模料标准附件、图纸改版创建/关联设计订单和真实业务验收尚未完成。

## 持续开发：项目计划上下文与大节点表格核对（2026-09-15）

- 新增 `query_project_plan_context` 只读工具和“项目计划上下文核对”Skill。用户按项目 ID、项目编号/名称、计划单号或任务关键字提问时，模型可读取当前可见有效计划、计划变更、任务依赖、运行中节点、逾期节点、客户承诺交期风险和大节点覆盖情况。
- 大节点覆盖按任务名称/标识辅助核对设计/采购/加工/装配/试模/交付等类别；工具明确提示这不能替代项目负责人按实际模具类型确认，也不默认 55 天周期、自然日/工作日、节假日或齐套率口径。
- `query_project_plan_context` 会附加 `erp_execution_progress`，区分 `NOT_CONFIGURED`、`LOGIN_REQUIRED`、`NO_MOLD_REFERENCE`、`RESOLVED` 或具体 ERP 错误；该字段只引用 ERP 原系统事实，不修改 Agent 计划、不登记开完工、不替代计划变更审批。
- `analysis.visualization` 输出可渲染的计划时间线、状态看板列和风险泳道，并把 ERP 外部进度状态以只读引用方式附加给渲染层；它不代替最终交互式甘特图规则，也不允许拖拽直接改已批准计划。
- 对话依据展示新增项目计划专用表格。当工具结果含 `analysis.tasks` 时，会先显示“项目大节点 / 计划任务表”，列出节点、状态、计划开始、计划结束和前置依赖，并显示已覆盖/缺少的大节点。
- 新增 SQLite 单元测试覆盖有效计划分析、运行中/逾期/依赖阻塞/客户交期风险、大节点缺口、计划变更权限隔离、多候选不自动决定、计划时间线/看板数据契约，以及 Mock ERP 进度只读引用。FR-033～042 仍为 NOT_VERIFIED：装配齐套率/关键件口径、异常调整执行、甘特图/看板完整交互样式和真实 ERP 环境验收尚未完成。

## 持续开发：正式开工条件核对工具（2026-09-15）

- 新增 `query_internal_start_readiness` 只读工具和“正式开工条件核对”Skill。用户按项目 ID、项目编号/名称、承接单、开工通知、合同等线索提问时，模型可核对项目状态、有效承接、有效拒单、正式开工通知、销售合同、整套委外合同和计划上下文。
- 工具明确区分承接、合同、计划和正式开工：承接不等于正式开工，销售合同存在不等于正式下达，项目计划存在不等于已经下达执行任务。合同晚到仅作为核对提醒，不自动阻塞或放行开工。
- 返回 `readiness.known_blockers`、`warnings`、`hints` 和 `can_prepare_start_from_known_facts`，只表达当前可见事实是否支持“准备开工申请”；不创建开工通知、不下达设计/采购/生产/装配/试模任务。
- 新增 SQLite 单元测试覆盖具备承接依据时可准备开工、已有正式开工时不重复准备、未分配承接查询工具时不泄露承接单号、多项目候选不自动决定。FR-020～025 仍为 NOT_VERIFIED：客户开工条件、通知矩阵、合同晚到催补、财务取得 BPM 开工通知、订单/合同核对和移模/签收维护尚未完整验收。

## 持续开发：合同上下文核对工具（2026-09-15）

- 新增 `query_contract_context` 只读工具和“合同上下文核对”Skill。用户按项目 ID、项目编号/名称、合同号或业务线索提问时，模型可定位当前授权可见项目，并汇总销售合同、整套委外合同、付款节点、合同替代关系和预计到达逾期提示。
- 工具本身只要求项目业务档案读取权限；合同明细仍分别要求 `query_sales_contract` / `query_full_outsource_contract` 及对应业务权限。没有对应合同工具时只返回项目上下文和 limitations，不通过合同上下文泄露合同号、金额或付款节点。
- 派生状态区分销售合同、整套委外合同、有效合同、替代关系和预计日期逾期；合同存在不等于承接完成、正式开工、客户验收、实际回款或供应商付款完成。
- 新增 SQLite 单元测试覆盖合同号定位、付款节点汇总、替代关系、整套委外合同权限隔离、多候选和晚到合同提示。FR-026～032、FR-055～057 仍为 NOT_VERIFIED：合同上传/OCR、正式业务审核、客户规则适配、财务实际收付款确认、合同差异联动及真实 ERP 对账尚未完成。

## 持续开发：报价与承接上下文核对工具（2026-09-15）

- 新增 `query_quote_acceptance_context` 只读工具和“报价与承接上下文核对”Skill。用户按项目 ID、项目编号/名称或合同、模具、业务单据等线索提问时，模型可先定位当前授权可见的项目，再汇总报价承接决定、拒单记录、内部正式开工和销售合同上下文。
- 项目定位同时要求 `project.read` 与 `quote_acceptance.read`；业务对象候选匹配只在用户已拥有 `query_business_object_candidates` 时参与。报价承接记录由本工具读取；正式开工和销售合同仍分别要求对应查询工具与业务权限，不能借上下文工具绕过金额、合同或开工资料限制。
- 返回口径明确区分 `RESOLVED`、`MULTIPLE_CANDIDATES`、`NOT_FOUND` 和 `NOT_FOUND_OR_FORBIDDEN`，并给出 `has_effective_acceptance`、`has_effective_rejection`、`has_formal_start`、`has_sales_contract` 等派生状态。工具只汇总事实，不创建报价、不承接、不拒单、不正式开工，也不把销售合同等同于承接。
- 新增 SQLite 单元测试覆盖有效承接/正式开工/合同摘要、多候选不自动决定、销售合同权限边界。FR-006～019 仍为 NOT_VERIFIED：完整资料接收、报价版本、承接/拒单审批、客户分类、中标接收、同一开工草稿延续和真实 ERP/业务验收尚未完成。

## 持续开发：业务对象候选匹配工具（2026-09-15）

- 新增 `query_business_object_candidates` 只读工具和“业务对象候选匹配”Skill。模型可用项目编号/名称、客户名称、模具号、合同号、采购单号或任意线索查询当前权限内候选项目，并返回命中字段、来源和 EXACT/PARTIAL 口径。
- 匹配范围要求用户同时具备项目读取和项目业务档案读取权限；合同、订单和工程联络线索只有对应查询工具和业务权限可用时才参与匹配，避免用隐藏字段反推出项目。
- 多候选返回 `MULTIPLE_CANDIDATES`，未找到返回 `NOT_FOUND`，唯一候选也只表示“当前可见资料中的候选”；工具不创建项目、不合并对象、不承接、不累计收付款，也不替代正式纠错流程。
- 新增 SQLite 单元测试覆盖同模号多项目候选、隐藏项目不可见、合同号按授权工具参与匹配。FR-004/FR-005 仍为 NOT_VERIFIED：重复邮件/合同/付款的完整去重、人工纠正历史和报价/中标接收流程尚未全部实现。

## 持续开发：按项目聚焦的供应商发货风险工具（2026-09-15）

- `analyze_delivery_risk` 不再只是无参数扫描当前可见范围；工具 schema 新增 `project_id` 和 `identifier`，用户询问某个项目时可按项目 ID、编号或名称限定分析范围。
- 项目编号/名称解析只在当前用户可见项目内进行；未找到或多项目命中时返回明确 resolution/candidates，不扩大为全量风险分析替代回答，避免把不同项目记录拼接成确定结论。
- 结果继续只基于已发布临期规则、当前授权责任域内的正式订单、已上报异常和未发货数量；不读取采购申请来臆测发货，不把局部供应商风险推断为整套模具总体延期。
- 更新“供应商发货风险分析”Skill，使模型在项目上下文明确时传入项目范围；新增测试覆盖项目聚焦 schema、同用户多项目订单过滤，以及责任域隔离。真实 ERP 发货、物流、合同/计划联动和生产权限矩阵仍待联调验收。

## 持续开发：能力按业务流组织（2026-09-15）

- 设置页和管理员授权页不再把工具与 Skill 平铺成技术 key 列表，而是按项目管理、采购、工程、财务、仓储等业务部门，以及查询、操作、审批、核对类型分组展示。
- 管理员给用户启停工具/Skill 时可按能力名称、权限、说明、部门和类型筛选；这只是组织和可发现性优化，不扩大任何数据权限，也不把部门名称当作自动授权来源。
- 新增项目业务档案权限与能力的中文名称，并保持能力分配与业务授权取交集的规则。该改动服务于智能体工作台的“业务流能力目录”，没有增加传统 ERP 菜单或业务模块页面。

## 持续开发：项目业务档案与反向定位（2026-09-15）

- 新增 `query_project_dossier` 只读会话工具和“项目业务档案核对”Skill，可按项目 ID，或项目编号/名称、模具号、工程联络、采购订单、合同及业务单据编号反查项目，并汇总当前可见的项目画像、模具、计划、联络、采购、业务单据、供应商实付和销售合同节点。
- 工具单独要求 `project.dossier.read`，同时仍要求 `project.read`；反查线索只来自用户已拥有的对应查询工具和实际可见字段。合同号等字段被字段授权裁剪时不能用于隐形命中；多项目命中返回候选并要求用户指定项目 ID。
- 财务汇总只按当前可见付款确认与冲正记录计算供应商实付；客户付款节点仅展示合同条件，不宣称已回款。风险信号只表达当前已见事实，不把局部计划或供应商异常推断为整套模具整体延期。
- 没有新增传统 ERP 菜单或复制 ERP 台账。ERP 采购、制造、财务等原生事实仍按后续适配引用；未分配工具、结果截断或 ERP 未联调均写入 limitations/coverage。
- 新增 4 项 SQLite 权限与汇总单元测试，覆盖同号歧义、无权项目不可见、裁剪字段不能反查、实付汇总口径。完整 pytest 使用仓库内 basetemp 复跑为 123 passed、103 skipped；首次默认 Windows Temp 目录权限导致 2 个文件存储测试 setup 报错，已确认不是业务代码失败。
- 内置浏览器使用 Qwen3-30B-A3B-Instruct 对 `SMOKE-M001` 本地合成项目提问，Run 成功完成且仅调用 `query_project_dossier`；页面显示项目状态、结构设计阶段、1项未关闭工程联络单、无采购订单和客户承诺交期，控制台 error/warn 为空。FR-110～112 仍为 NOT_VERIFIED：真实 ERP 原生引用、专用 PostgreSQL 和生产权限矩阵尚未完成验收。

## 持续开发：工程联络单结构化影响与执行反馈（2026-09-15）

- 联络单主表补齐客户编号/名称、模具号、产品或料品、申请日期、问题来源、当前环节、变更类别和紧急程度；线上新建要求完整填写，历史旧数据保持可读，不伪造缺失字段。
- 责任事项增加结构化影响对象与原生编号、影响说明、继续/暂停/取消/返工/重新下达动作、预计交期天数和预计金额。引用 ERP 图纸、物料、采购、在制及供应商任务时必须保存 ERP 原生引用和事实截至时间，Agent 不复制同义台账。
- 办理反馈记录不可覆盖的实际完成时间、工时、实际金额、执行依据及来源；方案 BPM 材料冻结联络主数据、结构化影响项和附件版本。方案生效后生成幂等交接记录并通知当前责任人/部门负责人，但不会把 ERP 对象直接改成已执行。
- 查询/准备工具、Skill、右侧只读材料区和浏览器合成场景已同步；没有增加常驻业务菜单或传统 ERP 表单。新增迁移 `a8c4e1d92f70` 和4项结构化影响规则测试。本轮完整测试为119 passed、103项专用 PostgreSQL 集成测试按保护规则跳过，Vue 类型检查/生产构建、Python 编译和新迁移离线 PostgreSQL DDL 均通过。
- 30B 内置浏览器联调成功返回合成联络单的客户、模具、当前环节、图纸影响、返工动作、顺延2天与3500 CNY。联调同时修复 SQLite 恢复时区兼容及模型取得证据后仍扩展无关工具导致预算耗尽的问题；运行时现在保留最后收口轮并在证据/上下文达到保护阈值后禁用后续工具，只允许依据已取得证据输出。
- FR-082～090仍为 NOT_VERIFIED：专用 PostgreSQL 集成测试、真实 ERP 受影响对象执行回执、设计/采购/生产等完整通知矩阵、财务计价/合同/物流联动和生产附件安全验收尚未完成，不能把结构化引用或方案获批称作正式业务执行完成。

## 持续开发：项目终止、结算与正常关闭（2026-09-15）

- 新增终止与正常关闭两类独立清单，覆盖终止依据、当前环节、已完成工作、已发生费用、未完成采购/在制品/供应商任务处置、客户及供应商结算、交付验收适用性、异常事项和全业务资料归档。关闭不能由生产完工、发货、签收或单次回款任一事实单独触发。
- 新增 `query_project_closure_context` 及六项清单准备/核对工具和“项目终止与关闭”Skill。会话先展示证据与阻断项；终止和最终关闭须本人确认后提交 Agent BPM，清单核对则直接记录人员、来源、依据、时间及版本。ERP 已有采购、制造和财务事实只通过原生引用接入，不在 Agent 重建同义台账。
- 终止审批生效后仅停止 Agent 本地未完成计划任务，项目转为终止待结算并建立终止清单；不会把 ERP 任务擅自改为完成。终止清单允许交付、客户验收等不适用项填写原因；正常关闭清单不允许借此绕过应完成条件。
- 清单更新均追加修订历史，最终关闭前重新检查实时计划、联络异常、付款占用和 Agent 本地未结采购，避免使用过期核对结果。正常关闭与终止已结算分别写入 `CLOSED_NORMAL`、`CLOSED_TERMINATION`，并保留关闭人、时间和全部检查明细。
- 增加迁移 `f1a4d8c7e2b3`、5项终止/关闭规则测试及浏览器合成终止场景。全部可运行的非 PostgreSQL 测试为114 passed，103项专用 PostgreSQL 集成测试按保护规则跳过；Vue 类型检查与生产构建通过。内置浏览器已验证终止建议、本人确认、Agent BPM 同意及生效回执，页面显示30B模型且控制台无错误/警告。
- 左下角账号菜单的设置新增浅色/深色外观切换，本机持久化并在页面初始化时应用。内置浏览器验证双向切换、刷新保持和浅色主工作台显示。FR-094～098仍为 NOT_VERIFIED：专用 PostgreSQL、真实 ERP 事实适配、生产附件与真实财务/客户结算尚未完成联调验收。

## 恢复持续开发：项目暂停与恢复业务流（2026-09-15）

- 按最新要求恢复持续开发。本轮没有新增常驻菜单或传统 ERP 模块页；新增 `query_project_control_context`、`prepare_project_pause`、`prepare_project_resume` 三个会话工具和“项目暂停与恢复”Skill。工具先展示项目版本、有效计划、未完成任务、当前暂停区间、客户承诺交期及可选 Agent BPM，浏览器本人二次确认后才创建申请并提交审批。
- 项目整体暂停在草稿时冻结有效计划及全部未完成任务，审批生效前再次核对项目、计划和任务状态；发生变化即阻断旧申请。暂停生效后沿用既有业务门禁阻止普通下单、计划执行、发料、装配和试模，资料补录、沟通、工程联络、合同与结算核对按权限保留。
- 恢复必须引用当前有效暂停区间，按恢复日减暂停日计算实际暂停天数，只顺延暂停时冻结且仍未完成的节点；已完成节点不变。每个节点保存计划日期前后值，暂停区间、恢复单据和节点顺延有唯一约束，重复恢复不能再次累计。
- 客户承诺交期仅在暂停时留快照，恢复不会自动修改；需要调整时仍须独立客户确认流程。ERP `project_service` 的项目状态与通知仅作后续适配边界，本轮未修改或复制 ERP 数据。
- 新增迁移 `d33a12f7b9e1` 和 3 项暂停恢复规则测试。交接环境恢复锁定依赖后，全部可运行的非 PostgreSQL 测试为 109 passed；103 项依赖专用 `agent_test` 的集成测试按保护规则跳过。新迁移从上一版本离线生成 PostgreSQL DDL 成功，Vue 类型检查及生产构建通过。尚未在专用 PostgreSQL、真实浏览器、真实模型或 ERP 上联调，因此 FR-091～093 仍标记 NOT_VERIFIED，不能作为业务验收完成。

## 本轮收尾与暂停（2026-09-15）

用户要求：完成本轮工程联络业务后打包项目，后续暂停开发。OCR 暂不开发。全项目尚未完成，不能把压缩包称作生产交付或全部需求验收。

- 已接通发起/历史补录、部门分派、本人反馈、处理方案通用 BPM、退回整改、再次反馈、独立复验和人工关闭。新增五项生命周期工具，与附件关联合计十一项 prepare_contact_* 建议工具。处理人不能验收自己的结果；关闭人必须为发起人或管理员指定验收负责人且当前具备相应权限。
- 方案冻结责任事项与附件版本。材料变更阻止继续同意及关闭，须重新审批；所有有效事项在最新方案下复验合格才能关闭。历史记录只追加，关闭后不能继续修改。联络协作关闭不自动关闭 ERP 异常、订单、项目或旧工程变更对象。
- 同一会话续问补充最近四轮本人请求、最多6000字符，帮助理解“上一份方案”等指代；不复用历史工具结果或审批事实，不跨用户/会话。完整长期上下文治理仍待完成。模型方案时态需保留用户原意，最终仍以人员核对为准。
- 附件原件上传、权限下载、图片预览、关联确认、版本保留已实现。格式检查不等于内容识别或杀毒；私有 S3 使用版本标识，但生产对象存储未联调。当前开发业务库无真实附件原件，隔离测试库已验证上传及版本链。
- 完整后端209项测试通过，包含8项联络生命周期及10项文件测试；另有3条既有依赖弃用提示。最终 Vue 类型检查和构建通过。开发/测试库均升级至 c825ef319d76，并应用运行账号最小表权限。
- 真实内网 Qwen 任务 8db2a150-c78d-40c9-bbe6-49d0c671fb39 成功理解续问指代、重新查询正确联络单并生成保持原文的方案建议。浏览器核对卡确认后创建本地合成审批；消息通知出现待审批项，人工二次确认后实例 COMPLETED、方案 EFFECTIVE。案例尚未复验或关闭，不能把它称作已关闭。完整关闭与退回链由隔离测试验证。
- 保留两次诊断历史：一份建议误改措施时态，未提交；一次续问缺少前文导致选错对象，被业务校验阻断，无方案写入。修复后重测通过，不删除失败记录或冒称始终成功。
- 没有新增右侧常驻业务菜单。管理、工具技能及流程配置在左下角账号→设置；待审批在消息通知；右侧只显示当前材料和审批节点。

仍待：自由协作可视化画布、完整成本/计划影响与 ERP 执行回执关联、附件安全扫描与生产存储验收、历史补录最终业务状态认定，以及 V1.1/V3.6 覆盖表中的其他未完成项。OCR 暂缓，ERP 真实联调等待用户启动原系统；Redis/Docker 生产部署尚未交付。冻结包附源码、锁定依赖、迁移、测试、需求技术原文和开发数据库备份；不包含密钥、环境密码或运行缓存。

## 先前检查点：会话操作建议与本人确认（2026-09-15）

- 新增 query_contact_context 及五项 prepare_contact_* 工具：发起、补充记录、组织部门事项、分派处理人、提交本人反馈。由模型根据完整语义自主选择当前授权工具，不使用关键词分流。建议持久化为所属 Run 的 Step 证据，生成时不创建/派发/提交业务。
- 用户在会话卡片核对具体项目、责任域、模式、内容及人员，点击后获取仅限本人会话的短时确认凭证，再确认执行。模型和 Worker 无用户会话，不可取得或使用该凭证。后端重新检查任务所属人、授权指纹、能力、版本、部门成员及当前办理资格；取消任务、撤权或资料变化均阻断旧建议。
- 联络服务移除内部提交事务；人工确认统一提交业务变化、确认回执、审计与 outbox。同一确认重复点击返回回执；保存确认审计失败时业务也回滚。中文责任域名称统一为实际权限标识，避免“五金”与 hardware 形成不同权限范围。
- 前端仅新增会话核对卡和必要确认弹窗，右侧继续仅查看当前单据材料。确认成功显示实际回执，刷新后可恢复已确认状态；原模型回复作为当时历史保留，不改写为业务事实。
- 实际内网 Qwen 任务 47dd4e69-362c-4ba5-8100-2ec45bbd069b 成功，顺序调用 query_projects、prepare_contact_create。确认前合成联络单数量为0；人工暂不执行、重新核对后确认，数量为1，并有1份确认回执及1条对应确认审计。未操作 ERP 或生成正式审批。首次因工作进程内网访问权限而网络失败，调整后验证成功。
- 最终完整后端189项测试通过（新增7项操作建议/确认测试）；Vue类型检查和生产构建通过。浏览器 http://127.0.0.1:5173/、约1320×900桌面视口验证真实模型建议、暂不执行、人工确认、材料打开和刷新恢复回执，最终页面非空、无框架错误覆盖层、控制台错误/警告为空。未覆盖移动端；真实Redis投递和ERP联调仍未验证。
- 仍缺附件上传/预览/OCR、纸质步骤核对、正式方案审批关联、整改复验/关闭，以及其他业务完整会话接入；不据此认定整个工程联络闭环或项目已交付。

## 先前检查点：联络协作与会话优先的 MCP（2026-09-15）

- 新增独立联络协作模型、迁移与接口，覆盖人工创建、历史补录、发起人组织部门事项、负责人/有权限发起人分派、本人反馈、不可覆盖的过程时间线及权限检查；补录不派单，反馈不产生正式审批或关闭。
- 新增联络查询工具和 Skill，并将 Harness 的工具发现及调用接入私有、绑定任务租约的 MCP JSON HTTP 接口；MCP 和旧内部工具路由共用执行服务及 Step 回执，没有复制业务逻辑或把模型身份升级为管理员。详见 MCP_BUSINESS_CAPABILITIES.md。
- 用户纠正页面优先方向后，已撤下采购、仓储、项目、预警、联络等常驻业务导航，保留审批和必要管理配置入口。联络材料从会话证据或通知按需打开。工具、Skill 按部门及业务流组织的完整管理目录尚待实现，不能只移除菜单就视为 Agent 化完成。
- 最新完整后端182项测试通过（包含10项联络测试及4项MCP测试），前端类型检查/构建通过。实际浏览器验证创建、分派、反馈、线下记录，以及精简后的菜单。热更新时临时引用错误已修复，最终刷新观察区间无控制台错误或警告。真实内网模型任务 e3eec3b8-43dd-4ed3-96a9-fec13a0b4409 成功，经 MCP 调用 query_contact_cases 返回业务证据，无写操作。
- 尚缺手写导入/OCR、完整可配置能力分类、会话业务写入与人工确认工具、正式方案审批衔接、任务恢复与最终关闭；整个业务闭环与项目交付仍未完成。旧 ERP 未修改、未联调。
- 用户指出联络菜单移除后仍打开了整套操作表单，已将 ContactPanel 收为当前单据的只读材料区，移除发起、分派、反馈表单及全量列表。最终 Vue 构建通过；浏览器验证从会话证据打开指定单据，显示责任部门、人员、反馈和实际/录入时间，发起与派发按钮数量均为0。附件与完整流程图尚待实现，当前只展示已存在的协作事实。

## 需求澄清记录：工程联络单灵活协作（2026-09-15）

- 工程联络单不强制由管理员预配置固定流程。支持手写材料识别核对，以及发起人在线创建、选择责任部门、线下讨论线上记录。
- 上传人选择历史补录或线上续办；历史补录不自动重新派单或执行业务动作。部门负责人分派办理人，发起人有权限时可直接指定。协作、代录意见、正式审批与最终关闭分别留痕，强制人工事项不因上传纸质签名或线下讨论而自动通过。
- 已同步 PRODUCT_CONTRACT.md、BPM_AND_EXCEPTION_CONTRACT.md，详细约束见 ENGINEERING_CONTACT_COLLABORATION.md。本轮仅修改开发依据；现有代码仍有先审批生效的固定路径，双模式导入、灵活协作任务及相应权限尚未实现，不能据此宣称已交付。

## 先前检查点：资料模板登记、版本与流程绑定

- 已新增资料模板 API 和 Vue 管理页面，维护表头、多张明细表、字段类型、单位及金额关联币种；草稿可修改，发布后只能复制新版本。审批模板选择已发布资料版本并保存字段契约，资料新版本不会覆盖旧流程绑定。
- 迁移 94d51bc730ef 已应用开发库与隔离测试库。资料模板已发布版本新增数据库更新/删除保护；权限、并发、版本和绑定一致性有后端测试。最新完整后端168项测试通过，Vue类型检查及构建通过。
- 实际浏览器验证合成设计清单字段配置、发布第一版、复制第二版草稿，审批模板选择器只显示已发布第一版，观察区间无错误或警告。仅维护合成配置，没有发起正式审批或执行 ERP 操作。
- Excel 列映射/解析、材料存储与人工核对、业务材料绑定、字段权限、动态条件编辑器和动态模拟界面仍待完成。当前资料结构绑定不代表上传资料已确认，正式发起继续拦截未绑定已核对资料的流程。

## 先前检查点：资料字段条件解释器

- 已开发基于资料字段契约的动态条件后端，覆盖指定行、任意行、全部行、过滤及四类汇总，支持文本、数值、日期、布尔与币种明确的金额；同一行复合判断不跨行拼接，缺数据或混合币种不会自动放行。
- 已接入 BPM 分支、必须驳回与模拟，共用解释器，模拟提供实际值和行/字段依据。旧模板不改变原规则含义。具体契约、限制和剩余功能见 BPM_MATERIAL_TEMPLATES.md。
- 最新后端完整164项测试通过，前端类型检查/构建通过。本轮没有新的渲染界面功能或浏览器验收；资料模板 UI、Excel 导入、人工确认与持久化绑定仍未实现。正式提交拒绝未绑定资料的动态模板，不能把模拟值当正式材料。
- API 重启后，实际已登录 HTTP 请求验证：跨行分别命中不走增补节点、同一行复合命中进入增补节点、缺资料返回 ROUTE_DATA_MISSING；三次均返回条件依据。仅使用合成输入模拟，没有创建正式审批或执行 ERP 操作。

## 最新：自由类别与动态人员配置

- 用户纠正了模板分类方式：不使用预设业务/采购类别限制新流程。已新增管理员自由创建、改名和停用的流程类别，界面按类别维护模板；新配置不再要求选择业务枚举、六种采购类别或新模/改模类型。发起人按类别 → 模板 → 已发布版本选择，允许显式选择历史已发布版本，草稿不可用。该规则覆盖下文历史“仅最新版本可选择”的描述。
- 已发布旧模板的 config/BPMN/hash 和已有实例保持不变，迁移只将其分类为“待整理”。旧模板原有约束仍由后端检查；另存或修改草稿时页面明确提示转为通用模板配置。新模板不依靠分类名称决定业务接口，实际单据类型仍校验权限、资料和后续业务动作。
- 管理员可维护稳定 ID 的角色、部门、成员和部门负责人；节点可选择指定用户、角色、部门或两者交集。同类多组选并集，角色与部门同时选为交集。组织成员变更记录原因、版本、审计和用户 security_version，不创建业务权限 Grant。
- 节点进入时解析人员，记录规则、组版本、候选人、合格人员和时间。当前席位不因角色成员增加或移除而改票，下一节点解析新成员；办理时继续检查实际业务权限。无人/规则失效/会签人员缺权限或完整材料读取权限时阻塞，不跳过。
- 迁移 71ab32091cde、83c405a62edf 已应用开发库及隔离测试库，运行账号表权限已更新，API 已重新启动。最新全后端 133 项测试通过，Vue 类型检查和构建通过。实际浏览器验证角色创建、自由创建加工类别、角色选人、保存并发布模板，观察区间无新增控制台错误或警告。
- 固定设计清单、模具核算清单可作为资料模板的方案已获用户确认。字段映射、Excel 上传、按行/汇总条件、附件内容核对、动态模拟等尚待实现，完整约束见 BPM_MATERIAL_TEMPLATES.md。当前 RuleEditor 与模拟仍是少量固定字段，不能将其视为完整条件配置。
- 项目角色、基于实际项目/资料的候选人预览、认领、指定退回、无人处理恢复和完整图形设计器仍待补齐；受控转交、显式前/后加签和受限的一跳人工审批代理已完成，但不等于自由认领或通用恢复机制。整个 BPM 与完整项目均未达到交付验收。

## 流程版本、适用范围与中文界面更新

- 已补流程详情、按流程查看版本历史、分页读取历史、当前草稿修改及修改后另存新版。草稿修改带内容哈希并发校验；已发布版本接口禁止覆盖。发布第二版后，旧实例仍绑定第一版，新申请只选择该流程的最新已发布版。保存草稿即分配版本号，发布改变该版本状态，不再次增加版本号。
- 模板支持采购类别、新模/改模适用范围。提交前按实际业务材料列出适用流程，后台在创建确认请求与实际确认时再次校验。已增加设计类型迁移，旧设计记录不猜测新模或改模类型。
- 流程卡片不展示内部流程编码，节点标识自动管理；条件详情以中文展示判断字段、比较方式、目标节点、默认出口和必须驳回原因。人员权限、工具技能、授权范围、状态及业务字段使用中文说明；业务原始编号和用户输入保留原值。
- 本次“查看流程”报错定位为运行中的后端未加载新增路由，返回 404；重新启动 API 后详情返回 200，页面成功显示。不是权限不足，也没有修改 ERP。
- 最新完整后端测试 99 项通过（隔离 PostgreSQL），覆盖原实例继续使用旧版、草稿并发冲突、已发布禁止覆盖、权限拦截、适用类别与最新版选择；前端类型检查和构建通过。真实 ERP 联调仍按用户安排延期。
- 实际浏览器已验证：查看中文流程详情、从第一版保存第二版草稿、修改第二版草稿仍为第二版、发布第二版并在历史中保留第一版。独立测试页无新增控制台警告或错误。用户补充：模型名应显示实际配置原名（如 Qwen3-30B-A3B-Instruct），不强制中文化；页面已恢复原名并验证。

## 最新范围核对与纠偏

完整范围再次明确：报价、中标、合同上传、项目大节点维护均在 Agent 开发；此前的简表只是部分示例。FR-001～118 的原文与逐条开发/验收状态见 REQUIREMENTS_TRACEABILITY.md，不能用 ERP 存在基础表或 CRUD 接口抵消这些业务需求。未验收条目持续保留，不只开发已有演示页面能覆盖的模块。

依据现已包含需求 V1.1 和技术 V3.6。用户明确允许现有 ERP 接口复用，正式操作必须人工确认；原材/五金/委外采购不重复开发，Agent 采购新增范围为辅材/办公用品/试模料。完整代码证据及模块对照见 ERP_SCOPE_AUDIT.md；逐项开发前先查已有实现。

审批最新约束：只借鉴 ERP 部分业务审批规则，不调用 ERP 审批流、待办、发起/决定接口。Agent 自建完整可配置 BPM，覆盖新增及接入业务；ERP 仅复用业务查询与执行。已有顺序 ALL/ANY 实现还不是完整 BPM，人员规则、条件路由、设计器和恢复等必须继续完成。

最新补充：异常处理与工程联络单在 Agent 新开发，不复用 ERP 异常流程。工程联络单承载异常解决闭环；管理员通过通用 BPM 模板配置新模/改模设计上传、采购价格、工程联络单方案审批等流程。详细实现与验收约束见 BPM_AND_EXCEPTION_CONTRACT.md；该约束是开发依据，不表示设计器或异常闭环已经完成。

业务动作补充：采购下单、拆单复用 ERP，拆单具体接口尚待核验，不标记已接入。发货车辆、物流信息维护明确为 Agent 新开发；辅材、办公用品、试模料新增采购范围继续保留。BPM 编排不改变已有业务功能归属，不重复建立订单或发货事实。

已继续核对钢料拆单/整单不拆源码；用户确认保留 ERP 两种路径的内置业务路由，不要求重写或拆除。静态范围核对已覆盖 118 条 FR，对应 627 条相关接口候选、55 个源文件哈希；见 ERP_REQUIREMENT_REVIEW.md、ERP_ACTION_REVIEW.md。候选映射不等于联调通过。合同 OCR 后先保存草稿、上传人核对再发起 BPM 已确认。

通用 BPM 后端已新增条件分支、默认出口、前向路径校验与模拟；审批席位随实际引擎路径推进，缺字段/多分支命中阻塞，ALL 会签不静默剔除失效人员。相关测试与原核心测试共 48 项通过（真实隔离 PostgreSQL 与 Spiff 引擎）；尚未完成分支设计器界面和完整 BPM 验收。新 ERP 身份/操作台账迁移已应用于本地开发与测试库，不代表接口已接入。

后续验证更新：BPM 配置界面已接入分支目标、默认出口、组合规则及保存前模拟，并在实际浏览器完成创建、两条路径模拟、资料缺失阻塞、保存发布及复制已发布模板。修复了 Vue 响应式对象不能直接 structuredClone 导致的复制失败，以及右侧模块导航溢出；390×844 窄屏通过，默认视图已恢复。修复后观察区间无新增浏览器 warning/error，前端类型检查与生产构建通过。完整后端曾 89 项通过，之后新增类型校验用例及参数防护，最新 BPM/核心回归 55 项通过；不能将这些测试作为 ERP 或全业务验收。

当前 BPM 仍有明确缺口：项目角色和基于实际资料的候选人预览、完整业务字段目录、认领、指定退回节点、超时与事件恢复、独立流程服务部署以及完整图形设计器。受控席位转交、显式人员池内的前/后加签和按节点/决定/有效期约束的一跳人工审批代理已经完成；当前已发布模板冻结版本、动态角色/部门解析、指定用户 ALL/ANY、受限字段的前向条件分支和模拟可验证，仍不代表通用 BPM 全部交付。

用户安排：先开发，真实 ERP 联调待用户启动 ERP 后共同进行。本地 API、合成业务、模拟传输测试继续；不提前要求 ERP 启动，不将待联调接口计为完成。

本轮新增的本地采购、制造、财务模型及测试属于合成场景试验，部分与 ERP 重复，正在按归属整理，不能视为最终交付能力。七项领域测试及十项制造/消息测试曾通过；这些测试证明本地代码的特定行为，不证明 ERP 已联调或完整业务已验收。最新 ERPIdentity/ERPOperation 模型和 erp_adapter.py 尚未完成迁移/接入，后续完整测试须先处理这段未完工作。

Redis 消息 Worker 已有投递租约、失败重试、Inbox 去重、权限核对及丢失后补投代码，并用真实隔离 PostgreSQL 配合模拟传输验证。尚未进行真实 Redis 运行验证。新业务面板曾通过 Vue 构建，但最近的制造页面变更尚未做完整浏览器回归。

下方表格是上一稳定检查点的历史覆盖情况，仍需按本轮审查更新；不得据其“尚未实现”或旧测试数量忽略当前文件，也不得把其“完成”当作全部项目完成。

## 本次完成

- Python 模型 HTTPS 接入：定位默认 TLS 握手超时；可配置 X25519，保持 TLS 1.3 与证书/主机名验证。
- 真实 Qwen 调用通过工作台发起，模型执行新工具，展示查询来源、时间和证据。管理员可查询两条合成项目记录。
- 浏览器验证管理员分配项目查询工具后，五金采购模拟账号只返回 TEST-M001，一条记录，与右侧项目面板一致；浏览器控制台无错误/警告。1280×720 与 390×844 窄屏回归通过，采购草稿在收起及切换模块后保留。
- Harness 在调用前持久化工具提议；响应丢失后恢复同一工具步骤，数据库幂等回执避免重复执行；跨重启保留模型次数和任务截止时间。
- 工具执行、恢复、结果返回、历史展示重新校验身份、能力和时间敏感授权。授权到期或撤权后阻止旧结果继续使用。
- 管理员可按用户启用/停用已登记工具和 Skills；与业务授权取交集，变更留审计并更新权限版本。依赖工具不可用时 Skill 不生效。
- 前端展示实际配置模型及执行进度；登出和权限变化清理旧资料；采购编辑面板收起/切换后保留内存草稿。

## 与完整需求的差距

| 文档领域 | 当前实现 | 仍需完成 |
|---|---|---|
| 架构、数据归属 | 独立 Vue3/FastAPI/agent_db；Worker 无业务库连接 | 正式服务部署隔离、凭据分发与生产运行检查 |
| 工作台 | 统一会话、历史、工具证据、右侧收展/全屏，已实现业务入口 | 多任务标签、完整附件预览、全部业务页面、全局表单离开保护 |
| Agent/Harness | 真实模型工具循环、次数/时间限制、租约、取消、步骤回执、检查点恢复 | 人工等待恢复协议、完整任务事件流、全部故障注入和运营观测 |
| LLM/上下文 | 单任务消息链、受限工具与技能上下文、证据编号校验、授权变化失效 | 连续追问对象绑定、结构化摘要、压缩治理、数值和业务结论验证。编号正确不能证明自然语言结论正确 |
| Tools | 两个只读新工具：项目查询、采购申请查询 | 全部受控业务工具、参数/字段契约、操作预演和业务回执查询；不允许模型直写库 |
| Skills | 新采购申请核对 Skill、按用户分配、依赖工具约束 | 完整技能包发布/版本治理与技术文档规定的其他 Skills |
| 用户权限 | 登录、CSRF、稳定用户 ID、完整范围授权组、DENY、时效、字段过滤、撤权及工具/Skill 分配 | 组织/角色模板、字段授权编辑器、授权模拟器、账号停用/密码与 MFA 治理、授权管理员权限上限 |
| 临时 SQL | 窄范围 AST 防护验证 | 查询执行器、只读数据库凭据、数据集目录、行/列隔离、完整越权测试。尚未开放模型 SQL 执行工具 |
| 被动预警 | 已有按需 `analyze_delivery_risk` 工具，可按项目聚焦正式订单异常/临期未发货并保留口径 | 真实 ERP 发货/物流/合同/计划联动、生产权限矩阵和浏览器模型联调仍待验收；不依据采购申请臆测供应商延期 |
| BPM | SpiffWorkflow 顺序节点、ALL/ANY、指定审批人、版本冻结、强制驳回、无审批人阻塞、受控转交、前/后加签、受限人工代理 | 完整设计器/条件路由、认领、指定退回、超时恢复及全部节点可用性验证 |
| 表单/审批材料 | 采购明细、提交人、备注、冻结快照、当前节点、审批历史、人工确认 | 附件上传/版本/权限、防病毒与对象存储；通用表单设计与全部资料类型 |
| 模板库 | 一套合成采购申请模板 | 文档规定的全部模具业务审批模板、对应领域动作和验收用例 |
| 全流程模具业务 | 采购申请草稿、提交、两级人工审批可验证 | 其余业务闭环；审批通过不代表正式下单、发货、实物收货、付款或全部流程完成 |
| Redis/消息 | 有数据库 outbox/inbox/notification 结构与部分事件写入 | Redis Streams 投递/消费/重试/死信、去重、通知生成、故障恢复。当前 Worker 通过数据库任务轮询领取 |
| ERP 关联 | 提供的源码、结构文件仅关联参考，未改旧系统 | 授权只读连接与按数据归属查询路由；不复制实时台账、不恢复旧 Tool/Skill |
| 审计/事务 | 本地业务短事务、人工回执、防重复、审计与 outbox 一起提交 | 全动作覆盖、长期归档、数据库约束加固、并发与故障验证矩阵 |
| Docker/运维 | 本地开发进程与专用 PostgreSQL 实际运行 | Docker 镜像/Compose、Redis 实例、备份恢复、生产 HTTPS、发布回滚与资源限制；本机未发现 Docker 命令 |
| 验证/交付 | 当前 56 项自动化测试通过；Vue 类型检查与构建通过；真实浏览器模型查询通过 | 完整 V3.6 验收矩阵、所有模板与风险动作、负载测试及生产部署验收 |

## 下一步顺序

按用户最新顺序，先核对全量需求与 ERP 既有功能（静态范围核对已登记），随后继续通用 BPM 的人员配置、字段条件、流程编辑及完整办理能力。报价、中标、合同上传/OCR、项目大节点、工程联络单等全部业务保持开发范围；ERP 既有具体动作按契约开发适配器并模拟验证，真实联调后续统一安排。普通审批代办按策略与委托单独实现，强制人工动作持续保护，不把开发优先级变成功能阶段或范围删减。

## 本次网络诊断依据

本机 Python 为 OpenSSL 3.5.8。Clash Party/mihomo 正在运行，TUN 启用、MTU 1500、DNS 为 fake-ip，模型域名解析为 198.18.0.172。

| 路径/握手 | 实测 |
|---|---|
| 无显式代理（仍经过本机 TUN）+ 默认 TLS | TLS 握手超时 |
| 显式 HTTP 代理 127.0.0.1:7890 + 默认 TLS | TLS 握手超时 |
| 无显式代理 + TLS 1.2 | HTTP 200，约 1.1 秒 |
| 显式代理 + TLS 1.2 | HTTP 200，约 2.3 秒 |
| 无显式代理 + TLS 1.3/X25519 | HTTP 200，协商 TLSv1.3，约 0.8 秒 |
| 修复后项目 Python 模型请求 | 成功，约 1.9 秒 |

以上足以定位当前路径对默认握手的兼容问题，不能单凭这些测试确定是本机代理、上游节点、中间网络或服务器中的哪一处。未关闭 TUN、未改全局代理规则、未绕过证书校验。

OpenSSL 官方说明 3.5 默认发送 X25519MLKEM768 和 X25519 两种 key share，作为分析握手差异的依据：[OpenSSL TLS 1.3 文档](https://github.com/openssl/openssl/wiki/TLS1.3)。此依据不等同于证明当前网络中的具体故障设备。

## 通用 Agent Core / 可替换业务包拆分（2026-09-17）

- 新建 `backend/agent_core`，物理承载模型适配器、上下文压缩、通用多轮 Harness 和领域包加载/工具门面；核心 Python 源码不再包含工程联络、项目暂停等模具业务规则。
- 新建 `backend/domain_packs/mold`，集中模具系统策略、25 个 Skill、73 个工具注册与执行分发、九类确认动作处理器及现有 ERP HTTP 适配器。业务包由 `domain_packs/active.py` 或进程变量 `AGENT_BUSINESS_PACK` 选择。
- 新增业务中立的 `/api/proposals/{step_id}` 与 `/intent` 接口；前端确认卡不再按 `proposal.kind` 硬编码九套路由和审批类型，审批展示读取服务端确认策略。原领域端点暂时保留兼容。
- `business.create_intent/confirm_intent` 不再硬编码九类 action 分支，改由当前业务包的 proposal handler registry 校验和执行。新增契约测试保证每个 `prepare_*` 工具都有确认处理器。
- 全量后端 385 项通过，Vue 类型检查和生产构建通过。内置浏览器以 `admin / admin123` 登录后验证历史对话、一般对话、两轮工具调用、供应商进度确认卡和通用意图接口；打开确认弹窗后选择“暂不执行”，未落业务数据，浏览器无新增错误或警告。
