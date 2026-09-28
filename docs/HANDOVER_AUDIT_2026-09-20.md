# 接手核查与浏览器回归

本次继续开发使用桌面上的需求 V1.1 与技术 V3.6。两份原件 SHA-256 分别为 `c5cb24ea0d0d1245b1dd664d7c65b3384dae34eca44f7fd0c8dbdf63bde7b558`、`d6e3795b15a3aff11acdc355aa4d66f3833b360260b0b365349c264bd1f6b164`，与仓库原文提取记录一致。本文记录本次接手的结论，不将本地合成数据或代码覆盖视为全项目验收。

## 当前交付缺口

| 范围 | 已有基础 | 仍需完成或证明 |
| --- | --- | --- |
| 报价、中标、开工、合同、计划 | 已有独立工具、确认建议、BPM、版本及启动协调器 | 真实样本、审批角色配置、完整场景联验；须核实历史开发记录中的过时缺口 |
| 设计、采购、制造、装配、试模 | 已有 ERP 适配、只读上下文和执行协调器 | 真实 ERP 接口及来源核验；设计写工具因缺少受控确认链仍被禁用，不能算交付可用 |
| 委外、交付、客户验收、收尾 | 已有供应商上报/材料交接、签收、复验、财务核对和收尾工具 | 不同阶段事实一致性、真实合同/签字/执行回执及结算验收 |
| 工程联络与设变 | 已有方案审批、责任事项、反馈、复验、关闭以及计划影响候选 | 联络影响到计划、成本、合同与 ERP 执行动作的全过程验收；方案获批不等于执行完成 |
| Harness 与连续对话 | 已有持久化运行、证据、权限失效、确认恢复及预算 | 本次修正目录路由和错误反馈；复合任务的语义覆盖、长上下文、对象引用连续性仍须继续验证 |
| 需求与交付资料 | 已保存 FR-001～118、AT-01～18、AD-01～13 | 覆盖表 FR-001～003 错挂暂停恢复证据，需逐项校正；V3.6 实际包含 TC-01～125，不能用历史目标中 TC-01～36 的表述缩减范围 |
| 部署及业务验收 | 已有迁移、备份恢复和运行就绪检查脚本 | 目标环境部署/恢复演练、容量与日志策略、附件生产存储、真实 ERP 联调和业务负责人验收 |

ERP 保持 `D:\ERP-system\management-system` 中的权威执行能力。本轮未修改 ERP 源码、未新增菜单或业务模块页面。OCR、客户平台自动接入等继续遵循 `PRODUCT_CONTRACT.md` 中已有决定。

## 2026-09-21 旧部署历史 revision 兼容桥

- 只读检查确认当前主库 `alembic_mold_version=mb0d0e000013`，仓库原迁移图没有该 revision；主库同时存在该历史迁移产生的 document/contract intake 扩展表。没有在主库执行升级、stamp 或删除对象。
- 新增 `mb0d0e000013_legacy_schema_bridge.py`，作为零 DDL 兼容标记；`mc0d0e000013_customer_identifiers.py` 改为从该标记继续。这样旧库不需要伪造版本戳，空库也能从 `mb012` 经过桥接节点继续升级。
- 隔离 schema 回归先安装 legacy schema 和当前域表到 `mb012`，再 stamp 模拟旧部署的 `mb013`，执行完整 `upgrade head`；历史项目哨兵的 code/status/row_version 保持不变，客户模型/物料字段完成迁移，最终 `mi0d0e000019`。该场景新增测试通过。主库随后只读执行 `current/heads/check`：revision 已可解析，但 `check` 如实返回 `Target database is not up to date`，表明剩余为实际待升级结构差异；完整生产迁移前仍需备份、停机窗口和部署方确认。

## 本次修复

1. 对照 `D:\pi-desktop\packages\agent-runtime\src\runtime.ts` 的授权目录与 ToolSearch 模式，移除 Harness 的业务词表、寒暄词表、附件关键词分流、自动代发查询和按关键词提前收口。目录始终可发现当前授权能力，模型自行选择；工具执行端仍重新校验权限。
2. `tool_discovery.py` 分离目录发现与业务执行。准确工具名不被项目号模式、部门词或当前输入改写；能力搜索只作用于模型发出的检索词。按需加载 schema，返回授权工具名及对应 Skill 说明，不执行搜索到的业务操作。
3. 未激活或不存在的工具调用以完整 assistant/tool 错误对记录，标记 `executed=false`。模型须重新通过 ToolSearch 选择工具，系统不自动改名或代为执行；重复错误仍受预算及有限协议修正约束。
4. 移除无业务证据时拼接固定业务答复的兜底；确认建议必须有真实 proposal 证据，未确认建议不能返回已执行业务状态。
5. 当前执行链不再展示 Host 注入的历史助手上下文。保留当前工具回执、错误、确认过程和最终答复。
6. 统一项目定位契约：`project_id` 是 Agent 项目 UUID；业务编号、模具号或名称走 `identifier`。非法 ID 明确返回参数错误，不再把错误定位产生的空结果当成业务缺项；不自动互换两个字段。
7. 修复三个旧附件测试的跨会话数据构造，使其通过实际会话/附件 API 绑定原件；没有放宽附件权限或撤权校验。

## 已发现的浏览器问题与边界

- 浏览器使用 `http://127.0.0.1:5173/` 已登录账号及其配置的真实 Qwen 模型，测试对象为已有合成项目 `SMOKE-CUSTOMER-ACCEPTANCE-0920155336`。
- 早期运行 `06d15030-7307-4641-9237-6287ef2dc759` 因工具名错误终止；运行 `6df88242-78a7-4600-8adb-4a0ce48da6a2` 暴露仅用临时系统提醒无法可靠恢复的问题。失败记录保留。
- 补全错误对后，模型能在未激活工具被拒绝后重新发现工具并继续查询，但一次复合请求没有独立核对执行链，不能记作完整通过。
- 续问运行 `7a3fa606-e65b-4fca-9167-3063409a0de1` 把项目编号放入 `project_id`，随后根据空结果错误推断设计缺项。此运行虽然状态为 `SUCCEEDED`，其业务结论仍为失败证据，不能按状态计作验收通过。
- 补严定位契约后，浏览器重新读取真实执行链，正确定位到“基线计划交接 / 尚无生效项目基线计划”，而非之前错误推断的设计阶段。

## 最终回归结果（本轮）

- 后端全量：`674 passed`（537 秒）。最后补充的项目定位契约修改另跑相关业务测试：`88 passed`；11 个定位输入模型的字段契约测试全部通过。两项第三方依赖弃用警告仍存在。
- 前端：20 项 Vitest 通过，mold/template TypeScript 类型检查通过。
- 浏览器单独执行链查询正确；同一会话用“这个项目”追问收尾链正确定位到客户验收失败。参数错误和工具发现错误保留在执行过程，模型通过实际工具自行纠正。
- 普通结束语运行 `41d1d67d-685c-48b7-bd21-aefa2ed55fc1` 正常回复；浏览器未显示业务工具调用，也未把历史助手回复重新显示为本轮过程。
- 已处理的“客户质量验收确认验收”卡可展开完整详情；“暂不执行”和“已确认执行”均禁用，页面明确不可重复执行。本轮只查看历史卡，没有新确认业务操作。
- 浏览器控制台捕获的 error/warn 为 0。

### 尚未通过，必须继续处理

联合查询复测运行 `38969ecd-da53-4fe7-9637-a30efb07f24f` 只调用 `query_project_completion_context`，却同时断言执行链卡在客户验收、收尾链卡在结项清单。这与独立执行链的 `current_focus=baseline_plan`、收尾链的 `current_focus=delivery_acceptance` 不符。状态虽为 `SUCCEEDED`，业务验收仍判定失败。当前证据 ID 校验只能证明引用来自本轮，尚不能保证每项结论有对应范围的证据；下一步应继续审查任务分解、证据范围和最终答复验证机制，不按这句话编写关键词路由或硬编码答案。

历史验收确认回复还出现了“财务依据本记录执行扣款”“同步调整计划”的越界建议；确认卡实际只登记验收事实，并未执行扣款或改计划。这也是待修正的语义问题，不能把确认卡 UI 正常视为模型答复正确。

所有真实 ERP 及生产业务验收状态保持未验收，以上浏览器场景使用本地合成数据。

## 后续开发：任务范围、字段语义与迁移核查

### Harness

- 原失败运行 `38969ecd-da53-4fe7-9637-a30efb07f24f` 实际只有 3 轮、无压缩、`finalizing=false`；工具结果完整，不能把该次错误归咎于上下文截断。已发现的技能局部收口要求与用户多事项请求之间的边界冲突已澄清。
- 按准确工具名 ToolSearch 现在也加载对应技能的说明，优先直接依赖该工具的技能，而不是把它列为可选后续工具的协调器。此操作仍只激活指定工具，不自动扩大执行权限。
- 重复调用返回完整的拒绝调用/观察消息，不再直接强制结束整个任务；混合批次明确标记未执行，模型可继续处理尚未覆盖的其他事项。
- 运行预算或上下文接近上限不再触发隐藏工具并强制生成业务结论。预算耗尽以 `BUDGET_EXCEEDED` 失败关闭，保留已有执行证据；上下文不足仍使用原有无损压缩门禁。协议修正和可信确认后的回复继续保留独立收口规则。
- 真实模型复测 `2cc75c15-718a-4c90-a716-fbd0a1349348` 在修正技能加载后仍先读取了无关工程联络，耗尽 12 轮后给出错误执行链结论，故仍未通过。此证据推动移除预算强制收口，不能以新增提示词宣称语义问题解决。该次运行发生在移除预算强制收口之前。
- 技能/目录相关回归 177 项通过；随后预算逻辑调整的 Harness 回归 137 项通过。测试证明协议与调用边界，不证明真实模型能够正确完成任意复合问题。

### FR-002 字段贯通

- 中标接收新增 `customer_model_number` 与 `customer_material_number`，和客户模号、外部订单号分别保存；确认卡、版本查询、开工材料快照和模型语义上下文均保留独立含义。
- 旧 `customer_model_or_material` 保留为“历史未分类原文”，不猜测、不回填。新版本核对字段不会重写旧接收版本与旧开工快照；未提供新字段时保持旧来源内容指纹，避免绕过防重。
- 新迁移 `mc0d0e000013` 基于仓库可达版本 `mb0d0e000012`，只增加两个可空字段。独立 PostgreSQL 空库安装、旧版采用、迁移模型一致性与业务回归 33 项通过；来源指纹兼容单测通过；包含开工/执行/启动/API 的后续集成回归 48 项通过。
- FR-001～003 的暂停恢复误挂证据已纠正，原暂停恢复证据继续保留在 FR-091～093。覆盖表仍完整保留 118 个 FR、18 个 AT、13 个 AD，不声明整项验收通过。
- 核查时发现 FR-001 的业务类型曾由 `historical_mold_number` 是否存在推断，但该字段历史关系实际是 BACKUP/REFERENCE。此问题已在下述首次开工分支中修正；已有项目再次设变开工仍未完成。

### 本地数据库部署缺口

2026-09-20 本地 `127.0.0.1/moldpilot` 的版本表实际为 Core `a10c0e000008`、Mold `mb0d0e000013`；仓库、可达提交和不可达 Git 对象均未找到后者。该库另有当前源码未登记的 document_intake、document_ocr_job、document_recognized_page、document_extracted_field、contract_intake_group、contract_intake_mold_match、contract_mold_line、contract_relation 等表及合同/付款节点扩展列。

本次 `scripts/migrate.py upgrade head` 明确失败于缺失历史 revision，新增两列未应用。未降级、未删除额外结构、未修改版本戳。已向用户询问旧项目或迁移备份路径；OCR 仍按约定暂缓，不能因发现旧表就重新启用。当前已启动 API/Worker 保持此前加载的代码；新增字段与最后预算修复尚未部署到该运行库，不能把独立测试库验证写成当前浏览器部署成功。恢复迁移来源并完成兼容核查后再升级、重启及回归。

## 后续开发：首次开工类型与冻结对象核对

- `prepare_internal_start` 必须明确选择新模 `NEW_MOLD` 或首次承接外部模具设变 `FIRST_EXTERNAL_CHANGE`；不再根据历史模号推断。历史 `BACKUP/REFERENCE` 关系作为独立材料进入确认卡与快照，本次执行仍引用 ERP 已确认的内部模具关联，不自动建模或改模号。
- 修正此前要求历史模号必须属于本次执行对象的错误门禁：参考/备份模具与本次目标分开记录。若两者混为同一对象，则要求核对；已生效原项目的再次设变不能借此入口重复创建项目或改写原模号。
- 在 BPM 提交和最终开工生效前，再次核对冻结的内部模具 ID、模号、可用状态、项目关联集合和中标资料最新版本。变化时阻断，项目保持未开工且不产生部门交接；原快照不被修改。项目、模具和关联加锁，读取刷新会话缓存，避免使用其他会话更新前的旧对象。
- 新中标版本确认和开工确认均锁定并刷新项目记录后重新预览，避免两条链路在同一项目上交错确认过期资料。
- 兼容边界：没有冻结快照的历史直接领域记录仍沿用原通路；旧快照未明确业务类型时要求重新核对，不能猜测迁移。此兼容路径仍需纳入部署与业务验收。
- 定向开工/领域/中标/启动回归 41 项通过；跨会话状态验证与前端类型检查结果见开发状态。以上为独立测试库验证，未更新当前浏览器运行的 API/Worker。
- 当时发现的 FR-001/079/080 再次开工缺口已进入下述开发；真实外部模具接收、客户物料查询和 ERP 档案核验仍未验收。

## 后续开发：原模再次设变正式开工

- 复用 `prepare_internal_start`、确认卡和 Agent BPM，新增 `EXISTING_MOLD_CHANGE` 业务分支。原项目须 ACTIVE，指定原项目已有的内部模具 ID，引用当前有效且审批完成的工程联络设变方案；不重复建项目、模具或中标接收记录。
- 本次客户模号、目标模具、客户收费或免费金额、供应商单独费用/合同/书面依据、执行方式、销售合同方式、开工原件和要求完成日期进入不可变快照，并进入 BPM 审批材料。客户收费与供应商费用不合并，客户免费不代表供应商免费。
- 收费、免费和无新增合同都必须完成本人确认及开工审批。引用的开工与收费附件须来自当前已批准方案；同一方案已有待处理或生效开工即阻断重复。提交与生效重新核对原模具、有效方案与冻结材料；项目暂停、模号变化或资料换版不能静默通过。
- 审批生效保持原项目 ACTIVE 并追加本次五类部门交接。既有计划、执行记录和模具不被重建；不代替 ERP 执行。工程联络允许记录已发生反馈事实，但复验/关闭需要该版方案的生效开工通知，进度查询显示缺口。
- 销售合同可选择无新增、沿用既有有效销售合同、或新增待到。待到合同在现有合同工具中通过 `start_notice_subject_id` 明确关联本次通知，原项目其他合同不能冒充补齐结果；确认关联随合同材料冻结，原开工快照不改写。
- 迁移 `md0d0e000014` 仅将开工快照的中标版本外键改为可空，便于原模设变引用原工程联络方案；首次开工仍由输入和领域校验强制要求中标资料。降级遇到无中标关联的设变记录会拒绝，不删除记录或伪造来源。
- 第一轮开工/合同/工程联络/拆分迁移集成回归 69 项通过；后续审批冻结材料与晚到合同闭环 34 项、客户收费/供应商费用及联络回归 44 项、权限及领域/执行/收尾读取方 25 项、最终供应商展示与财务读取方 16 项通过。两套前端类型检查和编译/diff 检查通过；测试集合有重叠。
- 尚未完成：真实 ERP 执行与受影响任务联动、目标环境迁移兼容与浏览器复测、真实角色及附件验收。缺失 `mb0d0e000013` 仍只阻塞当前运行库部署，没有改写版本戳。原多事项模型语义问题仍保留为失败证据。

## 后续开发：执行反馈不能跨方案冒用

- 核查发现原设变查询以任意项目开工判断设变开工、以客户引用判断书面证据，旧变更记录还按描述中的关键词推断类别。现改为明确分类和实际方案证据；逐单返回 `change_start_readiness` 与客户确认核对状态，项目最初开工不能填平本次缺口。
- 新增 `contact_execution.py`：反馈明确引用当前方案和对应生效开工，且实际完成时间不得早于开工生效。依据作为追加式反馈回执的 `execution_basis` 保存，不覆写旧反馈、不迁移猜测旧记录，不增加表结构。
- 未关联事实仍可如实登记；复验 PASS 和关闭要求反馈对应当前方案和通知。退回整改可以保留旧反馈并要求补充新的执行核对；新方案和新开工均已生效时，旧反馈仍被拒绝。ERP 来源仍须提供原记录引用与核对时点，此服务仅登记人员核对的事实，不声称已经自动验证 ERP 原件。
- 既有联络面板展示反馈对应方案/开工，查询返回授权可见且材料仍有效的执行依据选项；缺失/旧版依据不再被查询成可关闭。旧无关联反馈幂等重试保留原请求指纹。
- 回归覆盖真实 BPM 开工后的反馈→独立复验→关闭、未关联事实保留→退回→重新关联反馈、反向时间阻断、新方案/新开工不沿用旧反馈、旧请求重试兼容及查询权限。第一轮 30 项、补充 19 项、查询/收尾/联络 27 项、最终影响项/项目关闭读取方 10 项通过，集合有重叠；两套前端类型检查、编译及 diff 检查通过。未进行当前浏览器的新版本部署，原迁移及真实模型问题仍在。


## 后续验收：独立安装与真实模型复合问题（2026-09-20）

- 新增 `scripts/browser_acceptance.py` 和 [本地验收说明](LOCAL_BROWSER_ACCEPTANCE.md)。从空库正常迁移当前源码，两套结构检查均无新增操作；独立 PostgreSQL、Redis 15、worker scope、模型配置、文件存储及 localhost 登录 Cookie。原 5173/8000 运行库未迁移，API/Worker 未重启，真实 ERP 未连接。
- 内置浏览器在 localhost:5174 登录合成账号，确认合成项目 `ACCEPTANCE-COMPOUND-001` 的客户验收失败事实。原建议由种子准备，确认后的恢复由真实 Qwen3-30B-A3B-Instruct 完成。Run `40c35eaa-9dfb-45ab-a595-4609ed783228` 成功恢复；验收记录和原签收各 1 条，计划任务仍为 0。历史详情保留、两个办理按钮禁用。回复的“立即启动返工”“确保复验一次通过”等建议仍未区分后续授权与实际结果，不能算完整语义验收通过。
- 同会话联合查询 Run `66eb8e32-023e-47a1-829e-caa7602fb763`：模型先误把技能名当工具名，Harness 返回未执行观察后，模型自行 ToolSearch 并正确调用执行/收尾两个查询。两项最早缺口分别答对 `baseline_plan` 与 `delivery_acceptance`；但追加“两个链路均未进入后续阶段”与已存在客户签收和验收记录矛盾，整条答复判为语义失败。4 轮，无上下文压缩，无预算强制收口。
- 新会话不同问法 Run `f5692ff2-e0e3-4918-b499-3ad5ad1573eb`：只调用业务档案与收尾查询，却把收尾客户验收缺口当成执行链最早缺口，判为语义失败。5 轮，无压缩，`finalizing=false`，不是预算不足或强制收口导致。
- 单事项对照 Run `e79bdf3f-9597-4d78-adc5-912871c7722b`：实际调用执行查询，正确定位基线计划缺口，保留委外合同和未通过验收事实。普通结束语 Run `151f6033-01fd-4162-97f8-b87297c1e5d7` 返回 CONVERSATION，0 次业务工具调用。
- 浏览器 error/warn 捕获为 0，mold/template 类型检查、Python 编译和 diff 检查通过。以上是独立合成环境的新版本证据，不代表原环境升级或真实 ERP 验收成功。
- 结论：当前 Harness 的协议、证据 ID 和操作回执校验无法证明每项自然语言结论都对应正确证据范围；已有完整任务提示仍不足。本轮保留失败运行，没有追加针对这两句话的关键词规则、答案改写或补查兜底。后续应继续做通用任务范围与结论证据关联设计及跨场景评估，不把一次正确路由当作复合任务能力通过。


## 后续修复：完整工具目录与项目标识契约

- 对照 PI-Desktop `plugin-skills-prompt.ts` 的技能目录/按需正文分离方式，检查实际运行上下文，发现原目录将分组的所有成员视作已展示，却只输出前四个激活入口；分组尾部或非入口成员因而不可见。工具描述还被任意截断，可能丢失用途或限制。原 30 工具目录测试没有技能分组，未覆盖此缺陷。
- `tool_discovery.optional_tools_prompt` 现在逐一列出已授权工具的准确名称和完整注册说明，每个工具仅一次；另列明确不可调用的技能索引。技能分组继续决定按需 schema 激活，不再决定目录可见性。未增加关键词路由、自动查询或权限。新增正反成员顺序、重叠技能、未授权成员及说明尾部约束测试。真实初始 system 从 14135 字符变为 16312 字符，运行未发生上下文压缩。
- 目录/Harness 回归 140 项通过。只重启独立验收 Worker 后，Run `70f3049e-f960-4654-ac5a-4d3601d4d203` 加载新目录，并查到结项与执行事实，但再次调用同参结项查询，在此前两次未激活调用后耗尽协议修复次数，以 `MODEL_OUTPUT_INVALID` 失败关闭。9 轮、无压缩，未伪造完成答复；此问法仍未通过。
- 该运行同时暴露结项查询把业务编号作为 project_id 后误报 NOT_FOUND 的参数缺口。新增仅支持 ID 场景的 `ResolvedProjectId`，结项读取与继承的办理输入要求真实 UUID，并提示先查询项目，不能引导填写该接口不存在的 identifier 字段。项目档案（包括使用同一输入的财务读取）与暂停/恢复上下文采用已有 ProjectId 契约；有效 UUID 统一小写，避免大小写导致假未命中。
- 项目标识/结项/档案/财务读取方 37 项，随后暂停恢复/能力目录/业务包 46 项通过。非法结项参数在访问数据库前返回 INVALID_TOOL_INPUT；正式办理仍保留原权限、版本、确认与 BPM 门禁。
- 独立验收 API 更新后，双链 Run `d8526535-2fba-4fc4-9ca8-cd6dd1b3fdf7` 实际调用执行与收尾两个查询，正确分别说明基线计划和验收未通过缺口，没有追加后续阶段不存在的推断。4 轮、无压缩、`finalizing=false`；浏览器正文与持久化结果一致，error/warn 为 0。此单例通过不覆盖前述问法失败，也不证明所有业务结论正确。
- 原 5173/8000 API/Worker 及缺失历史迁移的原库保持不变。独立环境当前进程句柄已更新到其本机回执。完整交付、真实 ERP、原库兼容迁移与复合任务跨场景语义验收仍未完成。


## 后续开发：客户复验链与正常关闭实时门禁

- 对照 FR-068/069/097/098 发现原状态按同一签收记录的最后一条验收判断，独立初验通过可掩盖旧失败；正常结项门禁只核对清单和其他系统事实，没有直接检查客户失败验收。两处现已修复。
- CustomerAcceptanceRecord 增加可空 previous_acceptance_id。新复验必须明确引用同项目、同签收依据的当前链末记录，链中须有失败依据，日期不得倒退；再次复验继续引用最新记录。确认时项目锁串行化并重新核对，唯一约束阻止同一前次记录被两条复验分叉。初验不能带前次关联，已关闭项目不能直接追加验收。
- 状态只沿明确关联计算；另一条初验或旧无关联复验不会消除未解决失败。旧记录、责任、费用与影响信号原样保留，查询返回前次 ID、可继续复验的候选 ID 及历史未关联提示。新关联的确认卡显示前次结果与原件依据，无新增 ERP 菜单。
- 正常关闭在准备/提交与最终生效时重新检查客户验收失败或关联冲突，清单 DONE/NOT_APPLICABLE 不能覆盖这些实际失败；终止结算继续采用自身适用事项规则。关闭和验收确认共用项目锁。此修复不自动办理整改、扣款、改计划或合同变更，真实工程联络/供应商整改联调仍未验收。
- me0d0e000015 从 md0d0e000014 正常升级，只新增明确关联和约束，不猜测回填旧关联；数据库禁止验收事实 UPDATE/DELETE。存在已确认关联时降级拒绝丢失证据。冻结旧版适配检查显式登记新模型对象，不放宽其他结构检查。
- 业务、权限、交付/委外/执行/收尾读取方及关闭回归 47 项通过；空库安装、旧历史验收保留、结构检查和有链降级拒绝的迁移回归 2 项通过。旧测试将验收日期改为在插入时提供，避免修改已确认事实；正常关闭失败调用以事务回滚模拟真实请求，不绕过关闭门禁。
- 独立验收库实际升级到 me0d0e000015，Core/Mold 结构检查均无新增操作，独立 API/Worker 已重启。原 5173/8000 运行库及缺失历史迁移保持不变。
- 自然语言复验 Run `e990cd27-6ba3-4333-9182-d9c88d4be1b0` 错选工程联络与候选匹配后 MODEL_OUTPUT_INVALID。指定交付工具的接口测试 Run `defb05ca-f761-42d3-b685-319c26499e0f` 取得真实验收记录，但把 deduction_amount 填为 0（合法无新增扣款应为空），工具返回 INVALID_TOOL_INPUT 后又偏离到其他链路，最终协议失败。两次均未生成可确认的复验卡、未写入新验收，不能宣称浏览器办理闭环通过。
- 下一步 Harness 排查证据：通用参数错误经领域解析只保留第一条错误 message，缺少字段位置；当前“Input should be greater than 0”不能明确定位 deduction_amount。应检查跨工具的参数诊断/观察契约，不为这条问法放宽扣款含义或添加自动改参兜底。

## 后续开发：原生参数诊断与运行恢复（2026-09-21）

- 接续上轮实际失败，新增 `agent_core/tool_validation.py`。通用工具边界只增强 `INVALID_TOOL_INPUT`，沿 Python 原生异常链读取 Pydantic `ValidationError`（包括 `raise ... from None` 隐藏的上下文），保留各错误的字段路径、RFC 6901 Pointer、规则代码和 message。最多返回 20 条并明确总数和截断状态；不序列化原生 input、ctx、url 字段。领域自定义 message 仍由领域负责内容，不将其宣称为全面脱敏。
- `DomainError.as_dict()` 统一现有 HTTP 和 MCP 错误契约，新增可选 details；非校验错误保持原 code/message。MCP 失败继续回滚、没有执行回执；Worker 和 Harness 保留完整诊断作为下一轮工具观察。未修改工具输入、默认值、权限、扣款规则、模型配置或路由。
- 上轮运行句柄 83601 已确认退出 0：工具诊断/MCP/Agent API/Harness 172 项通过。本轮真实 `prepare_customer_acceptance` 经领域和 MCP 拒绝 deduction_amount=0，返回 `/deduction_amount`、`greater_than`，参数保持原值且没有 Step；工具诊断/MCP/Harness 149 项通过。保留原业务错误回归，另加结构化诊断到模型观察的独立用例，最终定向 2 项通过。测试集合有重叠，不相加宣称总覆盖；Python 编译及 diff 检查通过。
- 当前操作系统中原回执的独立 API/Worker/Web 进程及其子进程均不存在，5173/5174/8000/8001 均无监听。独立库 Core/Mold 结构检查通过，只有 6 个成功、3 个失败历史 Run，无运行任务。核实独立端口空闲后归档旧进程回执，使用原 runtime.json 恢复独立服务：API 45616、Agent Worker 19356、Message Worker 15824、Web 42432；实际句柄以本机 processes.json 及系统进程为准。未迁移、修改版本戳或启动原库服务。
- 内置浏览器 `rewriteDocumentation` 初始化失败，重置后 `getState` 同样返回 `failed to write kernel assets: 系统找不到指定的路径`。没有使用其他方式冒充浏览器验收。
- 经实际 HTTP API 登录合成账号，在新会话逐字重放上一失败 Run 的提示，生成 Run `8511a881-c24d-4f8f-9e99-bfc738bc3726`（会话 `6e27a4cd-2b3c-400b-a0ad-27ef77f1224d`）。第一轮 `MODEL_NETWORK_ERROR`，无工具 Step、无新验收事实。仍使用 Qwen3-30B-A3B-Instruct；活动内网模型服务 TCP 可达，但只读 `/models` HTTP 请求返回 RemoteProtocolError（服务断开 HTTP 连接）。这次不能评价修参效果，不能视为模型或复验办理通过。
- 后续仍须在模型 HTTP 和内置浏览器可用时完成实际复验确认链及正常关闭验收；原库迁移来源缺口、真实 ERP 与所有需求交付未因此解决。

## 后续开发：财务读取验收影响和供应商扣款（2026-09-21）

- 对照 FR-069/076/106 查明 `query_finance_context` 原有 `has_cost_or_deduction_signal` 只由 ContactTask 费用/工时生成。即使已经登记客户验收扣款或 SupplierDeductionSettlement，财务模型上下文仍遗漏这些事实。现新增 `quality_finance_context`，复用交付验收读取器与委外扣款读取器，不复制业务表或重建财务总账。
- 客户验收保留原记录 ID、前次复验关联、金额币种、责任、计划/合同影响和证据；供应商扣款保留原供应商、合同/联络引用、责任、结算状态与结算依据。双方分别列示，accounting_effect=NONE，不猜测关联、不自动对冲或改变合同应收应付/实收实付。复验 PASSED 不抹去历史费用，CANCELLED 与 SETTLED 不列为未结算。
- 来源仍要求原工具能力及原项目数据权限；不可见不等于没有记录。验收明细上限 50，明确返回总数和截断标记，影响摘要仍来自全量可见验收；供应商明细上限 100，达到上限且未找到待结算时返回未知而非“没有待结算”。财务模型上下文保留分来源事实；收尾查询返回细分标记，提示记录存在不能自动解释为尚未结算。财务 Skill 与工具说明同步说明边界，无关键词路由或模型答复兜底。
- 首轮财务/交付/委外/收尾集合 48 项通过、7 个新增用例因合成供应商缺少必填 category 失败；修正夹具并为独立扣款使用不同来源引用，未放宽数据库约束。新增来源/权限/截断回归 8 项通过；加入已取消/已结算语义用例和收尾读取方后最终 15 项通过。集合重叠。Python 编译、需求生成器均通过，118 FR/18 AT/13 AD 全量保留，FR-069/076/106 补充证据但仍 NOT_VERIFIED。
- 当前源码只读访问独立合成库，项目 ACCEPTANCE-COMPOUND-001 的 1 条历史验收已进入财务影响标记；实际回款、付款仍为 false，accounting_effect=NONE。本机证据保存于忽略目录 `.local/browser-acceptance/finance-quality-read.json`。该检查不是浏览器或模型测试。
- 核实独立库只有 4 个 FAILED、6 个 SUCCEEDED Run 后，仅更新独立 API：确认旧 API 父/子 PID 45616/39768 的 8001 命令身份再停止，启动新 API 父 PID 41296，回执已更新，`/api/health` 返回 200。原数据库/迁移未更改。
- 模型 `/models` HTTP 再次 RemoteProtocolError，浏览器 getState 仍初始化路径错误。未重新制造模型运行，未宣称可用。后续仍需开发/验证验收影响与财务处理的明确关联、真实 ERP 同步及完整结算闭环；此次只补齐已存在事实跨工具读取，不把查询贯通当作业务处理完成。

## 后续开发：已有扣款责任的结算确认（2026-09-21）

- 原 `prepare_supplier_deduction_settlement` 仅创建独立记录：先登记 RESPONSIBILITY_CONFIRMED 后，相同 source_ref 的 SETTLED 会判重；改来源又没有明确关联。核查 ERP `module_admin/service/purchase_reconcile_statement_service.py` 已有实际对账调整逻辑，本次只完善 Agent 原件/责任/结算依据协同，不复制 ERP 对账处理或修改 ERP 代码。
- 现有输入增加 previous_deduction_id 与 customer_acceptance_id。已有责任记录后续结算须明确引用前次 ID，status=SETTLED、source_ref 留空，原来源由前次 ID 追溯；须保持同项目、供应商、合同、联络、原因、责任、金额币种和已有验收关联。客户验收来源要求同项目及 project_close.read，客户与供应商金额无需相等，不推断转嫁或对冲。
- 本人确认时锁定项目并重新核对前次记录尚无后续结算、当前权限、项目版本及资料。原合同已关闭可以继续核对该原责任的结算证据，新责任仍要求合同生效。确认卡展示前次记录/原件、原来源、验收依据；未使用新菜单。重复同一确认返回既有回执，竞争卡阻断后到确认，数据库唯一约束防分叉。
- SupplierDeductionSettlement 增加两个可空外键和前次唯一/类型约束；所有确认历史禁止 UPDATE/DELETE。mf0d0e000016 从 me0d0e000015 正常升级，不回填旧关联；存在任何新关联时降级拒绝丢失证据。旧版采用检查只显式排除新模型对象，不放宽其他漂移检测。
- 财务和委外读取方检查全部可见关联后标注 previous_deduction_id、customer_acceptance_id、superseded_by_id、is_current、lineage_valid，保留历史明细。只有依据一致的明确后续结算能消除原责任的未结算标记；独立 SETTLED、金额不一致或其他异常关联不能抹去旧责任，不更改实收实付或合同余额。
- 43 项业务回归通过；补充历史供应商/责任哨兵后，迁移与收尾集 8 项通过；新增原合同关闭、禁止丢弃验收关联及不一致导入记录后，最终业务集 32 项通过。集合有重叠。迁移测试验证空库安装/完整降级、旧事实及空关联保留、不可变触发器、存在关联时拒绝降级；业务测试覆盖真实 human intent 确认与重复/竞争、原依据冻结和权限边界。Python 编译与 diff 检查通过，需求生成保留 118 FR、18 AT、13 AD，未升级验收状态。
- 独立库只有 4 个 FAILED、6 个 SUCCEEDED Run 后，按已验证的父子进程命令停止独立 API/Worker，升级 mf0d0e000016 并通过 Core/Mold 结构检查，再启动 API 父 PID 42676、Agent Worker 父 PID 26968；本机回执已更新。8001 健康检查 200，当前源码财务查询 RESOLVED，新工具 schema 含两个关联字段。原库/版本戳未改变。
- 内置浏览器 getState 仍报告内核资源路径不存在；模型 `/models` 仍 RemoteProtocolError，无新的模型任务。上述是自动化业务/部署验证，不是浏览器或真实模型验收。真实 ERP 对账、更正/撤销流程、完整财务处理与关闭仍须继续核验，不能把依据登记视为实际 ERP 结算操作已完成。

## 后续开发：既有付款冲正流程的工具封装（2026-09-21）

- 核查发现既有 finance_correction 已具备原付款关联、全额负向冲正及预留恢复，但没有 Agent 入口，暂停/终止/关闭项目又在提交入口被统一拦截。新增 prepare_finance_correction 封装原领域/BPM；不新建财务台账、页面或实际退款，不修改 ERP。
- query_finance_context 保留已授权分析中的付款明细、真实 ID、金额、项目版本和按原付款类别筛选的可用审批选项。工具准备只读，确认锁项目并重新核对权限、版本、原件日期、材料和已有/待办冲正；创建原 finance_correction 后提交所选 BPM。原付款与冲正金额币种进入审批快照，审批成功才追加负向记录及恢复申请预留；驳回保留原付款事实并允许重新申请。
- 暂停、终止、关闭项目允许原有授权财务更正，其他业务门禁保留。付款冲正仍是全额、供应商付款范围；客户收款更正、部分冲正、扣款取消与真实 ERP 对账不在此实现的验证范围。
- 首轮相关集合 46 项通过、4 项失败，失败暴露本人确认后的 Run 恢复排队使旧 finance receipt GET 错报 PROPOSAL_STOPPED。现仅对本人、权限指纹仍有效、工具仍可用且已有 CONFIRMED/SUBMITTED 持久化回执开放非运行任务的读取，不开放新执行或未确认卡；增加越权及停止任务测试。修正后定向 7 项通过，新增测试后最终 test_finance_correction_tools/test_finance_quality_context/test_model_harness/test_domain_pack 集合 172 项通过，集合有重叠；Python 编译和所改文件 diff 检查通过。
- 独立库运行记录为 FAILED 4、SUCCEEDED 6，无执行中任务。核对旧 API 父/子 PID 42676/44120 和 Agent Worker 父/子 PID 26968/1568 后仅重启这两个独立服务；新 API 父 PID 37868、Agent Worker 父 PID 28524，回执已写入。8001 健康、合成账号登录、能力目录均 200，目录包含 prepare_finance_correction。本轮无 schema 变更，独立库仍 mf0d0e000016，原库及版本戳未修改。
- 内置浏览器 getState 再次初始化失败（kernel assets 路径不存在）；模型 /models 仍 RemoteProtocolError。本轮没有新的真实模型调用或浏览器操作，自动化业务验证不冒充 UI/模型验收。FR-093/098/105/108/114 补充实现与测试证据，全部验收状态保持 NOT_VERIFIED。

## 后续开发：合同替代与实收实付执行的一致性（2026-09-21）

- 上轮推进了供应商付款冲正工具接入，本轮据 FR-031/102/104/105/108 继续核查执行链。确认现有 contract_relations/财务查询已汇总 ContractSettlementAllocation，但 payment_reserve、validate_customer_receipt 和实际付款校验未计入该历史；旧付款申请在原合同被替代关闭后仍能确认实付。原付款后续冲正也未抵减当前合同的已分配金额。再次核对 ERP purchase_reconcile_statement_service.py 的实际对账调整归属，本轮仅修正 Agent 已有合同版本/财务事实与审批，不复制或调用 ERP 旧审批/对账。
- 增加共用 allocated_total，实际回款的节点与合同额度、付款申请占用及实付节点额度包含已确认的有符号历史分配。实际付款锁定原合同再锁付款节点，与合同替换的前序合同锁核对同一历史；旧合同非 EFFECTIVE 时原付款授权不能用于新实付，在途付款审批也阻断。原单据、历史审批、未支用预留记录保留，查询另标注 contract_status/payment_execution_eligible，不把旧审批事实解释为当前可支付。
- 新 correction_allocations 只依据原付款已存在的明确有效合同分配计算冲正影响，不按金额或文本猜测目标；校验项目、版本链、原件、币种、金额和节点，冲突分配拒绝。准备卡展示影响，提交检查目标合同读取权限，BPM 快照冻结，生效前再次比较。通过后追加关联负向分配并写审计事件，原分配保持原值；后续合同替代仍读取原正负付款事实各一次。
- 若合同先替代、冲正后审批，CORRECTION_ALLOCATION_CHANGED 阻断未获批准的新分配影响；若冲正先完成、旧替代审批后完成，则原完整性门禁阻断缺少新负向记录的替代。历史冲正详情使用当轮审批快照，后续再替代不重写历史目标。取得节点锁后 refresh 付款申请，避免另一会话已确认付款但本会话仍使用旧预留余额。
- 新增 test_replacement_finance_execution.py 首轮 5 项全部失败，分别证明超额申请、旧授权付款、缺失冲正分配、审批依据漂移和超额回款。修复后首轮 32 项通过、1 项错误码断言失败；确认现有 require_source 契约为 SOURCE_INVALID 后只修正测试预期。扩充审批交错场景后合同/财务/领域集合 56 项通过；再补跨会话余额刷新及财务/收尾回归，最终 32 项通过（集合重叠）。Python 编译和定向 diff 检查通过；无新增 schema。
- 核实独立库 FAILED 4/SUCCEEDED 6、无活动 Run。重核 API 父/子 37868/43292、Agent Worker 父/子 28524/42212 的命令身份后，仅重启独立服务；新 API 父 PID 18228、Agent Worker 父 PID 19444，本机回执更新。8001 健康、登录、能力目录均 200，prepare_finance_correction 已加载，Core/Mold 结构检查通过，仍 mf0d0e000016。原库、版本戳、ERP 代码及服务未修改。
- 内置浏览器 getState 仍失败于 kernel assets 路径初始化，未执行 UI 操作；本轮未重试模型调用。没有把自动化与进程检查视为浏览器、真实模型、真实 ERP 或业务验收。客户回款更正、部分冲正、旧合同迟到实际付款的后续适配及完整财务处理仍需继续核对；不宣称整个项目交付完成。

## 后续开发：生效阻断与退回后的冲正修订重提（2026-09-21）

- 上一轮已实现合同替代与实付/冲正的一致性，本轮检查异常后的办理路径。发现 prepare_finance_correction 仅新建，CORRECTION_PENDING 又阻止对 RETURNED/APPLY_BLOCKED 原申请新建；现有 business.submit 不能修改材料，business.retry_apply 也不能绕过旧快照变化。此属实际办理断点，依据 V3.6 第 12.5 节及表单 revision/round_no 完整重审要求继续开发。审批归属遵循 PRODUCT_CONTRACT，复用 Agent 自有 BPM，不调用或改写 ERP 旧审批。
- 原工具增加 existing_subject_id/subject_revision/revision_reason 配对契约，继续提供完整当前材料与显式流程。原付款 ID、项目及责任域不变；DRAFT/RETURNED/REJECTED/APPLY_BLOCKED 允许申请人或有业务权限的管理员修订；SUBMITTED/EFFECTIVE、其他人的申请、缺少完整材料读取、过期版本、运行中审批及已有实际冲正均阻断。
- business_revisions 建立可复用的版本与审计边界，具体业务适配器校验字段并更新本业务材料；确认先锁原申请再锁项目，重检后递增 revision，保留原号及创建人，从 DRAFT 建立新 round_no/ApprovalInstance。旧审批动作、结果、incident 和快照不改，新轮完整重新审批；准备只读，重复确认返回原回执，竞争卡受版本阻断，提交/人员分配失败整体回滚材料、版本、审计及新实例。
- 原卡展示原申请状态/版本、原材料、修订原因、当前原件及分配影响。query_finance_context 保留原申请 revision/created_by，财务 Skill 说明使用原 ID 重提，禁止换付款绕过原申请。新增 business.revised 审计记录操作者、前后值、原因及前轮实例，前端只补审计中文名称，没有新菜单或页面。
- 由于该审计包含完整财务材料，新增通用 audit_projection：可信事件生产方声明明细所需业务权限、范围及字段，审计读取按当前授权交集投影；只有 audit.read、跨项目、字段不全或权限已撤销均不返回财务详情或含原因的摘要。新增规则只用于声明受保护的事件，本轮不把未声明的全部历史审计当作已完成同等审查。Core 不硬编码 mold 业务名，新增修订事件使用本领域读取权限。
- 首轮冲正修订及既有冲正集 16 项通过；扩充财务/替代/领域包集合 55 项通过、1 项测试导入错误（域包导出名称不是可导入子模块），修正为真实核心 schema 路径。最终 test_finance_correction_revisions/test_finance_correction_tools/test_audit_projection/test_core/test_domain_pack 集合 75 项通过，集合重叠。验证真实本人确认与 BPM 的阻断→修订→新轮→生效、RETURN/REJECT/DRAFT、重复/竞争、材料权限、源付款冻结、失败回滚、历史快照以及审计范围/字段/撤权。mold/template 前端类型检查、Python 编译和 diff 检查通过。
- 确认独立库仍 FAILED 4/SUCCEEDED 6、无活动 Run。核实旧 API 父/子 PID 18228/26024 和 Worker 父/子 19444/41516 的命令身份后仅更新这两个独立服务；新 API 父 PID 45628、Agent Worker 父 PID 27492，回执更新。健康、登录、能力目录和审计 API 均 200，新工具描述含重提且审计响应含投影状态；Core/Mold 结构检查通过，仍 mf0d0e000016，无新增迁移。原库和 ERP 未改动。
- 内置浏览器 getState 仍失败于 kernel assets 路径初始化；本轮没有新真实模型或 UI 测试。原项目部署缺失历史迁移、真实 ERP 联调、其他领域表单的修订重提、财务剩余流程及全量业务验收仍未完成。需求覆盖状态保持 NOT_VERIFIED，持续交付目标保持 active。


## 后续开发：合同修订材料版本与新审批轮（2026-09-21）

- 核查 prepare_contract_record 只能新建，重复原件/合同门禁阻止原 RETURNED/REJECTED/APPLY_BLOCKED 继续办理。沿用业务修订校验和原确认卡，新增 existing_subject_id/subject_revision/revision_reason；保留原 BusinessSubject 编号/创建人，修订后递增 revision 并建立完整新 round_no。旧审批快照、动作、incident 不覆盖。原申请需无运行中审批、无实际收付款申请/回款执行；确认持有原合同与项目锁后重新核对，竞争卡失效，失败原子回滚。
- 不删除或解开原不可变表约束。新增 ContractDetail.material_version 当前指针，PaymentStage、ContractBusinessTerms、ContractReceiptEvidence、ContractSettlementAllocation 追加独立 material_version，材料版本与审批 revision 分离。新条款记录 attachment_selection 显式原件集合，旧附件可复用，新附件追加，历史附件仍可追溯。初版 None 保留旧最高附件版本读取语义。
- contract_materials 统一当前条款/接收依据/节点/分配读取，合同查询/开工/BPM/实收/预留/实付/冲正分配使用一致版本，旧节点即使 ID 仍存在也不能用于新执行。已有生效合同不能走本入口绕过替代/追加规则。替代合同因审批期间原付款冲正而阻断后，可在原申请显式补齐正负原件分配，新轮成功且当前余额不重复计入旧版本。
- mg0d0e000017 下接 mf0d0e000016，旧事实初始化材料版本 1，Terms/Receipt 复合主键和 Allocation 唯一约束加入材料版本，原 UPDATE/DELETE 禁止触发器保留。降级拒绝任意表存在版本大于 1 或明确附件选择，即使当前指针未更新仍保护追加材料。旧表采用映射排除新增列。独立 schema 安装/旧库采用/往返及无损拒绝测试通过；原库缺失 mb0d0e000013 问题未改。
- 相关旧合同/替代财务/财务修订集合 32 项通过；首次新修订/迁移集合 6 项通过；最终 test_contract_revisions/test_contract_tools/test_split_migrations/test_start_tools/test_change_start 共 57 项通过，集合有重叠。Python 编译与定向 diff 检查通过。合同签约事件全量版本语义、真实 ERP、真实合同修订 UI/模型仍未验收。
- 本轮开始核实原独立服务父/子进程已消失且 8000/8001/5173/5174 均无监听，独立库无活动 Run。正常升级独立库至 mg0d0e000017，Core/Mold check_all 通过，隐藏启动 API/Agent Worker/Message Worker/web，父 PID 分别 16136/26100/13088/19376；回执逐项保存。没有重启旧运行库服务。内置浏览器恢复，合成账号在 localhost:5174 正常登录；健康与配置模型 /models 均 200。


## 内置浏览器实测：目录拒绝不应冒充输出格式错误（2026-09-21）

- 本轮 CUA 恢复可用。使用内置浏览器在独立 localhost:5174 合成管理员登录，模型 Qwen3-30B-A3B-Instruct HTTP 恢复，未调整模型配置。原 5173 未启动，本轮不触碰缺失历史迁移的原库。
- Run 540cc39c-ec50-4146-8c29-b03aa0989902 从“工程联络执行与客户验收收尾，只查询”进入联络协作工具；先调用未激活工具、重复传入不支持 query 参数、将项目编号作为 case_id，随后重复查询与重复加载技能，在第 9 回合 MODEL_OUTPUT_INVALID。只取得 query_contact_cases 空可见结果，不能据此推断整个执行链无事实。没有压缩，没有业务写入。
- Run e11027a1-d709-4115-8919-97a6c69bd10f 查询“执行链和项目收尾条件，工程联络、客户验收与财务待办”，正确取得 query_project_completion_context 和 query_delivery_logistics_context；已读到客户验收 FAILED、扣款 3000 CNY、交期影响 7 天、未解决失败。第 7 回合调用尚未激活 query_finance_context 被全局 2 次 protocol_repairs 提前终止，而参数 envelope 是合法 native call。
- 对照 D:/pi-desktop/packages/agent-runtime/src/runtime.ts 的 buildToolSearchTool/findDeferredTools：工具发现仅改变 schema 可用性，native 错误观察不应变成最终输出格式错误。此次只修正通用分类：先校验 JSON 参数，再对不可用工具保留配对 observation、executed=false；全批不执行，计入普通工具/回合预算。移除用于目录错误的临时业务引导提醒；不替模型激活、不改参数、不增加次数、不拼接结果。真正非法 JSON 仍消耗原格式修复预算，重复调用规则本轮未变。
- 合成测试覆盖三个不同工具显式发现、已有 2 次格式修复后的目录恢复、未授权工具、混合批次全拒绝、普通工具预算和坏 JSON。首轮 144 通过/1 个旧预算预期失败，按新错误分类更新旧测试后，test_model_harness/test_tool_discovery/test_tool_validation 最终 149 项通过。
- 核实独立库 SUCCEEDED 6/FAILED 6 无活动 Run，核对 Worker 父/子 26100/8808 的 app.agent_worker 身份后仅重启该独立 Worker，新父 PID 17056，回执已更新。API/Message Worker/web 保持本轮启动进程。
- 原问题原样新会话复测 Run 689631a5-ad1d-469f-bebd-57473df9b5c4：3 次以上目录错误不再 MODEL_OUTPUT_INVALID，成功读取候选、收尾、交付验收、财务四个工具，但工具发现耗时/多轮调用仍导致第 12 回合 BUDGET_EXCEEDED，protocol_repairs=0；剩余上下文约 4.8k/32.8k。未取得执行链或完整最终答复，复合任务仍失败，不调大预算换通过。
- 浏览器失败任务的完整依据弹窗显示真实验收失败、扣款影响、结项清单缺失。发现四个新增事实字段仍使用“业务补充信息”，补齐 BusinessFacts.vue 中验收失败未解决、客户验收费用影响、供应商扣款记录/待结算标签；HMR 后在同一记录弹窗验证文字准确显示，mold/template 类型检查通过。无新增菜单/页面。初始浏览器控制台 warn/error 为空。
- 后续应继续核查通用发现与渐进加载：现有 fuzzy search 先选单个技能组，重复搜索会再次注入完整技能材料，模型仍频繁跳过显式激活。本轮没有用该单一样本重写检索权重或业务词表，也没有宣称上述剩余问题已经解决。

- 首页“查看我的项目”按钮正确填入请求，发送后 Run cd878838-47ea-4cf8-8a26-a111c73786f7 为 SUCCEEDED，浏览器显示项目编号、ACTIVE 与客户交期，实际 query_projects 与 query_project_control_context 返回值一致。不过模型又以不同参数读了两次控制上下文，且“无未完成任务”未复述 ERP 未联调/当前可见范围限制；只确认基础 UI 请求与结果展示链路可用，不判模型语义质量或完整业务验收通过。四次新模型请求均未进行业务写入。


## 后续开发：目录检索、技能说明复用与 MCP 元数据一致性（2026-09-21）

- 上轮为实质进展：合同材料修订、Harness 目录拒绝分类及浏览器证据均落地。本轮重新检查 PRODUCT_CONTRACT、当前源码、监听及进程；5174/8001 存活，原 5173/8000 未运行，独立库无活动任务，未启动 ERP。继续以完整目标开发，不把单次模型失败等同于整体阻塞。
- 对照 pi-desktop runtime.ts 的工具级检索发现，本项目 find_deferred_tools 仍按技能组描述/别名选择唯一赢家，强制前置 query_ 工具并排除组外候选。现描述检索直接排序全部授权工具，精确名称保持单工具，技能索引 key/name 明确选择才加载其声明的有界入口；候选上限仍 4，权限交集未变，完整索引不裁剪。多个相同分数按规范工具名稳定排序，成员顺序与重复技能别名不影响结果。
- 去掉 ToolSearch 结果中的整组工具重复描述。技能说明根据当前模型 transcript 内真实 assistant ToolSearch call 与 tool observation 的配对关系、skill key 及完整说明逐字匹配去重；不使用跨压缩的 loaded 标志。若原观察被压缩移除或说明变更，重新提供全文。业务工具即使返回同结构 source=harness 也不算已加载证明。发现操作本身可重复，不触发业务防重复；所有调用仍受原回合/工具预算约束，不自动补调用。
- 初步平铺检索与说明去重后，Run 3bbd8234-ac91-4f40-aa6c-251659b1950c（原复合问题）仍在第 12 回合 BUDGET_EXCEEDED，protocol_repairs=0、无压缩，实际读取全生命周期、交付物流及联络空结果；重复技能说明已经只返回 instructions_already_in_context。该结果不能作为业务验收通过。
- 检查目录发现“读取项目收尾链路”正式名称仅存 CAPABILITY_NAMES，原工具描述不含“收尾”；模型可见索引丢失人能看到的能力身份。Agent Core 的 tool_schema 现在从同一 capability_descriptor 获取正式名称并保留完整描述/参数，不手写别名。同时基于授权工具语料的逆文档频率降低常见“上下文/查询”等词的排名影响，不为失败样本配置词权重。中性 cobalt/nebula 合成测试验证跨技能与常见词不遮蔽特征词。
- 只修正 HTTP schema 后 Run 41e0fc6f-17b5-46a5-9c8d-10df0e754952 仍失败；核对该实际 Run 系统目录确认没有新标题，进一步定位 app.agent_worker 在 claim 后用 MCP Gateway.discover 覆盖 tools，而 mcp_api tools/list 使用 TOOLS 原 description。统一 MCP 描述与 inputSchema 均来自公共 tool_schema，新增真实 MCP Gateway 测试与 claim/schema 逐项一致，避免仅以直接调用 schema 的测试冒充实际 Worker 输入验证。
- 测试将旧的业务词路由预期改为新契约，保留精确技能入口/精确工具的独立行为；新增跨组、顺序、别名不干扰、授权过滤、重复发现、压缩恢复、工具观察来源及全目录中文身份保留。中间集合 154、43、198、175、199 项通过（重叠），接通 MCP 后最终 test_model_harness/test_tool_discovery/test_tool_validation/test_capability_catalog/test_conversation_context/test_domain_pack/test_mcp 共 207 项通过，仅原有两项依赖弃用警告。
- 每次独立服务更新前核实任务终态和进程命令/父子身份；Worker 17056/11312→996/13176→新父 7720，API 16136/25288→18240/14932→新父 4764，进程回执同步保存。Message Worker 13088、web 19376 未改。首次误查未定义 /health 返回 404，实际 /api/health 已核实 200；原库、模型设置、预算、ERP 数据均未修改，无新增 schema。

- 最终 MCP 修复后的原问题复测 Run 7bf9f3b0-c65c-4e9d-be93-3e0686bb47be：持久化系统目录已包含“读取项目收尾链路”，模型明确搜索生命周期/收尾/交付/联络技能；实际成功读取 query_project_lifecycle_context、query_project_completion_context、query_delivery_logistics_context。说明目录修复到达真实 Worker；本次没有再发生“搜索收尾却没激活收尾工具”。但仍在第 12 回合 BUDGET_EXCEEDED，protocol_repairs=0、无压缩，末次向不接受参数的 query_contact_cases 传 project_id；未完成财务明细与最终完整业务答复。浏览器明确显示失败，剩余上下文约 7.2k/32.8k，不将局部工具成功视为复合业务通过。
- 本轮三次浏览器请求全部只读，没有生成 proposal 或写业务事实。遗留重点：模型频繁先调用未激活工具、无参数工具仍传入项目字段，以及模糊检索候选带来的多个技能说明成本；不通过提高预算、自动代查、参数猜测或答案兜底掩盖。原运行库缺历史迁移、ERP 联调和 V1.1/V3.6 全量业务覆盖仍未完成。保持目标 active。


## 2026-09-21 供应商付款申请接手及内置浏览器验收

### 实现与复用边界

检查 V3.6 金额/节点修改完整重审要求及 ERP purchase_reconcile_statement_service.py 对账职责。Agent 既有 supplier_payment / finance.condition / finance.confirm 实现申请审批与事实确认，但仅实付已有会话工具，条件核验和申请缺少封装。新增两个准备工具并接现有 finance.execute、人工作用确认、Agent BPM、权限和审计；不修改 ERP 源码，不写 ERP 对账/银行事实，不增加业务菜单。

原申请修订复用 business_revisions：创建者或有业务权限管理员、完整材料读取、创建/提交权限及明确版本/原因；只允许 DRAFT/RETURNED/REJECTED/APPLY_BLOCKED 且无实付历史，旧轮快照保留。节点更换重新核验现生效合同、当前材料版本、条件和余额，旧占用释放与新占用同事务，生效/在途申请禁止改写。确认锁后刷新合同/材料/原申请明细，避免意向校验之前读入的旧 ORM 缓存。

浏览器检查暴露审批材料只含 stage_id。现 submit_subject 对付款冻结 payment_basis，包含当前合同编号/版本、供应商、节点与条件依据；提交者需完整合同材料读取权。该引用只进入新审批快照，不扩张普通付款查询读取源合同的权限，不回填旧实例。卡片和审批材料均复用通用工作区。

### 自动化与部署证据

- 初始独立测试 7 项通过；相关财务/冲正/合同替代/能力目录/领域包/MCP 97 项通过。
- 并发缓存测试最初误用不存在的 SUPERSEDED 状态，被数据库检查约束拒绝；改为通过真实替代合同审批路径制造旧合同失效。不是放宽生产约束。之后付款工具 9 项通过。
- 审批材料补全后：test_supplier_payment_request_tools、test_domains、test_replacement_finance_execution、test_finance_correction_tools、test_finance_correction_revisions 合计43项通过，2条既有依赖弃用警告。两套 Vue 类型检查通过。
- 独立库 check_all 两个 schema 均通过，无新迁移；原运行库仍未改动。每次更新均先核实无活跃 Run 和进程身份，仅更新独立 API/Agent Worker。最终记录 API parent20228、Agent Worker parent10996；后续重启必须重新核实身份。Message Worker13088、web19376 未重启。
- 新 scripts/create_supplier_payment_acceptance_fixture.py 仅接受隔离安装配置，拒绝同项目重复覆盖。创建合成 ACTIVE 项目、供应商并通过真实业务 API 完成合成合同审批及付款流程发布，不预置付款申请/实付或模型答复。运行回执与凭据在忽略的 .local/browser-acceptance 内，报告不包含密码。

### 内置浏览器与真实模型

沿用内置浏览器 tab1、localhost:5174。项目 ACCEPTANCE-PAYMENT-001 / ac73dc7b-569b-4964-b8e4-1f60a9330fe7，合同928911d7-9f7b-4ddc-8293-d0351cfaa123，节点9ea0d37a-c850-4304-9461-dff8fdfe8c33，付款流程ae7a96aa-745c-4f52-b92f-de773f6e5915。全为合成资料。

1. Run 3dadad55-6efb-454e-a7d9-fc1316ce47b9：浏览器输入核验条件及人工依据。模型先遇 TOOL_NOT_ACTIVE 后自行 ToolSearch，实际调用 query_finance_context 和 prepare_supplier_payment_condition。页面确认后持久化条件并展示已处理卡，恢复答复准确区分后续付款申请。此单项真实模型/UI通过。
2. Run 58d948f7-5f79-4ee8-bf22-b305d9d3d0a7：同会话申请60 CNY，明确“合成供应商付款审批”。模型查询合同、错误参数 contract_id/payment_subject_id，经诊断改为 stage_id 后仍使用合同审批流程66401ac6-a3aa-4248-ba81-c0ebd3d430e9；WORKFLOW_MISMATCH 后未查询财务流程选项，发生重复查询及无关计划工具调用，12回合 BUDGET_EXCEEDED。真实财务查询结果确实已提供正确付款流程。失败时未创建付款申请，没有提高预算/换业务关键词路由/自动修正参数/伪造结果。
3. 为独立验证 UI/BPM，直接调用准备工具种下明确标注“非模型生成”的卡，会话73d41144-a799-4e9e-ab5d-e00a796f9a4d。首卡 Run5a442bcd-4506-46b7-91a3-315b0593fb46、Step1462fb44-1aab-4af1-93bd-f500afc7fb76。浏览器展开卡核对60 CNY/余额100/条件/流程，确认后消息中心出现待办；人工退回，占用降为0，首轮快照保留。
4. 修订卡 Run940f1a2d-88e4-4e98-ac4d-73c27cafcc97、Stepbdd6f542-3e2e-49d3-929c-eef38c995ccf，同样明确非模型生成。浏览器确认原号/原60/新50/版本1/退回/修订原因，确认后新轮；审批材料实际显示第二版50 CNY、节点100、合同号、供应商名称和条件依据，人工同意并确认后页面显示审批完成。
5. 数据核对：原申请2a6ec106-d18a-4ac6-a43c-fa7e1e5c19f6 / SUPPLIER_PAYMENT-F92C00A5B4，当前EFFECTIVE、revision2、round_no2、amount50、reservation50。第一轮86d99547-a05e-46f9-b8a5-3880f052b777为RETURNED、冻结60，新增前的旧快照未回填；第二轮addf76a3-6660-4017-be86-1cbb15a10382为COMPLETED、冻结50及payment_basis。PaymentConfirmation数量0，无活跃Run。完整本地回执 supplier-payment-ui-verification.json。浏览器 warn/error 日志为空。
6. 合成卡确认后的恢复答复仍由真实模型产生。修订卡在第二轮尚待审批时，模型正文错误声称“完成最终审批”，随后又称“已提交审批”；页面卡片、待办、DB均真实保持SUBMITTED。记录为恢复语义失败，不能用该Run的SUCCEEDED判通过。需继续检查通用人工作用确认/业务审批的回执上下文边界，对照pi-desktop，不添加业务措辞替换或固定成功答复。

### 剩余缺口

真实模型申请流程及恢复语义未通过；当前人工条件依据仍不是完整交付/验收/发票/客户回款适用矩阵，也未完成特殊审批适配。修订卡原材料仍有英文字段、RETURNED原始状态，属于展示欠账。ERP真实联调和原库历史迁移未解决，其他业务修订/回执链仍需按覆盖表推进。118 FR保持未验收；此次合成UI通过不代表整体项目交付。
## 2026-09-21 结构化付款条件矩阵接续

- 针对 V1.1 FR-056 的条件缺口，`PaymentStage` 增加 `condition_profile` 与 `condition_evidence_map`。合同阶段输入现在明确 `payment_type` 和唯一条件规则；支持 DELIVERY、ACCEPTANCE、INVOICE、CUSTOMER_RECEIPT、CUMULATIVE_PAID 五类条件，以及 PREPAYMENT、PROGRESS、ACCEPTANCE、FINAL 四类付款类型。
- `finance.condition` 和供应商付款条件准备工具按规则逐项核验：`applicable=false` 的规则不阻断，适用规则缺少 `evidence_by_rule` 返回 `PAYMENT_CONDITION_MISSING`，特殊审批规则缺少 `special_approval_reference` 返回 `PAYMENT_SPECIAL_APPROVAL_REQUIRED`。条件确认后保存逐项证据；付款申请会冻结特殊审批依据，并要求所选 BPM 模板声明 `supports_special_approval=true`，普通模板在准备阶段阻断并返回授权候选；供应商付款申请仍须经过原申请→审批→待支付→实际付款确认边界。
- 新迁移 `mh0d0e000018_payment_condition_matrix` 与 `mi0d0e000019_payment_special_approval_reference` 增加结构化字段并保护非空降级；`migrations.py` 映射排除同步更新。独立验收库已升级并检查 `alembic_mold_version=mi0d0e000019`，原数据库/ERP 未操作。
- 验证：`tests/test_supplier_payment_request_tools.py` 13 项通过；联动付款、财务上下文、合同、领域和迁移集合 49 项通过；财务冲正、替代执行、修订、能力目录和 MCP 集合 60 项通过；mold/template 两套前端类型检查通过。首次全量测试被系统 pytest 临时目录权限阻断，改用仓库内 `--basetemp` 后全量 `817 passed, 2 warnings`；测试集合存在重叠。
- 仍不宣称整条需求通过：特殊审批模板的实际节点配置和业务验收、真实模型付款申请、恢复答复“已提交审批/最终生效”的语义一致性、真实 ERP 财务联调和原部署历史迁移仍待验收。覆盖表对应 FR 全部继续保持 `NOT_VERIFIED`。

## 2026-09-21 可信恢复回执生命周期边界

- 针对修订付款卡恢复轮次出现“已提交审批”被模型说成“最终审批完成”的问题，检查确认恢复链只把原始权威回执和自然语言提醒交给模型，缺少主机拥有的结构化生命周期边界。未添加业务关键词、固定成功文案或自动状态修正。
- `app.agent_resume` 现在保留原回执不变，并旁挂 `authoritative_receipt_semantics`：按 `status`、`business_status`、`approval_status` 机械投影生命周期和 `can_claim_effective`、`can_claim_approval_submitted`、`can_claim_confirmed_action`。未知、提交中、阻断、退回或驳回状态均不能声称最终生效；恢复系统提示要求模型只引用该结构化边界。
- `test_agent_api.py` 与 `test_model_harness.py` 共 169 项通过。该修复改善 Harness 证据协议，尚未重新完成真实模型付款申请及恢复答复验收；浏览器隔离环境与原 5173 端口状态按本记录其他章节执行。
- 同时修复通用错误回执裁剪：MCP 已返回的 `tool_error.details` 现在沿 `action_outcomes` 和 API 执行轨迹保留，工作流不匹配时的授权候选等结构化信息可用于模型恢复和人工核对；新增回归后两套测试共 171 项通过。
