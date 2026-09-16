# V1.1 全量需求开发覆盖表

本表保留 FR-001～118、AT-01～18、AD-01～13 原文。报价、中标、合同上传及项目大节点维护明确属于 Agent 开发；ERP 具体业务动作复用不代表整项需求已满足。

状态 **未验收** 表示尚未登记足以证明整条需求通过的证据，不表示没有任何代码。只有相关代码、权限/异常路径测试和业务验收证据齐备才能标记通过。V3.6 的架构、界面、Harness 和部署等要求仍需独立核验，不能由本表代替。

生成源为 `scripts/build_requirements_traceability.py`，机器跟踪文件为 `requirements/coverage.json`。生成器保留已登记的实现/验证证据；范围数量校验仅用于防遗漏，不能证明功能完成。

## 业务对象与匹配

Agent 开发关联、候选确认、防重和历史追溯；引用 ERP 已有实体，不复制实时台账

### FR-001

内部模具号应保持稳定。已有模具的设变沿用原内部模具号，另建独立设变记录及任务；首次承接的外部模具按建档规则建立内部档案。新模建档与已有模具设变应在内部开工流程中分支处理。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：backend/app/project_control_tools.py 会话查询、暂停建议和本人确认后提交 BPM；ProjectPauseDetail 冻结有效计划与未完成任务范围；项目暂停生效后复用执行门禁阻断普通执行动作
- 验证证据：tests/test_project_pause.py 覆盖审批前范围变化阻断；非 PostgreSQL 测试 109 passed；Vue 生产构建通过；专用 PostgreSQL 与 ERP 联调待执行
- 验收状态：NOT_VERIFIED

### FR-002

客户订单编号、项目编号、客户模号、内部模号、机型和物料号分别保存其业务含义，通过确认的映射关系关联。不得仅因某客户样本中字段值相同，就将全部客户的不同编码视为同一字段。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：恢复强制关联当前有效 PauseRecord；按实际暂停天数顺延冻结且未完成的计划任务；PauseTaskShift 保存每个节点计划日期前后值并以唯一约束防重复
- 验证证据：tests/test_project_pause.py 覆盖未完成节点顺延、已完成节点不变与重复恢复阻断；d33a12f7b9e1 PostgreSQL DDL 离线生成通过
- 验收状态：NOT_VERIFIED

### FR-003

一个项目包含多少套模具、合同与订单如何对应、设变是否增加订单及编号格式，在数据适配时确认；系统应保留实际确认的对应关系，不能仅凭合同号或模号相似自动合并。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：PauseRecord 保存区间与客户交期快照；恢复不修改 ProjectProfile.customer_due_date；暂停期仅阻断普通执行动作，资料、沟通、工程联络和结算核对仍按权限办理
- 验证证据：tests/test_project_pause.py 验证客户承诺交期保持不变及顺延审计；真实业务验收尚未执行
- 验收状态：NOT_VERIFIED

### FR-004

自动匹配成功时呈现候选及来源；无法匹配或多条匹配时进入人工处理，不得编造历史报价、历史模具或客户编码。空值、未确认值与有效业务值应可区分。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_business_object_candidates 只读工具按项目号、项目名、客户名、模具号、合同号、订单号等线索返回候选项目、命中字段和来源；候选匹配限定在当前用户同时具备 project.read 与 project.dossier.read 的项目范围内；订单、合同和联络线索还要求对应查询工具可用；多候选返回 MULTIPLE_CANDIDATES，未找到返回 NOT_FOUND，不自动创建、合并或承接业务对象
- 验证证据：tests/test_business_matching.py 覆盖多项目同模号候选、隐藏项目不可见和合同号按授权工具参与匹配
- 验收状态：NOT_VERIFIED

### FR-005

重复邮件、附件、合同或确认操作应能被识别并提示，避免重复建项目、重复累计收付款或重复生成同一付款申请。人工纠正匹配关系须保存前后值、原因、时间和人员。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：业务对象候选匹配可在创建或接收业务资料前提示当前权限内已有候选，降低重复建项目或重复关联风险；工具只返回候选与来源，不执行去重、合并、收付款累计或业务纠正；正式纠错仍需后续流程和人工确认
- 验证证据：tests/test_business_matching.py 覆盖候选不自动合并及权限边界；重复邮件/合同/付款的完整去重流程尚未验收
- 验收状态：NOT_VERIFIED

## 报价

Agent 开发资料接收、成本/工艺/工期评估、加工方式、报价版本、提交反馈及历史查询

### FR-006

业务人员接收客户报价图纸、项目资料或先行收到的中标资料，确认客户及项目基本信息后提交报价评估。接收资料、附件和沟通依据应与报价记录关联。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_quote_acceptance_context 可按项目 ID/项目编号/名称或授权候选线索定位当前可见项目，汇总报价承接上下文与关联依据口径；报价承接上下文工具返回未找到、多候选、无权或已定位状态，不创建报价、不接收附件、不自动提交评估
- 验证证据：tests/test_quote_tools.py 覆盖工具 schema、有效承接摘要和定位口径；完整资料接收、附件关联及报价评估流程尚未验收
- 验收状态：NOT_VERIFIED

### FR-007

总经理根据项目资料、客户类型和负荷判断是否报价及承接。不承接时记录拒单原因并结束对应流程；可承接时组织成本、技术及项目人员评估。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_quote_acceptance_context 汇总有效承接与有效拒单记录，并明确拒单、承接、正式开工互不等同；工具只读展示决定事实，不代替总经理判断、评估组织或拒单结束流程
- 验证证据：tests/test_quote_tools.py 覆盖承接/拒单上下文读取及多候选不自动决定；承接/拒单审批链尚未完成验收
- 验收状态：NOT_VERIFIED

### FR-008

内部加工由成本人员核算价格、技术人员开展粗略工艺分析、项目人员估算工期；整套委外应评估供应商价格、交付周期及其他要求。报价形成的价格、交期、收款条件及依据应保留版本。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_quote_evaluation_context 按项目线索核对报价阶段金额/币种/依据文本、最终加工方式、可见销售合同、可见整套委外合同和计划任务摘要；工具明确区分综合证据文本与结构化成本核算、粗略工艺分析、工期估算明细；当前返回 gaps，不把综合依据当作完整结构化评估；整套委外承接时检查当前可见整套委外合同，不把承接方式自动等同于供应商价格、周期和合同已确认
- 验证证据：tests/test_quote_evaluation_tools.py 覆盖金额/加工方式/客户下游事实派生状态、结构化拆分缺口和多候选不自动决定；完整成本/工艺/工期评估表单和正式业务验收尚未完成
- 验收状态：NOT_VERIFIED

### FR-009

报价阶段形成初步加工方式，中标承接审批时确认最终加工方式。执行中需调整时，评估已发生采购、生产、费用及交期影响，经审批后切换，并保留原方式及变更记录。后续任务按当前有效方式执行。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_quote_evaluation_context 同时返回报价/承接记录中的 execution_mode、项目档案当前 execution_mode，并在两者不一致或历史记录出现多个方式时提示需要核对当前有效依据；工具把报价阶段、承接确认和执行中变更的加工方式视为不同依据层，不自动切换项目方式或下推执行
- 验证证据：tests/test_quote_evaluation_tools.py 覆盖最终加工方式派生状态和多候选边界；执行中方式变更审批、已发生采购/生产/费用影响评估及后续任务重路由尚未验收
- 验收状态：NOT_VERIFIED

### FR-010

内部加工或委外评估完成后，汇总价格、交期和相关条件提交客户；保存提交版本及客户反馈。报价结果、后续修改和对应承接结果应可查询。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_quote_acceptance_context 将报价承接决定、后续承接结果和开放中的报价决定按项目上下文返回，供会话查询核对；当前只查询已登记业务单据事实，不生成报价版本、不提交客户反馈、不覆盖原报价依据；query_quote_evaluation_context 进一步汇总报价金额、加工方式、可见合同和客户反馈/下游事实信号，并保留 gaps 说明正式提交版本与客户反馈仍需专门材料
- 验证证据：tests/test_quote_tools.py 覆盖有效承接、开放决定集合和项目线索定位；tests/test_quote_evaluation_tools.py 覆盖客户反馈或下游合同信号；报价提交版本与客户反馈全流程尚未验收
- 验收状态：NOT_VERIFIED

### FR-011

报价数据库应保存客户公司、负责人、项目名称、模具信息、金额、我方责任人、历史报价及资料，供中标匹配。历史资料用于评估参考，不代替本项目的承接和价格审批。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_quote_evaluation_context 可按项目编号/名称等线索定位当前可见项目，并返回项目画像、报价/承接记录编号、报价金额、依据文本和后续合同摘要；历史报价资料仅作为可见上下文呈现，不替代本项目承接、价格审批或人工匹配确认
- 验证证据：tests/test_quote_evaluation_tools.py 覆盖项目线索定位和报价版本摘要；客户公司/负责人/模具信息/附件资料的完整报价资料库尚未验收
- 验收状态：NOT_VERIFIED

### FR-012

报价成本与后续实际材料、加工和外协成本分别保留，报价收款条件与最终合同条件建立对应关系；合同、设变或执行数据变化后，不覆盖原报价依据。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_quote_evaluation_context 将报价金额/依据、项目档案加工方式、可见销售合同和可见整套委外合同分开返回，避免用合同或执行事实覆盖原报价依据；工具目前只做上下文核对，不计算后续实际材料、加工、外协成本，也不把合同节点自动回写为报价收款条件
- 验证证据：tests/test_quote_evaluation_tools.py 覆盖合同权限隔离，未授权时不泄露合同号；报价成本与后续实际成本、合同条件映射和设变后历史保护仍未完整验收
- 验收状态：NOT_VERIFIED

## 中标与承接

Agent 开发客户分类、中标接收、匹配、人工承接/拒单及同一开工草稿延续

### FR-013

客户邮件按海信、海尔、其他客户分类处理，分类由人工确认。合同可从邮件获取或人工上传；模具图片由设计文员从客户报价资料中的UG图片等来源获取后上传内部通知单。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_bid_intake_context 按项目线索核对客户分类、客户规则键、可见合同、模具关联、承接/拒单和开工上下文；工具明确不读取邮箱、不连接客户平台、不上传合同或模具图片；未见客户邮件、客户平台文件、人工上传来源或模具图片时返回 gaps；客户分类只来自当前可见项目档案和客户规则，不根据项目名称或单个字段猜测海信、海尔或其他客户规则
- 验证证据：tests/test_bid_intake_tools.py 覆盖客户分类/合同/模具/承接上下文、来源资料缺口和权限隔离；真实邮件接收、合同上传和模具图片材料流程尚未验收
- 验收状态：NOT_VERIFIED

### FR-014

海信的中标信息与外部开工通知通常合并接收，收到后仍须人工确认承接。合同中的项目号、模具号、机型和项目名称与邮件信息核对，匹配失败由业务人员维护；财务确认金额和付款条件，业务审核及上传记录留存。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_bid_intake_context 将销售合同、承接决定和内部正式开工分开返回，并在合同存在但未见承接时提示合同不等于已承接；工具通过 project_profile/customer 返回客户规则键，可用于核对海信等客户规则是否已人工维护；匹配失败仍须人工维护，不自动创建或改写项目资料
- 验证证据：tests/test_bid_intake_tools.py 覆盖合同上下文、承接状态和多候选不自动决定；财务确认金额/付款条件、业务审核及上传记录留存尚未完整验收
- 验收状态：NOT_VERIFIED

### FR-015

海尔中标后由项目参与人员评估内部生产、整套委外或拒单。承接决定和拒单原因留存；按实际收到的客户开工通知维护订单编号、开工时间及交期。海尔平台对接方式单独适配，不能将客户平台自动下发理解为本系统已具备自动接口。

- 最新口径：客户平台自动连接已由用户取消；保留人工接收、维护、确认及依据。
- 实现证据：query_bid_intake_context 的 limitations 明确海尔等客户平台自动对接不作为已具备能力；当前只核对人工接收、维护、确认和依据；工具返回承接/拒单决定、最终加工方式和项目状态，但不自动从客户平台下发或维护外部开工通知
- 验证证据：tests/test_bid_intake_tools.py 覆盖承接/拒单上下文读取和权限边界；海尔人工中标接收、项目参与人员评估及客户开工通知维护尚未验收
- 验收状态：NOT_VERIFIED

### FR-016

其他客户通过邮件或文件提供中标和合同资料，参考海信流程并按确认的客户规则处理。中标和开工通知同时到达或分别到达均应可记录。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_bid_intake_context 不把客户规则固定为某一客户流程，按当前项目档案中的客户规则键和可见资料核对中标、合同、承接及开工事实；工具不判断中标和开工通知是否同时到达，只呈现当前可见依据并提示缺少来源记录时须人工补充
- 验证证据：tests/test_bid_intake_tools.py 覆盖项目线索定位和缺少来源资料 gaps；其他客户规则配置、同时/分别到达记录和文件来源流程尚未验收
- 验收状态：NOT_VERIFIED

### FR-017

中标信息进入后匹配报价及历史模具。系统生成待处理记录或开工通知单草稿，保存客户公司、负责人、客户模具号、金额、项目名称、我方收信人和匹配结果。未匹配的历史报价与历史模具字段留空，由人工补全适用信息。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_business_object_candidates 提供中标/承接前的候选项目匹配；query_quote_acceptance_context 可复用授权候选匹配定位项目并返回承接上下文；多候选返回候选列表并要求人工明确项目 ID，不虚填历史报价或历史模具；query_bid_intake_context 汇总客户、模具、合同、承接和开工上下文；未见待处理记录或开工通知单草稿时只报告缺口，不自动建单
- 验证证据：tests/test_business_matching.py 覆盖候选匹配与权限边界；tests/test_quote_tools.py 覆盖上下文多候选不自动决定；tests/test_bid_intake_tools.py 覆盖中标上下文多候选不自动决定
- 验收状态：NOT_VERIFIED

### FR-018

历史模具关系区分备份模具与参考模具，历史模号由相关人员确认。项目负责人会同设计、部门主管等评估利润、负荷和工艺后，确认内部承接、整套委外或拒单。涉及厂内量产或整套委外时通知对应冲压、采购等岗位，具体审批人员按适配矩阵执行。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_bid_intake_context 仅在具备项目业务档案读取权限时返回内部模具或历史模具关联，并把承接决定、执行方式和拒单记录作为独立事实展示；工具提示未见模具关系或客户来源材料时不能编造历史模具；涉及整套委外或厂内生产通知仍须后续审批矩阵和业务流程
- 验证证据：tests/test_bid_intake_tools.py 覆盖模具关系权限隔离、承接/拒单上下文和多候选边界；备份模具/参考模具区分、历史模号人工确认和岗位通知矩阵尚未验收
- 验收状态：NOT_VERIFIED

### FR-019

开工通知单草稿可在承接确认前建立，承接审批后继续完善同一条记录；不得因状态变化重复建单。拒单记录保留原因和审批依据，不进入任务执行。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_quote_acceptance_context 同时返回有效承接、有效拒单、开放报价决定和正式开工摘要，帮助核对承接后是否已有开工事实；工具只读且不新建开工通知单；拒单原因、审批依据和同一草稿延续仍需后续业务流程实现
- 验证证据：tests/test_quote_tools.py 覆盖有效承接/正式开工摘要和权限边界；开工草稿延续及拒单审批依据尚未验收
- 验收状态：NOT_VERIFIED

## 内部开工

Agent 开发开工依据、正式下达、业务状态、合同催补及财务交接；调用已有执行能力前校验开工条件

### FR-020

客户工艺方案确认并收到客户开工通知后，满足正式启动条件。项目负责人正式下达内部开工通知，通知设计、采购、生产、装配、财务等相关部门。承接确认、客户开工条件和内部正式下达分别留存依据。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_quote_acceptance_context 把承接确认、内部正式开工和销售合同作为独立上下文返回，避免把承接或合同误判为正式下达；正式下达、部门通知和客户开工条件校验仍需独立流程，不由本工具执行；query_internal_start_readiness 汇总项目状态、有效承接、正式开工通知、合同和计划上下文，区分承接确认、合同和内部正式下达；readiness.known_blockers/warnings/hints 只表达当前可见事实，不创建开工通知或发送部门任务
- 验证证据：tests/test_quote_tools.py 覆盖 has_effective_acceptance、has_formal_start、has_sales_contract 的独立派生状态；tests/test_start_tools.py 覆盖具备承接依据时可准备开工、已有正式开工时不重复准备；客户工艺方案确认和部门通知矩阵尚未验收
- 验收状态：NOT_VERIFIED

### FR-021

正式下达前允许匹配数据、准备草稿和项目计划草案，并按业务需要开展开工前的工艺评估及客户确认；不得下达或执行生产、采购、装配、试模任务。正式下达后，部门任务仍应按项目计划审批结果执行。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_internal_start_readiness 在项目仍为 DRAFT 时只提示可准备开工申请，不下达采购、生产、装配、试模任务；工具返回计划上下文并提示正式开工后仍须按项目计划审批结果执行
- 验证证据：tests/test_start_tools.py 验证工具只读核对与 can_prepare_start_from_known_facts；开工前草稿准备和正式任务门禁全流程尚未验收
- 验收状态：NOT_VERIFIED

### FR-022

内部开工业务状态为：待承接确认→已承接待开工条件→待正式下达→已正式下达→待计划审批→执行中。中标接收、匹配等处理动作另留记录；拒单记录原因后结束，暂停和终止按第12章管理。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_internal_start_readiness 返回 project_status、有效承接、有效拒单、有效开工和开放开工申请，帮助会话判断待承接/待开工/已开工状态；暂停、终止和拒单场景通过 blocker/warning 提示，不自动推进状态
- 验证证据：tests/test_start_tools.py 覆盖有效承接、有效开工和多候选；完整内部开工业务状态机仍未验收
- 验收状态：NOT_VERIFIED

### FR-023

内部通知关联外部订单、客户及内部模具、机型或物料号、项目、合同、开工时间和交期。新模流程生成或确认内部唯一模具号，已有模具设变复用原号。内部通知与销售合同分别管理，合同未到不阻塞已满足条件的项目开工。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：正式开工条件核对同时展示开工通知、销售合同、整套委外合同和计划上下文，明确内部通知与销售合同分别管理；工具提示合同晚到不必然阻塞已满足条件的项目开工，但需保留依据和后续合同核对
- 验证证据：tests/test_start_tools.py 覆盖销售合同与开工条件同时返回；tests/test_contract_tools.py 覆盖合同上下文，外部订单/模具唯一号适配尚未验收
- 验收状态：NOT_VERIFIED

### FR-024

无合同时记录预计到达日期；合同收到后补充实际到达日期并上传附件。超过预计日期仍未收到时提醒财务和项目负责人，业务或市场人员跟踪补充及签订情况。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_internal_start_readiness 在未见销售合同时给出 warning，说明合同晚到需保留开工依据并后续补合同核对；query_contract_context 的 late_expected_contracts 可提示预计日期已过但合同记录未生效/关闭
- 验证证据：tests/test_start_tools.py 覆盖未见销售合同时的开工核对 warning；tests/test_contract_tools.py 覆盖晚到合同提示；合同附件上传和催补通知尚未验收
- 验收状态：NOT_VERIFIED

### FR-025

财务按确认方式取得BPM等来源的开工通知，核对订单和合同信息；客户付款节点单独维护，不仅保存备注。移模时间按客户签收时间记录，由财务人工维护；签收不等于质量验收通过。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：正式开工核对与合同上下文工具分开返回开工通知、合同和付款节点，避免把签收、合同或付款备注误判为财务确认；工具只读，不维护移模时间、不确认客户付款节点、不替代财务核对
- 验证证据：tests/test_start_tools.py 覆盖开工通知独立于承接；tests/test_contract_tools.py 覆盖付款节点只读展示，财务取得 BPM 开工通知和移模维护尚未验收
- 验收状态：NOT_VERIFIED

## 合同上传与管理

Agent 开发上传、版本、审核、业务关联、晚到差异及替代追加；历史 ERP 合同/收付款事实引用，禁止重复累计

### FR-026

合同应与中标、开工通知、项目、模具和客户订单建立关联，记录合同编号、签订日期、金额、交期、付款方式、付款节点和相关凭证。财务以确认后的合同数据作为对账及收付款条件依据。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_contract_context 按项目或合同线索汇总销售合同、付款节点、金额币种和合同上下文，供财务/业务核对；合同明细必须同时具备对应合同查询工具和业务权限；上下文工具不上传凭证、不确认收付款
- 验证证据：tests/test_contract_tools.py 覆盖合同号定位、付款节点汇总和合同金额合计；凭证上传、签订日期和正式财务对账尚未验收
- 验收状态：NOT_VERIFIED

### FR-027

海信合同按业务规则核对项目编号；海尔合同信息核对合同号，电子合同按订单编号关联。海尔在我方设计确认可承接后发送合同的情况予以支持。字段映射按客户适配，不混用各类编号。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_contract_context 可按合同号或项目线索定位当前可见项目并返回合同记录；字段含义仍以客户适配规则和人工核对为准；工具不把项目编号、订单编号和合同号混用；多候选要求用户指定项目 ID
- 验证证据：tests/test_contract_tools.py 覆盖合同号定位和多项目歧义；海信/海尔/电子合同客户规则适配尚未验收
- 验收状态：NOT_VERIFIED

### FR-028

电子合同与纸质合同最终均需形成可追溯电子资料，记录上传人、上传时间、审批记录和版本。正式流程由业务人员上传并提交业务主管审核；现有设计文员或项目负责人上传路径如需保留，应在适配清单明确。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：当前合同上下文只读取已登记合同事实和付款节点，明确不上传合同、不 OCR、不生成审批版本；Skill 指令要求合同上传、审核和版本仍走人工确认及审批流程
- 验证证据：tests/test_contract_tools.py 覆盖只读上下文；电子/纸质合同资料上传人、上传时间、附件版本和审核记录尚未验收
- 验收状态：NOT_VERIFIED

### FR-029

开工通知与销售合同为关联单据，不视为同一对象。合同晚到时按确认的开工依据执行，收到后核对原项目价格、交期和付款条件；差异转相关责任人确认，必要时联动计划及财务变更。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_contract_context 与 query_quote_acceptance_context 分别呈现合同、承接和正式开工上下文，避免把开工通知与销售合同视为同一对象；late_expected_contracts 标识预计日期已过但合同记录未生效/关闭的当前可见记录，仅提示核对，不自动改变状态
- 验证证据：tests/test_contract_tools.py 覆盖晚到合同提示；tests/test_quote_tools.py 覆盖承接、开工、合同独立派生状态
- 验收状态：NOT_VERIFIED

### FR-030

客户设变可能替换合同、追加合同或修改原合同。海信替换原合同与海尔保留原合同等不同情况按实际文件记录。区分当前有效合同内容、补充内容和历史版本，不以“只保留一个合同”删除历史资料。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_contract_context 返回 ContractDetail.replaces_id 形成的合同替代关系，并保留新旧合同记录供追溯；工具不删除历史合同，不把替代关系解释为财务有效金额已核定
- 验证证据：tests/test_contract_tools.py 覆盖 SC-NEW 替代 SC-OLD 的 replacement_links
- 验收状态：NOT_VERIFIED

### FR-031

保留原合同、变更版本及历史收付款，明确替代或追加关系。财务确认后更新有效应收应付，历史已收已付不因合同替换而丢失，不得在新旧合同中重复累计。查询合同号应能追溯原版本、变更内容和已收未收情况。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：合同上下文返回 replacement_links、合同合计和付款节点，帮助查询原版本、新版本及替代关系；历史收付款保留与有效应收应付更新仍需财务确认流程；当前工具不累计实际收付款
- 验证证据：tests/test_contract_tools.py 覆盖替代关系和付款节点读取；实际收付款关系及防重复累计尚未验收
- 验收状态：NOT_VERIFIED

### FR-032

销售合同或已确认开工依据中的收款条件，与整套委外采购合同付款条件对照，提示资金安排上的差异。提示不自动改变合同条款或代替采购、财务审批。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_contract_context 同一项目下分别返回销售合同和整套委外合同的付款节点与金额汇总，为后续资金安排差异核对提供上下文；工具只展示节点和金额，不自动比较差异、不改合同条款、不替代采购或财务审批
- 验证证据：tests/test_contract_tools.py 覆盖销售合同与整套委外合同同时返回及权限隔离；资金差异提示规则尚未完整验收
- 验收状态：NOT_VERIFIED

## 项目大节点与计划

Agent 开发大节点维护、部门确认、审批、依赖、日期、计划版本、影响调整及看板；引用 ERP 实际执行记录

### FR-033

正式启动后，项目部当天制定项目大节点计划，组织设计、采购、加工、装配、调试及品质等部门确认完成时间。可执行时按审批流程批准；不能按期完成时，由项目部重编并再次确认。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_project_plan_context 汇总项目有效计划、计划变更和任务依赖，可识别未完成计划变更申请和当前有效版本；具备计划变更 prepare 能力且权限满足时，query_project_plan_context 返回 workflow_options，并可读取当前有效 plan_change 作为变更基线；带资料模板的计划变更流程会标记 material_required；prepare_project_plan_change 要求使用查询返回的真实项目、项目版本、当前有效计划 previous_id 和任务清单生成会话提案，本人确认后才创建 plan_change 并提交 Agent BPM；当审批模板绑定资料模板时，prepare_project_plan_change 必须传入本人已确认且与模板匹配的 material_review_id，确认提交后由 submit_subject 冻结资料绑定和 material_data；计划变更仍走领域校验和审批生效规则；审批生效前不关闭原计划、不修改执行任务，不代替部门确认
- 验证证据：tests/test_plan_tools.py 覆盖有效计划分析、未完成计划变更权限边界、计划变更 Skill 查询返回有效 plan_change 与 workflow_options，以及计划变更 proposal 不写业务、确认后提交 BPM、delegated_auto 传递到 submit_subject；并覆盖资料模板流程缺少已确认核对包时阻断、带核对包确认后冻结为 material_binding；真实部门确认尚未验收
- 验收状态：NOT_VERIFIED

### FR-034

计划保存客户要求日期、内部计划日期及实际完成日期。最终交期结合合同或客户确认依据预留必要时间，由项目部负责；市场跟踪合同，财务确认收款节点并跟踪回款。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：项目计划上下文返回任务计划开始/结束、状态、实际开始/结束字段和客户承诺交期风险；profile.customer_due_date 与计划节点分开展示，不把内部计划顺延直接改为客户承诺交期
- 验证证据：tests/test_plan_tools.py 覆盖计划结束晚于客户承诺日期的风险识别；市场合同跟踪和财务回款节点尚未验收
- 验收状态：NOT_VERIFIED

### FR-035

现有业务以55天作为一套模具项目周期的参考，由项目负责人结合线下评估填写，不作为全部模具固定承诺，也不等同于人员计费工时。自然日或工作日、节假日和各类模具周期模板在适配阶段确认。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_project_plan_context 的 limitations 明确 55 天周期、自然日/工作日、节假日和周期模板仍须适配确认，不能作为固定承诺；大节点覆盖仅按当前计划任务事实辅助核对
- 验证证据：tests/test_plan_tools.py 覆盖计划任务事实分析；周期模板和日历适配尚未验收
- 验收状态：NOT_VERIFIED

### FR-036

大节点至少覆盖设计工艺分析、结构设计及出图，原材料、五金和委外采购，工序加工，装配，试模及最终交付。开工、试模完成及出库等客户节点应形成提醒或待办，由项目负责人核对维护。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：analysis.milestone_coverage 按任务名称/标识辅助核对设计、采购、加工、装配、试模、交付等大节点覆盖和缺口；对话依据展示新增“项目大节点 / 计划任务表”，列出节点、状态、计划日期和前置依赖
- 验证证据：tests/test_plan_tools.py 覆盖大节点缺口识别；web/src/components/BusinessFacts.vue 对 analysis.tasks 渲染计划任务表
- 验收状态：NOT_VERIFIED

### FR-037

部门任务关联项目、模具和对应成果或工单，保存负责人、计划时间、实际时间、状态和完成依据。人员设备排班方式及细分任务粒度在适配阶段确定，任务完成记录应支持进度和实际工时追溯。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：项目计划上下文返回任务负责人、计划时间、实际时间、状态和依赖，供进度与实际执行核对；工具不登记实际执行、不计算人员设备排班或工时
- 验证证据：tests/test_plan_tools.py 覆盖运行中、已完成和计划中任务分析；实际工时追溯和 ERP 工单成果关联尚未验收
- 验收状态：NOT_VERIFIED

### FR-038

装配可在零件齐套达到设定条件时启动，现有参考阈值为70%至80%，允许配置。比例口径、关键件条件及统计范围须确认；不得仅凭总体百分比认定所有装配前置条件满足。试模必须在对应装配任务完成后正式开展。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：项目计划上下文能展示装配和试模节点及其前置依赖，辅助核对试模是否等待装配完成；limitations 明确齐套率、关键件条件和统计范围不能由本工具默认认定
- 验证证据：tests/test_plan_tools.py 覆盖依赖阻塞任务；装配齐套率和关键件口径尚未验收
- 验收状态：NOT_VERIFIED

### FR-039

异常先评估影响。不改变已批准计划的普通异常只记录问题、处理结果和实际耗时，不强制重排；影响节点、交期或跨部门协同的，由项目负责人组织调整并按审批结果执行。是否合并多项异常统一处理由项目负责人确定。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_project_plan_context 区分有效计划、开放计划变更和逾期/依赖阻塞节点，供异常是否需要调整计划时核对；prepare_project_plan_change 可把影响节点的调整方案封装为本人确认后的 plan_change BPM 提案；普通异常记录、处理结果、实际耗时和是否合并异常仍由工程联络/异常流程处理
- 验证证据：tests/test_plan_tools.py 覆盖 open_plan_changes、逾期节点分析和计划变更 proposal 确认链路；异常来源到计划变更的完整部门协同仍未验收
- 验收状态：NOT_VERIFIED

### FR-040

节点调整记录原计划、新计划、原因、影响范围及审批附件。内部部门提出调整后通知项目负责人，由其协调并通知受影响部门；客户变更由项目负责人组织传达。不得只留延期说明而不更新相应计划及任务。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：计划上下文返回计划变更记录和原计划 previous_id 明细，帮助查询调整记录和影响范围；prepare_project_plan_change 的预览显示原计划、新计划、原因、新增/删除/变更节点，并在本人确认后提交 plan_change BPM；计划变更 proposal 支持 material_review_id；资料模板流程会把已确认核对包、file_sha256、review_hash 和 material_data 冻结进审批快照，作为节点调整审批附件依据；审批生效前不更新相应计划及任务；审批生效后 plan.change.effective 事件按受影响任务通知新旧节点负责人；部门确认矩阵和更完整的受影响部门通知规则仍待补齐
- 验证证据：tests/test_plan_tools.py 覆盖计划变更权限隔离、确认后提交 BPM、资料核对包冻结为审批附件依据，以及生效后 Outbox 通知受影响任务负责人；完整部门通知尚未验收
- 验收状态：NOT_VERIFIED

### FR-041

内部计划顺延不直接修改客户承诺交期。涉及客户交期变化时记录客户确认依据，未确认的标记交期风险；跨部门节点按依赖和影响调整，不能无依据地将全部任务等量顺延。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：客户承诺日期与内部计划节点分开返回，并用 customer_due_risk_tasks 标识晚于客户承诺日期的未完成节点；工具不无依据等量顺延全部任务，也不修改客户承诺交期
- 验证证据：tests/test_plan_tools.py 覆盖客户交期风险任务识别；客户确认依据和跨部门调整执行尚未验收
- 验收状态：NOT_VERIFIED

### FR-042

计划应支持查看原计划、调整记录和实际进度，二维表、甘特图及进度看板的具体样式和计算规则后续适配。暂停恢复按第12章的整体暂停规则执行，不与普通节点调整混用。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_project_plan_context 支持查看有效计划、计划变更、实际进度和依赖阻塞；暂停恢复仍由 project_pause_resume 独立工具处理；对话中新增计划任务表格依据展示，但甘特图和完整进度看板样式仍待适配
- 验证证据：tests/test_plan_tools.py 覆盖有效计划与计划变更查询；web/src/components/BusinessFacts.vue 表格渲染通过前端构建验证
- 验收状态：NOT_VERIFIED

## 设计与成果协同

Agent 开发设计审批、资料协同及工程联络单关联；设计上传/BOM 等已登记业务能力经核对调用 ERP

### FR-043

设计主管确认内部设计或设计委外并提交审批，记录负责人、时间、费用及适用的供应商信息。当前设计排产以线下安排、线上进度记录为基础；供应商不适用于内部设计时不强制虚填。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_design_route_context 只读工具按项目、设计单、图纸版本、BOM物料、计划任务或工程联络线索核对设计/BOM/加工路线语境；design_route_context_review Skill 要求模型先查询真实设计路线证据，不生成图纸、不上传成果、不替代ERP设计/BOM登记；工具返回 route_summary、linked_plan_tasks、engineering_contact_impacts、warnings 和 derived_status，区分无生效设计、未完成审批、路线未关联计划和工程联络影响；工具在缺少项目计划或工程联络查询能力时写入 limitations，不通过设计上下文泄露隐藏计划任务或联络标题
- 验证证据：tests/test_design_tools.py 覆盖生效设计BOM路线、计划任务和工程联络影响聚合；tests/test_design_tools.py 覆盖无计划/联络工具时权限隔离，不泄露隐藏任务和联络标题；tests/test_design_tools.py 覆盖多项目候选要求指定对象，以及无生效设计版本的 warning
- 验收状态：NOT_VERIFIED

### FR-044

内部设计按工艺分析、结构设计、出图、设计确认推进。设计委外记录任务下达、成果接收、审核及整改结果，由设计主管组织排期和确认。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_design_route_context 只读工具按项目、设计单、图纸版本、BOM物料、计划任务或工程联络线索核对设计/BOM/加工路线语境；design_route_context_review Skill 要求模型先查询真实设计路线证据，不生成图纸、不上传成果、不替代ERP设计/BOM登记；工具返回 route_summary、linked_plan_tasks、engineering_contact_impacts、warnings 和 derived_status，区分无生效设计、未完成审批、路线未关联计划和工程联络影响；工具在缺少项目计划或工程联络查询能力时写入 limitations，不通过设计上下文泄露隐藏计划任务或联络标题
- 验证证据：tests/test_design_tools.py 覆盖生效设计BOM路线、计划任务和工程联络影响聚合；tests/test_design_tools.py 覆盖无计划/联络工具时权限隔离，不泄露隐藏任务和联络标题；tests/test_design_tools.py 覆盖多项目候选要求指定对象，以及无生效设计版本的 warning
- 验收状态：NOT_VERIFIED

### FR-045

设计确认后形成正式设计版本，管理对应BOM、工艺路线、零件清单及后续任务；设计人员上传需采购物料、零件和加工任务清单。成果产生方式按适配确认，正式版本的项目、模具和任务关联必须保留。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_design_route_context 只读工具按项目、设计单、图纸版本、BOM物料、计划任务或工程联络线索核对设计/BOM/加工路线语境；design_route_context_review Skill 要求模型先查询真实设计路线证据，不生成图纸、不上传成果、不替代ERP设计/BOM登记；工具返回 route_summary、linked_plan_tasks、engineering_contact_impacts、warnings 和 derived_status，区分无生效设计、未完成审批、路线未关联计划和工程联络影响；工具在缺少项目计划或工程联络查询能力时写入 limitations，不通过设计上下文泄露隐藏计划任务或联络标题
- 验证证据：tests/test_design_tools.py 覆盖生效设计BOM路线、计划任务和工程联络影响聚合；tests/test_design_tools.py 覆盖无计划/联络工具时权限隔离，不泄露隐藏任务和联络标题；tests/test_design_tools.py 覆盖多项目候选要求指定对象，以及无生效设计版本的 warning
- 验收状态：NOT_VERIFIED

### FR-046

图纸或工艺路线改版保留旧版，评估对采购、加工和其他任务的影响。工程联络单转设计时创建或关联设计订单，继承客户、项目、模具、料号、责任、紧急程度、方案、要求日期及附件审批信息。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_design_route_context 只读工具按项目、设计单、图纸版本、BOM物料、计划任务或工程联络线索核对设计/BOM/加工路线语境；design_route_context_review Skill 要求模型先查询真实设计路线证据，不生成图纸、不上传成果、不替代ERP设计/BOM登记；工具返回 route_summary、linked_plan_tasks、engineering_contact_impacts、warnings 和 derived_status，区分无生效设计、未完成审批、路线未关联计划和工程联络影响；工具在缺少项目计划或工程联络查询能力时写入 limitations，不通过设计上下文泄露隐藏计划任务或联络标题
- 验证证据：tests/test_design_tools.py 覆盖生效设计BOM路线、计划任务和工程联络影响聚合；tests/test_design_tools.py 覆盖无计划/联络工具时权限隔离，不泄露隐藏任务和联络标题；tests/test_design_tools.py 覆盖多项目候选要求指定对象，以及无生效设计版本的 warning
- 验收状态：NOT_VERIFIED

### FR-047

工艺分析后向采购提供试模料标准，包括客户、规格和颜色。发货地点、单独招标及无合同而线下确认的信息可通过记录表及附件维护，并保留来源。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_design_route_context 只读工具按项目、设计单、图纸版本、BOM物料、计划任务或工程联络线索核对设计/BOM/加工路线语境；design_route_context_review Skill 要求模型先查询真实设计路线证据，不生成图纸、不上传成果、不替代ERP设计/BOM登记；工具返回 route_summary、linked_plan_tasks、engineering_contact_impacts、warnings 和 derived_status，区分无生效设计、未完成审批、路线未关联计划和工程联络影响；工具在缺少项目计划或工程联络查询能力时写入 limitations，不通过设计上下文泄露隐藏计划任务或联络标题
- 验证证据：tests/test_design_tools.py 覆盖生效设计BOM路线、计划任务和工程联络影响聚合；tests/test_design_tools.py 覆盖无计划/联络工具时权限隔离，不泄露隐藏任务和联络标题；tests/test_design_tools.py 覆盖多项目候选要求指定对象，以及无生效设计版本的 warning
- 验收状态：NOT_VERIFIED

## 采购与价格

Agent 开发辅材、办公用品、试模料新增需求及全部适用审批；原材/五金/委外和已有下单/拆单动作调用 ERP

### FR-048

辅料和刀具先建正式料品档案，包含料号、分类、名称、规格型号、单位、库存方式、默认供应商、参考价格及启停状态。普通采购必须引用正式料品，不长期使用临时名称代替料号；分类和编码规则支持维护。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_procurement_price_context 只读工具按项目、料号、价格单、供应商、采购申请或订单线索核对料品、采购价格、设计采购需求和订单跟踪上下文；procurement_price_context_review Skill 要求模型先查询真实采购价格和订单证据，不把历史报价、草稿价格或聊天记录说成可直接下单依据；工具返回 effective_prices、open_price_reviews、design_procurement_needs_without_visible_price、order_tracking、warnings 和 derived_status，区分无有效采购价、未完成价格审批、未匹配价格、未完全发货和供应商异常；工具在缺少设计路线、采购申请或正式订单能力时写入 limitations，不通过采购价格上下文泄露隐藏订单号或采购申请
- 验证证据：tests/test_procurement_tools.py 覆盖有效价格、设计采购需求、采购申请、正式订单、发货、收货、检验和异常聚合；tests/test_procurement_tools.py 覆盖无订单/采购申请工具时权限隔离，不泄露隐藏订单号；tests/test_procurement_tools.py 覆盖多候选要求指定对象，以及无有效价格的 warning
- 验收状态：NOT_VERIFIED

### FR-049

管理按料号的通用价目表、按材质分类的分类价目表及按供应商与料号的专用价目表。价格新增、修改和停用须审批；多个价格同时适用时的优先级、有效期和税价口径在适配中确认。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_procurement_price_context 只读工具按项目、料号、价格单、供应商、采购申请或订单线索核对料品、采购价格、设计采购需求和订单跟踪上下文；procurement_price_context_review Skill 要求模型先查询真实采购价格和订单证据，不把历史报价、草稿价格或聊天记录说成可直接下单依据；工具返回 effective_prices、open_price_reviews、design_procurement_needs_without_visible_price、order_tracking、warnings 和 derived_status，区分无有效采购价、未完成价格审批、未匹配价格、未完全发货和供应商异常；工具在缺少设计路线、采购申请或正式订单能力时写入 limitations，不通过采购价格上下文泄露隐藏订单号或采购申请
- 验证证据：tests/test_procurement_tools.py 覆盖有效价格、设计采购需求、采购申请、正式订单、发货、收货、检验和异常聚合；tests/test_procurement_tools.py 覆盖无订单/采购申请工具时权限隔离，不泄露隐藏订单号；tests/test_procurement_tools.py 覆盖多候选要求指定对象，以及无有效价格的 warning
- 验收状态：NOT_VERIFIED

### FR-050

已有审批有效价格时带出对应依据；无价格或不适用时由采购询价、比价、议价，人工上传报价单、邮件或聊天记录后审批。历史报价只作参考，不直接替代有效采购价格。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_procurement_price_context 只读工具按项目、料号、价格单、供应商、采购申请或订单线索核对料品、采购价格、设计采购需求和订单跟踪上下文；procurement_price_context_review Skill 要求模型先查询真实采购价格和订单证据，不把历史报价、草稿价格或聊天记录说成可直接下单依据；工具返回 effective_prices、open_price_reviews、design_procurement_needs_without_visible_price、order_tracking、warnings 和 derived_status，区分无有效采购价、未完成价格审批、未匹配价格、未完全发货和供应商异常；工具在缺少设计路线、采购申请或正式订单能力时写入 limitations，不通过采购价格上下文泄露隐藏订单号或采购申请
- 验证证据：tests/test_procurement_tools.py 覆盖有效价格、设计采购需求、采购申请、正式订单、发货、收货、检验和异常聚合；tests/test_procurement_tools.py 覆盖无订单/采购申请工具时权限隔离，不泄露隐藏订单号；tests/test_procurement_tools.py 覆盖多候选要求指定对象，以及无有效价格的 warning
- 验收状态：NOT_VERIFIED

### FR-051

采购区分设计委外、原材料及五金零件、工序委外、组装、试模、整套委外、辅料和刀具等类型，按对应流程执行。供应商信息及负责采购岗位按类别维护。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_procurement_price_context 只读工具按项目、料号、价格单、供应商、采购申请或订单线索核对料品、采购价格、设计采购需求和订单跟踪上下文；procurement_price_context_review Skill 要求模型先查询真实采购价格和订单证据，不把历史报价、草稿价格或聊天记录说成可直接下单依据；工具返回 effective_prices、open_price_reviews、design_procurement_needs_without_visible_price、order_tracking、warnings 和 derived_status，区分无有效采购价、未完成价格审批、未匹配价格、未完全发货和供应商异常；工具在缺少设计路线、采购申请或正式订单能力时写入 limitations，不通过采购价格上下文泄露隐藏订单号或采购申请
- 验证证据：tests/test_procurement_tools.py 覆盖有效价格、设计采购需求、采购申请、正式订单、发货、收货、检验和异常聚合；tests/test_procurement_tools.py 覆盖无订单/采购申请工具时权限隔离，不泄露隐藏订单号；tests/test_procurement_tools.py 覆盖多候选要求指定对象，以及无有效价格的 warning
- 验收状态：NOT_VERIFIED

### FR-052

普通采购从料品档案发起。资产采购建立电子合同台账，按约定开票和付款，由采购办理、财务按业务要求盖章确认；是否扩展为资产管理功能在适配中明确。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_procurement_price_context 只读工具按项目、料号、价格单、供应商、采购申请或订单线索核对料品、采购价格、设计采购需求和订单跟踪上下文；procurement_price_context_review Skill 要求模型先查询真实采购价格和订单证据，不把历史报价、草稿价格或聊天记录说成可直接下单依据；工具返回 effective_prices、open_price_reviews、design_procurement_needs_without_visible_price、order_tracking、warnings 和 derived_status，区分无有效采购价、未完成价格审批、未匹配价格、未完全发货和供应商异常；工具在缺少设计路线、采购申请或正式订单能力时写入 limitations，不通过采购价格上下文泄露隐藏订单号或采购申请
- 验证证据：tests/test_procurement_tools.py 覆盖有效价格、设计采购需求、采购申请、正式订单、发货、收货、检验和异常聚合；tests/test_procurement_tools.py 覆盖无订单/采购申请工具时权限隔离，不泄露隐藏订单号；tests/test_procurement_tools.py 覆盖多候选要求指定对象，以及无有效价格的 warning
- 验收状态：NOT_VERIFIED

### FR-053

试模料由设计与项目并行确认是否需要额外采购及数量。客户提供满足需求的试模料时不重复采购；需额外采购时按确认需求办理，由对应冲压采购岗位跟进供应商。客户供料情况和采购判断应保留。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_procurement_price_context 只读工具按项目、料号、价格单、供应商、采购申请或订单线索核对料品、采购价格、设计采购需求和订单跟踪上下文；procurement_price_context_review Skill 要求模型先查询真实采购价格和订单证据，不把历史报价、草稿价格或聊天记录说成可直接下单依据；工具返回 effective_prices、open_price_reviews、design_procurement_needs_without_visible_price、order_tracking、warnings 和 derived_status，区分无有效采购价、未完成价格审批、未匹配价格、未完全发货和供应商异常；工具在缺少设计路线、采购申请或正式订单能力时写入 limitations，不通过采购价格上下文泄露隐藏订单号或采购申请
- 验证证据：tests/test_procurement_tools.py 覆盖有效价格、设计采购需求、采购申请、正式订单、发货、收货、检验和异常聚合；tests/test_procurement_tools.py 覆盖无订单/采购申请工具时权限隔离，不泄露隐藏订单号；tests/test_procurement_tools.py 覆盖多候选要求指定对象，以及无有效价格的 warning
- 验收状态：NOT_VERIFIED

### FR-054

下单后跟踪供应商生产、发货、到货、收货、检验和入库状态及凭证。不合格时按确认结果安排整改、退换货、扣款或重新交付，并关联原订单及工程联络单。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_procurement_price_context 只读工具按项目、料号、价格单、供应商、采购申请或订单线索核对料品、采购价格、设计采购需求和订单跟踪上下文；procurement_price_context_review Skill 要求模型先查询真实采购价格和订单证据，不把历史报价、草稿价格或聊天记录说成可直接下单依据；工具返回 effective_prices、open_price_reviews、design_procurement_needs_without_visible_price、order_tracking、warnings 和 derived_status，区分无有效采购价、未完成价格审批、未匹配价格、未完全发货和供应商异常；工具在缺少设计路线、采购申请或正式订单能力时写入 limitations，不通过采购价格上下文泄露隐藏订单号或采购申请
- 验证证据：tests/test_procurement_tools.py 覆盖有效价格、设计采购需求、采购申请、正式订单、发货、收货、检验和异常聚合；tests/test_procurement_tools.py 覆盖无订单/采购申请工具时权限隔离，不泄露隐藏订单号；tests/test_procurement_tools.py 覆盖多候选要求指定对象，以及无有效价格的 warning
- 验收状态：NOT_VERIFIED

## 整套委外合同与付款条件

Agent 开发合同草稿/审批、付款条件与防重申请；引用既有合同和执行事实，禁止以审批代替支付

### FR-055

整套委外采购合同可由模板生成草稿，带入项目、模具、供应商、金额、付款及交付要求，采购主管核对后提交总经理审批，再按确认方式签订。模板、客户资料及相关历史依据应与合同关联。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_contract_context 可读取整套委外合同、金额和付款节点，作为采购主管核对合同草稿/审批上下文的一部分；工具不生成合同草稿、不签订合同、不关联模板或客户资料附件
- 验证证据：tests/test_contract_tools.py 覆盖 full_outsource_contract 上下文读取；模板生成、采购主管提交和总经理审批尚未验收
- 验收状态：NOT_VERIFIED

### FR-056

供应商付款按合同对应阶段核验适用条件，包括交付、验收、发票、客户回款及累计已付款。预付款、进度款、验收款和尾款分别采用对应要求，不适用条件不作为阻断项；缺少适用资料时退回补齐，偏离约定时走特殊审批。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：整套委外合同付款节点通过 query_contract_context 返回，供应商付款申请仍由既有 supplier_payment 条件核验处理；上下文工具不把节点展示视为条件已满足或付款可执行
- 验证证据：tests/test_contract_tools.py 覆盖付款节点只读展示；tests/test_domains.py 已有供应商付款条件与预留相关测试，完整客户回款/发票/验收条件矩阵尚未验收
- 验收状态：NOT_VERIFIED

### FR-057

客户回款达到委外合同约定触发条件并经财务确认后，可生成对应供应商付款申请；其他付款节点按其适用合同条件发起。生成申请不等于付款获批或实际支付，同一触发依据不得重复生成有效申请。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：合同上下文展示整套委外合同付款节点，为后续根据适用条件生成付款申请提供查询依据；生成供应商付款申请、客户回款财务确认和同一触发依据防重复仍需独立流程
- 验证证据：tests/test_contract_tools.py 覆盖合同节点读取；付款申请生成与实际支付确认仍未验收
- 验收状态：NOT_VERIFIED

## 制造与质检

已有制造/检验/收货业务经审查调用 ERP；Agent 开发协同、审批、异常工程联络单及闭环验证

### FR-058

按项目计划及设备、人员安排工序任务，现场人员报工并记录任务进度、实际耗时和异常。工序完成进入适用的检验环节，后续流转依据检验及任务依赖条件执行。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_manufacturing_quality_context 按项目线索核对项目计划任务、加工/工序类任务、实际开始/完成日期、设计内部加工路线、装配/试模和工程联络异常上下文；工具将计划任务实际日期作为当前 Agent 可见报工事实，同时明确结构化工时、设备、人员班组和现场异常报工明细仍需专门业务记录；任务依赖和后续流转仍以项目计划、检验结论和对应业务回执为准，工具只读不创建工单、不登记报工、不确认检验
- 验证证据：tests/test_manufacturing_quality_tools.py 覆盖制造任务实际开工、未完工状态、制造上下文缺口和多候选不自动决定；完整 ERP 制造报工、工时设备人员明细和检验流转尚未验收
- 验收状态：NOT_VERIFIED

### FR-059

工序产品形成检测报告和问题记录。合格后进入下一适用工序或入库流程，完成所需工序检验后形成合格验收资料；不合格时发起工程联络单，记录原因、方案和状态，返回对应任务整改并复检。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_manufacturing_quality_context 返回 quality_or_rework_contacts 和 gaps，明确未见独立工序检测报告、合格验收资料或质检结论时，不能把计划任务完成等同于检验合格；工程联络异常、整改任务和试模未通过结果作为质量/返工上下文展示；工具不关闭问题、不认定整改验收
- 验证证据：tests/test_manufacturing_quality_tools.py 覆盖工程联络质量问题进入上下文、独立检测报告缺口和权限边界；真实检测报告、入库流转、不合格整改复检闭环尚未验收
- 验收状态：NOT_VERIFIED

### FR-060

当场发现问题应及时处理；后续才发现的问题应关联原任务及受影响成果，安排对应整改并保留追溯。处理方案提交、整改执行和复检确认分别记录，不能仅提交方案就认定问题关闭。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_manufacturing_quality_context 汇总工程联络问题、责任事项、实际完成时间、工时和执行证据字段，提醒方案审批、整改执行和复检确认必须分别核对；工具把未关闭工程联络或返工事项列为 warning，不因存在方案或任务进度就认定问题关闭
- 验证证据：tests/test_manufacturing_quality_tools.py 覆盖未关闭质量/返工联络事项派生状态；后续发现问题的原任务/成果关联、处理方案审批、整改执行和独立复检完整验收尚未完成
- 验收状态：NOT_VERIFIED

### FR-061

内部加工项目可包含局部委外。原材料发给零件或工序供应商时记录交接及责任依据，收货由仓库签收并通知采购，按要求入库检验。内部、局部委外成果分别按适用审核或验收规则管理。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_manufacturing_quality_context 区分设计BOM中的 INTERNAL、PURCHASE、OUTSOURCE 路线；存在采购或委外路线时提示内部制造结论须结合局部委外交接、收货和检验依据；设计路线和物料仅在用户具备设计路线工具/权限时返回，未授权时不会通过制造上下文泄露物料编号或 BOM 路线
- 验证证据：tests/test_manufacturing_quality_tools.py 覆盖内部加工路线读取、无设计权限不泄露物料/BOM；原材料发给工序供应商、仓库签收、采购通知和入库检验联调尚未验收
- 验收状态：NOT_VERIFIED

## 装配与试模

实际装配/试模能力复用 ERP；Agent 开发申请审批、业务前置、资源协同及异常处理，仅设钳工主管

### FR-062

系统显示适用的齐套进度，钳工主管确认装配条件后分配任务并下达装配工单。装配完成后确认完工，无异常由钳工主管发起试模申请；有异常关联质检及工程联络单，定位责任任务处理。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_assembly_trial_context 按项目、装配任务或试模线索核对计划节点、设计BOM路线、装配任务下发、装配开工/完工、试模申请、试模结果和工程联络异常上下文；analysis.derived_status 区分 has_assembly_order、has_assembly_started、has_assembly_done、has_trial_request、has_trial_result、has_open_assembly_or_trial_issue，避免把装配完成误判为试模完成或异常关闭；工具 limitations 明确实际装配/试模执行复用 ERP 或正式业务回执，Agent 不下达装配工单、不登记开完工、不修改 ERP 执行数据
- 验证证据：tests/test_assembly_trial_tools.py 覆盖装配计划节点、设计BOM路线、装配完工、试模未通过、工程联络异常聚合；真实 ERP 齐套率、关键件口径、钳工主管确认和装配工单联调尚未验收
- 验收状态：NOT_VERIFIED

### FR-063

试模安排确认机台租赁或内部资源及计划可用性，由试模主管分配任务。试模人员记录执行结果并上传报告；通过后形成出厂自检合格资料，不通过时发起工程联络单，返回对应任务整改后重新验证。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_assembly_trial_context 返回试模 planned_date、location、acceptance_criteria、responsible_id、results，并在 analysis 中区分 has_trial_request、has_trial_result、has_trial_passed、has_trial_failed；试模未通过时 warnings 提醒需关联工程联络单、整改责任任务和重新验证依据；试模通过时提醒不等于客户验收、出厂放行或项目关闭；试模记录仅在当前用户具备 trial_request.read 授权时通过同一上下文工具读取，未授权时不泄露试模单号、报告或结论
- 验证证据：tests/test_assembly_trial_tools.py 覆盖试模结果读取、试模未通过整改提示、无 trial_request.read 时不泄露 TRIAL-SECRET 或 SECRET-TRIAL-REPORT；机台租赁/内部资源可用性、试模报告附件解析和出厂自检资料联调尚未验收
- 验收状态：NOT_VERIFIED

## 交付与物流

Agent 开发发货车辆、物流信息维护、物流报价审批及验收协同；既有出入库/发货执行经审查复用 ERP

### FR-064

对模具、零件及出厂件按要求进行质量检测和出厂验收，保存结果与确认人。满足组装、检验和验收条件后办理入库、出库及发货记录，不能将工序合格直接等同于整套模具交付合格。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_delivery_logistics_context 按项目、发货、物流、签收或验收线索核对交付计划节点、正式订单发货、仓库收货、入库检验、库存移动、试模结果、结项清单和工程联络异常上下文；analysis.derived_status 区分 has_supplier_shipment、has_goods_receipt、has_receipt_inspection、has_stock_out_movement、has_trial_passed、has_customer_signature、has_customer_acceptance，防止把工序合格、供应商发货或试模通过等同于整套模具交付合格；工具 limitations 明确采购发货、仓库收货、入库检验、出库、客户签收和客户验收是不同事实，不能相互替代
- 验证证据：tests/test_delivery_logistics_tools.py 覆盖供应商发货、仓库收货、入库检验、出库移动、试模通过、客户验收清单和质量联络异常聚合；真实出厂件检测、ERP 出入库/发货执行和客户验收联调尚未验收
- 验收状态：NOT_VERIFIED

### FR-065

固定物流路线维护出发地、接收地、承运商、车型或运输方式、计价单位、含税方式和有效期。现有固定路线主要用于冲压业务；模具物流按实际路线处理，两类业务均保存地点、重量、车型及历史价格。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：新增 logistics_route / logistics_quote PostgreSQL 模型与迁移，保存项目/全局路线、出发地、接收地、承运商、车型/运输方式、计价单位、含税方式、报价有效期和审批证据；query_delivery_logistics_context 返回 analysis.logistics_pricing，区分有效路线报价、过期报价、待审批报价和缺口，没有结构化路线或有效报价时不编造路线、承运商、车型或价格
- 验证证据：tests/test_delivery_logistics_tools.py 通过 PostgreSQL moldpilot_test 覆盖无结构化物流路线时返回缺口，以及有效路线报价返回承运商、车型、计价单位、含税方式和有效期；路线/报价正式维护入口、地点/重量/历史价格完整维护和真实业务验收仍未完成
- 验收状态：NOT_VERIFIED

### FR-066

仓库确认路线后匹配审批有效价格并形成物流费用。无固定路线或未匹配有效价格时，由采购主管询比议价并审批，生成本次结算价格及对账依据。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：logistics_quote 可绑定 settlement_for_project_id 作为项目本次结算价候选；query_delivery_logistics_context 在 analysis.logistics_pricing.settlement_price_candidates 返回匹配项目的有效结算价，并用 has_project_logistics_settlement_price 区分“已有有效报价”和“已有本项目结算价”；仓库收货、检验和供应商发货不会被推断为已形成物流费用
- 验证证据：tests/test_delivery_logistics_tools.py 通过 PostgreSQL moldpilot_test 覆盖有效路线报价和项目结算价候选；仓库确认路线、采购主管询比议价审批、费用对账依据和财务结算联动尚未正式实现/验收
- 验收状态：NOT_VERIFIED

### FR-067

物流报价保存有效期，现有业务约定最长半年，期满或价格变化后重新维护并按需要多家比价。具体期限作为适配参数确认。发货时间、物流单、费用及对应项目模具应可追溯。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_delivery_logistics_context 结果包含供应商发货 reference、仓库收货 reference、库存移动 source_key 和对应项目上下文；新增 logistics_quote.valid_from / valid_to 并在 analysis.logistics_pricing 中返回有效、过期和待审批报价，保留本次结算价候选与项目模具追溯关系
- 验证证据：tests/test_delivery_logistics_tools.py 通过 PostgreSQL moldpilot_test 覆盖发货、收货、出库移动追溯、有效报价期和项目结算价候选；半年上限参数、多家比价、物流费用对账、客户签收和 ERP/财务联调尚未正式实现/验收
- 验收状态：NOT_VERIFIED

### FR-068

发货后记录客户签收和客户验收结果，两者分别确认。验收结果作为适用的回款及归档依据；签收日期按移模业务规则维护，不自动认定质量验收通过。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：新增 customer_delivery_signature / customer_acceptance_record PostgreSQL 模型与迁移，分别保存客户签收/移模签收、客户质量验收、复验、责任判断、整改期限和证据；query_delivery_logistics_context 返回 analysis.customer_delivery_acceptance，并将 has_customer_signature 与 has_customer_acceptance 分开，签收不会自动推断为客户验收通过；项目关闭/结项清单中的 CUSTOMER_ACCEPTANCE 仍仅作为客户验收依据之一展示
- 验证证据：tests/test_delivery_logistics_tools.py 通过 PostgreSQL moldpilot_test 覆盖客户签收与客户验收分离、签收后未验收仍返回缺口，以及普通仓库视角不泄露客户验收失败原因和扣款金额；真实签收日期、移模业务规则和回款归档联动尚未验收
- 验收状态：NOT_VERIFIED

### FR-069

客户验收不通过时记录问题、证据、责任判断和处理期限，关联工程联络单或供应商整改任务。整改后安排复验并记录最终结果，涉及费用、扣款、交期和合同变化时同步对应记录。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：customer_acceptance_record 保存客户验收不通过的问题描述、责任判断、整改期限、关联工程联络单、供应商、扣款金额、交期影响天数和合同变化要求；query_delivery_logistics_context 在验收失败且无复验通过时提示不能认定闭环完成，扣款、合同变化和交期影响分别写入 warnings，并继续汇总质量、交付、物流或验收相关工程联络事项
- 验证证据：tests/test_delivery_logistics_tools.py 通过 PostgreSQL moldpilot_test 覆盖客户验收失败、扣款金额、合同变化、交期影响和未复验通过警示；真实供应商整改任务、财务扣款、计划/合同变化同步和 ERP/财务联调尚未正式验收
- 验收状态：NOT_VERIFIED

## 整套委外协同

Agent 开发加工方式控制、节点协同、审批、异常及结算衔接；已有委外业务执行复用 ERP，不调用旧异常/审批流程

### FR-070

按中标承接确认及后续审批变更后的有效加工方式进入整套委外路径，不重复下达整套内部制造任务。原报价中的委外价格、周期和要求作为评估参考及合同核对依据。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_full_outsource_context 读取项目档案 execution_mode、有效 quote_acceptance 承接决定和 full_outsource_contract 上下文，derived_status.has_full_outsource_mode 区分整套委外路径依据；工具 limitations 明确报价委外金额、整套委外合同、供应商节点上报、我方收货、客户验收、扣款和结算是不同事实，不能相互替代
- 验证证据：tests/test_full_outsource_tools.py 覆盖有效整套委外加工方式、合同与委外计划节点聚合；不重复下达内部制造任务和真实 ERP 加工方式变更审批尚未验收
- 验收状态：NOT_VERIFIED

### FR-071

项目部跟踪供应商设计、采购、生产、质检、装配、试模、验收等适用节点；供应商上报，采购跟进并同步项目。具体节点、填报频率和证据模板后续适配；供应商是否登录系统另行确认，不默认必须具备供应商门户。

- 最新口径：不开发供应商门户；保留授权人员录入/导入上报证据和采购、项目协同。
- 实现证据：新增 supplier_progress_report PostgreSQL 模型与迁移，保存授权人员录入/导入的供应商阶段上报、状态、进度百分比、下次跟进日期、问题摘要、证据和采购跟进人；query_full_outsource_context 汇总项目计划/计划变更中供应商、委外、质检、装配、试模、验收、交付等节点，并新增 analysis.supplier_progress_reports，把供应商节点上报与订单/发货/收货事实分开展示；full_outsource_review Skill 明确不默认供应商门户，当前支持授权人员录入/导入上报证据和采购、项目协同口径
- 验证证据：tests/test_full_outsource_tools.py 通过 PostgreSQL moldpilot_test 覆盖供应商生产质检装配试模验收节点、供应商进度上报、风险/阻塞、逾期跟进、供应商发货与我方收货聚合；供应商填报频率、证据模板、供应商侧接入和真实协同流程尚未适配验收
- 验收状态：NOT_VERIFIED

### FR-072

供应商负责合同约定的生产与整改，我方负责相应的合同、节点、交付及验收结果核验和异常跟进。设计或项目人员上传客户资料，采购按合同和业务需要向供应商提供获准资料，并保留交接依据。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_full_outsource_context 返回已生效整套委外合同、供应商发货/收货/检验、工程联络质量延期问题、设变整改影响和客户验收/关闭清单上下文；analysis.warnings 在未关闭委外质量、延期、验收或扣款相关事项存在时阻止认定异常闭环
- 验证证据：tests/test_full_outsource_tools.py 覆盖供应商收货检验不合格、工程联络整改扣款线索和客户验收清单；客户资料交接依据、采购向供应商提供资料留痕和真实合同节点尚未验收
- 验收状态：NOT_VERIFIED

### FR-073

采购合同按模板、审批和签订流程办理，供应商在线签署属于原需求中的目标能力，其具体服务及签署方式须确认。未确定电子签署接入前，不将草稿自动生成等同于合同已签署。

- 最新口径：在线电子签署已由用户取消；保留模板、人工审核签订及签署文件上传。
- 实现证据：query_full_outsource_context 读取 full_outsource_contract 的状态、合同号、金额、供应商、阶段数和生效状态；合同草稿、模板生成或报价依据不会被认定为已签署合同；full_outsource_review Skill 记录用户已取消在线电子签署，保留模板、人工审核签订及签署文件上传口径
- 验证证据：tests/test_full_outsource_tools.py 覆盖已生效委外合同上下文；合同模板生成、人工签署文件上传、审批签订流程和正式合同附件管理尚未完整验收
- 验收状态：NOT_VERIFIED

### FR-074

客户设变关联原供应商、采购合同、当前进度及任务。小范围变化记录客户、我方和供应商沟通结果；需追加或变更合同的，保留原版本并完成相应确认审批。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_full_outsource_context 汇总 engineering_change 记录中的委外、质量、延期、合同、整改和复验影响，并标记未实施或未复验的影响项；工程联络/设变和整套委外合同、当前计划节点、订单执行跟踪在同一上下文中展示，便于核对客户设变对供应商、采购合同和进度的影响
- 验证证据：tests/test_full_outsource_tools.py 覆盖工程联络质量延期扣款事项进入委外上下文；客户设变追加合同、原版本保留和完整审批链尚未验收
- 验收状态：NOT_VERIFIED

### FR-075

设变后的委外进度沿用供应商上报、采购跟进、项目同步的机制；根据客户报价及供应商当前执行情况评估并议价，明确新增费用、交期及任务影响。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_full_outsource_context 将设变/整改影响项、供应商执行跟踪、委外计划节点和费用/扣款线索分开返回，避免把方案批准当作新增费用或交期影响已落实；analysis.warnings 对未执行/未复验影响项提示不能把方案批准等同于整改完成
- 验证证据：tests/test_full_outsource_tools.py 覆盖质量延期与扣款线索提示；设变后的委外议价、交期重排、客户报价和供应商当前执行评估尚未完整联调验收
- 验收状态：NOT_VERIFIED

### FR-076

质量或延期问题记录事实、责任确认、整改和复验。按适用合同及经确认的责任处理客户对我方、我方对供应商的扣款，不能在责任未确定时仅凭延期自动认定全部由供应商承担。扣款结果关联结算数据。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_full_outsource_context 汇总质量/延期工程联络任务的预计金额、实际金额、交期影响、执行依据和状态，derived_status.has_deduction_or_cost_impact_signal 标记扣款或费用影响线索；full_outsource_review Skill 要求质量或延期扣款必须结合合同、责任确认、整改/复验和结算依据，不能只凭延期自动认定全部由供应商承担
- 验证证据：tests/test_full_outsource_tools.py 覆盖供应商质量延期扣款线索、合同依据和未关闭问题提示；责任确认、复验关闭、客户对我方/我方对供应商扣款联动及结算写入尚未验收
- 验收状态：NOT_VERIFIED

### FR-077

委外项目交付、客户验收、回款及关闭按相应通用规则执行，财务记录整套交期、合同号、委外金额、付款和扣款。采购合同、节点上报、交付及结算记录均可查询追溯。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_full_outsource_context 关联整套委外合同、供应商付款申请与已付款、供应商发货/收货、客户验收/关闭清单和结算事项，用于追溯委外交付、验收、回款/付款与关闭上下文；derived_status.has_supplier_payment_request 和 has_customer_acceptance_or_close_evidence 分别标记供应商付款与客户验收/关闭依据，避免把交付、验收、付款、关闭混为同一事实
- 验证证据：tests/test_full_outsource_tools.py 覆盖委外合同、供应商付款申请/确认、交付验收清单聚合；真实财务回款、扣款、整套交期、合同号与 ERP 结算记录联调尚未验收
- 验收状态：NOT_VERIFIED

## 设变承接

Agent 开发设变分类、依据、报价与承接、原对象关联和版本；复用已有设计/制造业务动作

### FR-078

设变包括客户设变、内部修模改模及委外设变；客户设变的执行方式可为内部或委外。收费与否、是否新增合同和具体执行范围分别记录，不以有无合同代替设变记录。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_change_intake_context 汇总客户设变、内部修模改模、委外设变分类，分别返回收费/金额线索、销售/委外合同、开工通知、执行范围和工程变更记录；change_intake_review Skill 明确设变记录、合同、收费、开工和执行范围不能相互替代
- 验证证据：tests/test_change_intake_tools.py 覆盖客户设变、合同/开工、影响项和未闭环状态聚合
- 验收状态：NOT_VERIFIED

### FR-079

小设变可能免费或无合同，仍沿用正常开工通知及审批规则，不能因无合同跳过开工条件。收费通过邮件或沟通确认时保留可核对记录；口头沟通应补充书面确认记录及相关聊天等证据。内部执行由相关人员与客户确认金额，委外由采购与供应商确认金额并上传依据。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_change_intake_context 在 derived_status 中区分 customer_written_evidence、effective_start_notice、charge_or_cost_impact 和 effective_contract；工具 warnings 对“无合同或免费小设变也不能跳过开工条件”和口头/客户确认依据缺口给出模型可用提示
- 验证证据：tests/test_change_intake_tools.py 构造免费小改、客户邮件依据和正式开工通知，验证工具不把合同缺失等同为可跳过流程
- 验收状态：NOT_VERIFIED

### FR-080

已有模具设变复用内部模具号，客户模号变化保留历史；可通过已确认的订单合同关系定位原模具，无新增合同时直接关联原模具及原项目。缺少海尔物料号时由人工按订单编号查询客户系统，查询不到时补录或上传依据并留痕。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_change_intake_context 通过 ProjectMold/Mold、工程联络 mold_number/product_ref/customer_ref 和销售合同线索反查原项目、原内部模具与客户料品/模号证据；工具 gaps 明确缺少内部模具档案或客户物料号/客户系统依据时不能重复建模具或凭猜测办理
- 验证证据：tests/test_change_intake_tools.py 验证已有内部模具号 MOLD-INT-001 与客户模号变更线索同时返回
- 验收状态：NOT_VERIFIED

### FR-081

首次承接外部模具设变时，人工报价评估，可承接后按新业务承接流程办理并建立内部模具档案。已有档案再次设变不得重复建模具。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_change_intake_context 返回 latest_quote_acceptance、known_molds、effective_start_notices 和 contact/engineering change 事实，用于外部模具首次承接与既有档案再次设变的分流核对；change_intake_review Skill 要求首次外部模具走新业务承接，已有档案再次设变不得重复建模具
- 验证证据：tests/test_change_intake_tools.py 覆盖报价承接、内部模具档案和再次设变复用原模具的查询证据；首次外部模具建档端到端仍待业务验收
- 验收状态：NOT_VERIFIED

## 工程联络单与异常闭环

Agent 完整开发问题、方案、影响、BPM 审批、整改、复验及关闭；执行动作按能力目录调用，禁止旧 ERP 异常流程

### FR-082

客户设变、设计异常、组立异常、加工异常、采购异常、质检异常、试模异常、外协不良、降低成本及制程改善等，按业务适用情况通过工程联络单统一管理，并关联当前环节。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：ContactCase 问题来源、当前环节、变更类别与紧急程度字段及创建校验；query_change_intake_context 汇总 CUSTOMER_CHANGE、DESIGN_ISSUE、ASSEMBLY_ISSUE、MACHINING_ISSUE、PROCUREMENT_ISSUE、QUALITY_ISSUE、TRIAL_ISSUE、OUTSOURCE_DEFECT 等来源并关联当前环节；ContactCase/ContactTask 查询新增 progress_summary，按历史补录、待分派、待反馈、待复验、待方案审批、复验过期、可关闭等状态派生办理阻塞项和下一步动作；不把线下记录、反馈或方案审批误判为关闭
- 验证证据：tests/test_contact_impact.py：主数据必填与未来日期阻断；tests/test_change_intake_tools.py：客户设变联络单按当前加工环节被上下文工具聚合；tests/test_contacts.py 覆盖历史补录不自动认定最终关闭，以及线上联络单 DRAFTING→WAITING_ASSIGNMENT→WAITING_FEEDBACK→WAITING_REVIEW 状态诊断
- 验收状态：NOT_VERIFIED

### FR-083

联络单至少记录客户、项目号、模具号、产品料号或适用料品、申请日期、问题来源、责任部门、变更类别、紧急程度、说明、对策、要求及实际完成时间、工时、金额、附件、版本和审批记录。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：contact_models.py/contacts.py：客户、模具、料品、日期、实际时间、工时、金额、证据、来源；既有附件版本与方案审批记录；附件关联审计冻结文件名、sha256、版本、前序版本和收件人；query_change_intake_context 返回联络单客户、项目、模具号、产品料号、申请日期、问题来源、当前环节、变更类别、紧急程度、任务实际时间/工时/金额/证据和审批方案摘要；ContactCase/ContactTask 查询新增 progress_summary，按历史补录、待分派、待反馈、待复验、待方案审批、复验过期、可关闭等状态派生办理阻塞项和下一步动作；不把线下记录、反馈或方案审批误判为关闭
- 验证证据：tests/test_contact_impact.py：创建、反馈和不可覆盖规则；tests/test_change_intake_tools.py：联络单和处理方案字段被查询工具完整读取；tests/test_files.py 覆盖附件关联通知协作参与人并在审计中冻结文件名和 sha256；tests/test_contacts.py 覆盖历史补录不自动认定最终关闭，以及线上联络单 DRAFTING→WAITING_ASSIGNMENT→WAITING_FEEDBACK→WAITING_REVIEW 状态诊断
- 验收状态：NOT_VERIFIED

### FR-084

原件附件、操作记录和复检结果一并留存，关联设变单、维修或返工任务、项目节点和成本记录；额外工时及其计价关联财务，计价方式和审批权限后续适配。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：附件版本、过程记录、独立复验以及事项实际工时/金额/证据；contact.attachment_added 事件按联络协作参与人生成站内通知；affected_type/ref 原生对象引用；query_change_intake_context 关联工程变更影响项、ContactTask affected_type/ref、计划任务、合同和成本金额线索，并提示财务/合同正式联动不得由方案交接直接替代；ContactCase/ContactTask 查询新增 progress_summary，按历史补录、待分派、待反馈、待复验、待方案审批、复验过期、可关闭等状态派生办理阻塞项和下一步动作；不把线下记录、反馈或方案审批误判为关闭
- 验证证据：结构化影响与执行单测；财务正式计价未联调；tests/test_change_intake_tools.py：影响项、执行依据和费用线索进入上下文；正式财务计价仍待联调；tests/test_files.py 覆盖附件关联后只通知有业务读取权限的联络参与人且不通知操作人本人；tests/test_contacts.py 覆盖历史补录不自动认定最终关闭，以及线上联络单 DRAFTING→WAITING_ASSIGNMENT→WAITING_FEEDBACK→WAITING_REVIEW 状态诊断
- 验收状态：NOT_VERIFIED

### FR-085

发起后向项目负责人和设计发送待办。设计评估问题并提出方案，项目负责人可退回方案或据此组织计划。工程联络单由总经理或后续明确的授权审批岗位审批；具体审核、批准、加签和退回路线按审批矩阵执行，退回到明确责任人。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：项目负责人和责任部门负责人待办；可配置方案 BPM、退回整改及生效通知；query_change_intake_context 返回 contact_resolutions 与 approved/effective 状态，并保留“方案审批不等于执行完成”的告警；ContactCase/ContactTask 查询新增 progress_summary，按历史补录、待分派、待反馈、待复验、待方案审批、复验过期、可关闭等状态派生办理阻塞项和下一步动作；不把线下记录、反馈或方案审批误判为关闭
- 验证证据：联络生命周期集成测试；完整生产岗位矩阵待验收；tests/test_change_intake_tools.py：有效处理方案和未完成复验同时返回，避免误判；tests/test_contacts.py 覆盖历史补录不自动认定最终关闭，以及线上联络单 DRAFTING→WAITING_ASSIGNMENT→WAITING_FEEDBACK→WAITING_REVIEW 状态诊断
- 验收状态：NOT_VERIFIED

### FR-086

评估应列明受影响图纸、物料、采购单、在制任务及供应商任务，明确继续执行、暂停、取消、返工或重新下达。判断当前环节的可变更性及交期影响，不能把所有设变都机械解释为全部任务从头重做。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：ContactTask 受影响对象/原生编号、五类处置动作、交期、金额与 ERP 来源时点；query_change_intake_context 按 affected_type 和 planned_action 汇总图纸、物料、采购单、在制任务、供应商任务等影响，返回 CONTINUE/PAUSE/CANCEL/REWORK/REISSUE 及交期影响；ContactCase/ContactTask 查询新增 progress_summary，按历史补录、待分派、待反馈、待复验、待方案审批、复验过期、可关闭等状态派生办理阻塞项和下一步动作；不把线下记录、反馈或方案审批误判为关闭
- 验证证据：tests/test_contact_impact.py：材料冻结和 ERP 来源约束；tests/test_change_intake_tools.py：WIP_TASK 返工和交期影响进入 planned_action_summary/affected_object_summary；tests/test_contacts.py 覆盖历史补录不自动认定最终关闭，以及线上联络单 DRAFTING→WAITING_ASSIGNMENT→WAITING_FEEDBACK→WAITING_REVIEW 状态诊断
- 验收状态：NOT_VERIFIED

### FR-087

设变审批后按确认方案更新正式版本、受影响任务、节点计划及生产安排，通知设计、采购、生产、装配、试模、品质和验收等相关部门。未受影响的任务按批准计划继续；新旧版本及已发生执行记录均保留。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：方案冻结结构化影响材料；RESOLUTION_EFFECTIVE 幂等交接、修订和责任人通知；ERP 执行留给权威工具；query_change_intake_context 返回 plan_tasks、engineering_change impacts、contact_resolutions 和执行/复验状态，用于区分未受影响任务继续与受影响任务调整；ContactCase/ContactTask 查询新增 progress_summary，按历史补录、待分派、待反馈、待复验、待方案审批、复验过期、可关闭等状态派生办理阻塞项和下一步动作；不把线下记录、反馈或方案审批误判为关闭
- 验证证据：生效交接幂等单测；真实 ERP 更新和全部门通知未联调；tests/test_change_intake_tools.py：已复验 KEEP 影响项和未执行 REWORK 影响项同时返回；tests/test_contacts.py 覆盖历史补录不自动认定最终关闭，以及线上联络单 DRAFTING→WAITING_ASSIGNMENT→WAITING_FEEDBACK→WAITING_REVIEW 状态诊断
- 验收状态：NOT_VERIFIED

### FR-088

设变涉及设计时关联工艺分析、结构设计、出图及确认；涉及采购时关联请购、订单、价格审批及供应商合同；涉及制造、装配、试模时关联工单、报工、检测和复验结果。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：设计、采购、制造、装配、试模对象引用与执行/复验依据；query_change_intake_context 通过 ContactTask affected_type/ref、PlanTask、EngineeringChange impacts、销售/委外合同上下文关联设计、采购、制造、装配、试模、物流、合同和财务对象；ContactCase/ContactTask 查询新增 progress_summary，按历史补录、待分派、待反馈、待复验、待方案审批、复验过期、可关闭等状态派生办理阻塞项和下一步动作；不把线下记录、反馈或方案审批误判为关闭
- 验证证据：原生引用和来源规则单测；正式对象执行工具端到端待完成；tests/test_change_intake_tools.py：计划任务和在制任务引用进入设变上下文；tests/test_contacts.py 覆盖历史补录不自动认定最终关闭，以及线上联络单 DRAFTING→WAITING_ASSIGNMENT→WAITING_FEEDBACK→WAITING_REVIEW 状态诊断
- 验收状态：NOT_VERIFIED

### FR-089

设变影响出入库、物流路线、发货日期或运费时同步相关单据；影响金额、付款条件或交期时更新经确认的合同及财务记录。寄售料号、安全采购量及采购预警仅在已确认适配范围内联动。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：项目节点、合同和其他原生对象引用及交期/金额影响结构；方案交接不直接改写物流、合同或财务；query_change_intake_context 将交期影响、合同、委外合同、金额/费用影响和未落实事项聚合为只读上下文，并在 limitations 中声明不改写物流、合同或财务；ContactCase/ContactTask 查询新增 progress_summary，按历史补录、待分派、待反馈、待复验、待方案审批、复验过期、可关闭等状态派生办理阻塞项和下一步动作；不把线下记录、反馈或方案审批误判为关闭
- 验证证据：结构与边界校验；物流/合同/财务/预警联动未验证；tests/test_change_intake_tools.py：合同、金额和交期影响被识别；物流/财务正式联动仍待验证；tests/test_contacts.py 覆盖历史补录不自动认定最终关闭，以及线上联络单 DRAFTING→WAITING_ASSIGNMENT→WAITING_FEEDBACK→WAITING_REVIEW 状态诊断
- 验收状态：NOT_VERIFIED

### FR-090

异常处理须记录方案批准、执行结果及复检或复验结论，由适用责任角色确认关闭。工程联络单获批不表示整改完成；涉及节点、费用及合同事项未落实时应能识别未完成事项。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：方案审批、实际反馈、独立复验/整改及人工关闭分离；最新方案下全事项复验关闭门禁和追加式实际数据；query_change_intake_context 计算 has_open_execution_or_recheck_items/open_impact_count，并在 warnings 中强调工程联络单获批不代表整改完成；ContactCase/ContactTask 查询新增 progress_summary，按历史补录、待分派、待反馈、待复验、待方案审批、复验过期、可关闭等状态派生办理阻塞项和下一步动作；不把线下记录、反馈或方案审批误判为关闭
- 验证证据：联络生命周期和 tests/test_contact_impact.py；节点/费用/合同实时阻断待联调；tests/test_change_intake_tools.py：存在有效方案但未完成执行/复验时仍返回 open 状态和告警；tests/test_contacts.py 覆盖历史补录不自动认定最终关闭，以及线上联络单 DRAFTING→WAITING_ASSIGNMENT→WAITING_FEEDBACK→WAITING_REVIEW 状态诊断
- 验收状态：NOT_VERIFIED

## 暂停与恢复

Agent 开发依据、受影响动作限制、区间与顺延、防重复及客户交期独立确认；既有对象状态衔接须核对接口

### FR-091

收到客户邮件或线下暂停通知时，由项目负责人核实并上传依据，记录原因、暂停开始时间、影响对象及预计情况。暂停状态应通知相关部门，明确受影响任务的执行限制，不能只改变显示颜色而继续无条件下单或报工。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_project_control_context 支持按项目号、项目名、模具号、工程联络和暂停恢复单号定位项目，返回项目版本、有效计划、未完成任务、当前暂停区间、待处理暂停/恢复申请和可选 Agent BPM；prepare_project_pause 仅准备暂停建议，冻结有效计划和未完成任务范围；本人确认后才创建暂停单并提交 BPM，审批生效后项目状态转为 PAUSED；domains.before_submit/apply 在暂停状态下阻断普通计划、下单、报工、发料等执行业务，同时保留工程联络、合同、结算和恢复等专用流程
- 验证证据：tests/test_project_pause.py 覆盖暂停冻结未完成任务、计划范围变化阻断生效、按 identifier 查询上下文和暂停期限制/允许事项输出
- 验收状态：NOT_VERIFIED

### FR-092

恢复时上传恢复通知和恢复时间。项目整体暂停恢复后，未完成节点按实际暂停时长统一顺延，经项目负责人确认生效；已完成节点保留实际日期，同一次暂停不得重复顺延。局部任务调整按第7章影响评估处理。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：prepare_project_resume 要求关联当前有效暂停记录、恢复依据和恢复日期；恢复生效时按实际暂停天数顺延暂停时冻结的未完成节点；PauseRecord.shift_applied、shifted_days 与 PauseTaskShift 保存每个任务前后计划日期；已完成节点不顺延，同一暂停区间恢复记录唯一防止重复顺延
- 验证证据：tests/test_project_pause.py 覆盖未完成节点顺延、已完成节点保留原日期、同一恢复不能重复应用和查询返回顺延证据
- 验收状态：NOT_VERIFIED

### FR-093

顺延保留前后计划、暂停依据和确认记录，客户承诺交期按客户确认单独处理。必要的资料补录、沟通、保管和结算等操作按权限保留，不因暂停一概禁止。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：ProjectPauseDetail 保存暂停/恢复依据、原因、预计恢复日、客户承诺交期快照和冻结任务；PauseTaskShift 保存顺延前后日期与确认记录；query_project_control_context 返回 allowed_during_pause 与 blocked_during_pause，明确资料补录、沟通、合同结算核对、工程联络和恢复申请不因暂停一概禁止；恢复生效仅调整内部计划任务，ProjectProfile.customer_due_date 保持不变；客户承诺交期变更须另行客户确认
- 验证证据：tests/test_project_pause.py 覆盖客户承诺交期不随恢复顺延、查询返回 customer_due_date_is_independent 和允许/限制事项清单
- 验收状态：NOT_VERIFIED

## 终止结算与正常关闭

Agent 开发不同关闭清单、处置协同、人工确认、归档及历史更正；引用 ERP 已有执行/财务事实

### FR-094

客户终止项目时，项目负责人上传终止依据，记录当前环节并停止正常执行。统计已完成工作和已发生费用，由财务核对并按业务流程与客户确认结算金额及收付款。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：backend/app/project_closure.py 区分终止/正常关闭并在终止生效后停止Agent本地未完成任务；backend/app/project_closure_tools.py 提供终止材料准备、人工确认和Agent BPM提交；ProjectClosureDetail保存终止依据、当前环节、完成工作和费用汇总
- 验证证据：tests/test_project_closure.py覆盖终止生效、任务停止和终止清单建立；内置浏览器验证合成终止建议、本人确认、审批通过及生效
- 验收状态：NOT_VERIFIED

### FR-095

处理未完成采购、在制品、供应商任务及相关结算，保留取消、交接和处置结果。终止后允许授权人员办理适用的资料补充、费用核对和结算，不再按普通生产任务继续推进。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：终止清单保存未完成采购、在制品、供应商任务及客户/供应商结算处置；ERP事实要求原生引用和截至时间且不复制ERP台账；项目终止后使用专用待结算状态
- 验证证据：tests/test_project_closure.py覆盖ERP来源缺少引用时阻断及追加修订历史；真实ERP处置与结算联调待执行
- 验收状态：NOT_VERIFIED

### FR-096

按适用事项完成终止结算后以“终止已结算”等状态关闭。不适用的交付和验收记录说明原因，不套用正常交付项目的全部关闭条件；历史记录持续保留。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：终止关闭使用独立TERMINATION清单和SETTLEMENT_CLOSE决定；交付/验收允许记录原因后标为不适用；关闭结果为CLOSED_TERMINATION且历史不删除
- 验证证据：tests/test_project_closure.py覆盖终止结算关闭、不适用项和关闭后历史保护；专用PostgreSQL验收待执行
- 验收状态：NOT_VERIFIED

### FR-097

正常交付项目须在交付、适用验收、发票、回款、供应商结算及异常事项处理完成后关闭。生产完工、发货、客户签收或单次回款均不单独代表项目已结束。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：正常关闭清单要求计划、交付、适用验收、发票、客户回款、供应商结算、异常处理及归档；最终审批生效前重新检查实时阻断项
- 验证证据：tests/test_project_closure.py覆盖未完成计划和新联络事项阻断正常关闭；测试覆盖计划完成后的实时复核与系统修订归档
- 验收状态：NOT_VERIFIED

### FR-098

归档包括项目过程、设计版本、采购合同、质量、交付、验收、设变和财务记录。关闭确认人及检查明细在角色适配中确定；关闭后授权人员仍可查询全部适用历史数据，后续更正不得无痕覆盖原记录。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：ProjectClosureItemRevision追加保存每次清单更正历史；关闭案例保留人员、版本、来源、依据、截至时间和检查明细；迁移f1a4d8c7e2b3建立关闭案例、清单、修订和数据库约束
- 验证证据：5项终止/关闭规则测试通过；全部可运行的非PostgreSQL测试114 passed、103项专用PostgreSQL集成测试跳过；离线DDL和真实数据库验收另行登记
- 验收状态：NOT_VERIFIED

## 财务节点与核对

Agent 开发财务需求缺失能力、合同节点、审批、实际确认、核对及汇总；已有 ERP 财务事实不另建同义总账

### FR-099

开工时通知财务维护台账，取得内部通知并关联客户订单、项目、模具和合同。合同晚到时记录待核对事项，收到后确认合同编号、签订日期、金额、付款方式、节点及交期等信息。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_finance_context 汇总有效开工通知、项目/模具/合同资料和财务交接上下文，不创建同义财务台账；销售合同和整套委外合同返回合同号、金额、预计日期、付款节点及替代关系；合同晚到/缺失通过 gaps/limitations 提醒核对
- 验证证据：tests/test_finance_context_tools.py 覆盖开工通知、项目模具、销售合同和委外合同进入财务上下文
- 验收状态：NOT_VERIFIED

### FR-100

收款节点单独结构化保存，至少包含条件或事件、比例或金额、账期、预计到期日期、确认依据和状态。支持3-3-3-1、DFM认证、试模、签收或验收等不同约定；具体比例及事件以对应合同经财务确认的结果为准。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：ContractDetail/PaymentStage 独立保存合同金额、节点名称、条件、金额、币种、条件确认状态和依据；query_finance_context 将销售合同收款节点作为 customer_receivable_nodes 返回，明确节点条件需以合同和财务确认为准
- 验证证据：tests/test_finance_context_tools.py 覆盖 DFM 认证收款节点、金额、条件和未确认状态读取
- 验收状态：NOT_VERIFIED

### FR-101

按合同适用节点、触发事件、账期和到期日提醒。T0试模、DFM认证、移模签收或验收仅在合同采用时触发；试模后15、30、40天等为合同示例，不作为所有项目统一账期。未到期、到期未收和逾期未收分别记录，特殊标记由财务核实。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_finance_context 按合同实际 PaymentStage 条件返回节点，不把 T0、DFM、试模、签收、验收或账期示例套用为统一规则；工具 warnings 标识未确认条件和未接入客户实际回款台账，区分节点条件、提醒和实际收款
- 验证证据：tests/test_finance_context_tools.py 覆盖合同节点条件未确认时的派生告警；到期提醒算法和真实回款状态联调待完成
- 验收状态：NOT_VERIFIED

### FR-102

客户实际回款由财务人工确认，保存日期、金额、合同节点、凭证和对应项目关系。系统提醒或识别结果不替代实际回款确认；分次回款均留独立记录，并按确认关系汇总。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_finance_context 将客户付款节点与客户实际回款台账明确分离，derived_status.has_customer_actual_receipt_ledger 当前为 false；finance_context_review Skill 要求未接入客户实际回款台账时不得声称客户已回款
- 验证证据：tests/test_finance_context_tools.py 验证客户合同节点存在但 has_customer_actual_receipt_ledger 为 false，并输出未接入实际回款台账限制
- 验收状态：NOT_VERIFIED

### FR-103

付款流程为：申请→适用条件核验→审批→待支付及支付执行→实际付款确认。不符合适用条件时补充资料或特殊审批；审批通过仅代表允许支付，不计入已付款金额。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：supplier_payment 业务保存付款申请、关联付款节点、审批状态、授权占用 reservation；finance.condition 核验付款条件，finance.confirm 仅在申请生效后生成实际付款确认；query_finance_context 区分 APPROVED_FOR_PAYMENT、reservation、payment_confirmations 和 confirmed_totals，明确审批通过不等于已付款或全部付清
- 验证证据：tests/test_finance_context_tools.py 覆盖已审批付款申请、部分实付、未释放授权占用和审批/实付区分告警
- 验收状态：NOT_VERIFIED

### FR-104

实际支付完成并经财务确认后，生成或确认实际付款记录并计入供应商已付款，关联项目、合同、采购单、客户回款条件、费用和发票凭证。付款执行渠道另行适配，不默认为本系统自动操作银行转账。

- 最新口径：不开发银行自动转账；保留付款流程、人工实际支付确认及凭证。
- 实现证据：PaymentConfirmation 保存实际付款日期、金额、币种、付款引用、凭证和确认人；finance.confirm 不执行银行转账，仅登记财务确认结果；query_finance_context 将供应商实付按有符号付款确认汇总，并关联合同付款节点与申请
- 验证证据：tests/test_finance_context_tools.py 覆盖实际付款确认计入 confirmed_supplier_payment；银行转账渠道保持不开发
- 验收状态：NOT_VERIFIED

### FR-105

未完成、失败或撤回的支付与已确认支付应区分，重复确认不得重复累计。涉及错误确认、退款、扣款及金额更正时保留原因、前后值、人员和依据，具体冲回及对账流程在财务适配中确定。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：finance_correction 业务保存原付款、原因、冲正依据、冲正日期和反向付款记录；重复冲正由 ALREADY_REVERSED 阻断；query_finance_context 返回 finance_corrections，并按正负 PaymentConfirmation 汇总供应商实付，避免覆盖原付款或重复累计
- 验证证据：tests/test_manufacturing.py 覆盖财务冲正保留原付款并恢复占用；tests/test_finance_context_tools.py 覆盖冲正后净实付 8000.00 汇总
- 验收状态：NOT_VERIFIED

### FR-106

按项目和模具记录内部加工或整套委外属性；委外保存整套交期、合同号和金额。内部成本关联工时、材料、加工、外协及适用物流等费用，设变新增费用、额外工时、扣款和合同增减额分别可追溯。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：ProjectProfile 保存内部/整套委外属性；整套委外合同返回合同号、金额和预计交期；工程联络 ContactTask 保存额外工时、金额、扣款或成本影响线索；query_finance_context 汇总 execution_mode、full_outsource_contracts、cost_and_change_impacts，不把报价成本或回款金额直接当实际成本
- 验证证据：tests/test_finance_context_tools.py 覆盖整套委外合同金额、供应商合同节点、设变扣款和额外工时线索聚合
- 验收状态：NOT_VERIFIED

### FR-107

汇总客户已回款、供应商已付款、剩余应收应付及项目收入、成本、利润和占用资金，由财务核对。收入确认、含税口径、工时计价、分摊和占用资金公式须经适配确认，不直接以回款金额替代收入或以报价成本替代实际成本。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_finance_context 汇总客户合同收款节点、供应商已付款、未释放付款占用、财务冲正、费用/扣款线索和关闭清单财务事项；工具 gaps 明确收入确认、含税口径、工时计价、费用分摊和占用资金公式尚未适配，不能以回款金额替代收入或报价成本替代实际成本
- 验证证据：tests/test_finance_context_tools.py 覆盖供应商净实付、付款占用、费用线索和收入成本利润口径限制
- 验收状态：NOT_VERIFIED

### FR-108

合同替代、追加或变更后，按确认的有效金额及历史收付款关系更新台账，不重复计算。财务更正应可审计；无金额或项目权限的人员不能查看或导出相应信息。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：合同上下文保留 replaces_id 和合同状态；财务上下文按当前可见有效事实读取，不重复累计替代合同的付款节点；财务冲正以追加记录保存前后关系；query_finance_context 在缺少销售合同/付款等权限时不返回合同号、金额或付款明细
- 验证证据：tests/test_finance_context_tools.py 覆盖无金额/合同权限用户无法看到合同号、金额和供应商付款明细；合同替代真实收付款分配仍待联调验收
- 验收状态：NOT_VERIFIED

### FR-109

正常项目财务关闭按第12章正常关闭条件；终止项目按终止结算条件。结束后保留历史查询能力，不能因项目关闭删除合同、发票、回款或供应商付款记录。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_finance_context 读取 ProjectClosureCase/ProjectClosureItem 中发票、客户回款、供应商结算、财务归档及终止收付款核对事项；项目关闭后业务历史不删除，财务上下文通过项目档案与业务记录继续只读查询合同、发票/回款清单和供应商付款记录
- 验证证据：tests/test_finance_context_tools.py 覆盖结项财务清单项进入上下文；正常/终止关闭完整财务端到端验收仍待执行
- 验收状态：NOT_VERIFIED

## Agent 查询与被动预警

Agent/Harness/LLM/Tool/Skills 新开发；只在提问时分析，查询先按对象和权限路由，不镜像 ERP 数据

### FR-110

支持查询项目当前阶段、模具延期风险、零件异常、累计费用、客户回款节点及供应商执行情况。按确认权限找到对应业务对象，展示可核对的实际记录及来源单据。

- 最新口径：AI 预警仅用户提问时执行，依据已上报异常或临期未发货；严格限定用户责任域。
- 实现证据：analyze_delivery_risk 工具按已发布临期规则、正式订单、发货记录和未关闭供应商异常计算风险；DeliveryRiskInput 支持 project_id/identifier，将分析限定到单个可见项目；项目解析失败或多项目命中返回 resolution/candidates，不扩大为全量分析
- 验证证据：tests/test_delivery_risk_tool.py 覆盖工具 schema、项目编号过滤和歧义不退回全量分析；tests/test_domains.py::test_hardware_risks_exclude_other_supplier_domain 验证责任域隔离（专用 PostgreSQL 集成测试）
- 验收状态：NOT_VERIFIED

### FR-111

从项目可查看模具、合同、订单、工单、质量、验收、发票及付款记录；从工程联络单、采购单或费用记录也能反向定位项目与模具。历史模号、旧合同号和版本应保留检索关联。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：query_project_dossier 支持项目编号/名称、模具、联络、订单、合同和业务单号反查项目；analyze_delivery_risk 支持按项目 ID/编号/名称聚焦供应商执行风险
- 验证证据：tests/test_project_dossier.py 覆盖同号歧义、隐藏项目不可见和字段裁剪不反查；tests/test_delivery_risk_tool.py 覆盖项目编号限定风险分析
- 验收状态：NOT_VERIFIED

### FR-112

按提问查询时，应区分未找到、多条候选、未确认数据及无权限情形，必要时要求用户明确对象。不编造缺失数据或将不同项目的记录拼接为确定答案；计算结果可回溯输入口径。自然语言技术实现和响应指标后续适配。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：项目档案查询与发货风险分析均在未找到、多候选、无权限或范围不足时返回明确 resolution/limitations；analyze_delivery_risk 对项目标识不唯一时要求用户指定，不把全部可见项目当作替代结论；query_quote_acceptance_context 在报价承接语境中返回 RESOLVED、MULTIPLE_CANDIDATES、NOT_FOUND、NOT_FOUND_OR_FORBIDDEN，限制模型把不同项目记录拼接成确定答案；工具结果包含 limitations，说明只读边界、权限范围和人工审批要求；query_contract_context 在合同语境中返回 RESOLVED、MULTIPLE_CANDIDATES、NOT_FOUND、NOT_FOUND_OR_FORBIDDEN，并在权限不足时写入 limitations；合同上下文按对应合同工具隔离明细，防止用汇总绕过合同号或金额权限；query_internal_start_readiness 在正式开工语境中返回 RESOLVED、MULTIPLE_CANDIDATES、NOT_FOUND、NOT_FOUND_OR_FORBIDDEN，并在缺少承接/合同/计划工具时写入 limitations；工具在未授权承接查询时不泄露承接单号或把缺失资料编造成未承接；query_project_plan_context 在计划语境中返回 RESOLVED、MULTIPLE_CANDIDATES、NOT_FOUND、NOT_FOUND_OR_FORBIDDEN，并在缺少计划变更工具时写入 limitations；工具不把无有效计划推断为项目无进度，明确 warning 需要核对有效计划；query_design_route_context 在设计/BOM/路线语境中返回 RESOLVED、MULTIPLE_CANDIDATES、NOT_FOUND、NOT_FOUND_OR_FORBIDDEN，并在缺少计划或联络工具时写入 limitations；设计上下文工具不把无生效设计推断为项目无设计工作，不把BOM路线推断为采购/加工/装配/试模执行已完成；query_procurement_price_context 在采购价格和订单语境中返回 RESOLVED、MULTIPLE_CANDIDATES、NOT_FOUND、NOT_FOUND_OR_FORBIDDEN，并在缺少设计、申请或订单工具时写入 limitations；采购上下文工具不把无价格推断为不可采购，不把订单存在推断为已收货/检验/入库完成，正式执行仍以对应回执为准
- 验证证据：tests/test_project_dossier.py 覆盖同号歧义与无权不可见；tests/test_delivery_risk_tool.py 覆盖项目聚焦和歧义口径；tests/test_quote_tools.py 覆盖多候选要求指定项目 ID、无合同工具时不泄露合同号和有效承接摘要；tests/test_contract_tools.py 覆盖多项目候选、合同权限隔离和晚到合同派生状态；tests/test_start_tools.py 覆盖多候选、承接依据权限隔离和已开工派生状态；tests/test_plan_tools.py 覆盖多候选、无有效计划和计划变更权限隔离；tests/test_design_tools.py 覆盖多候选、无生效设计和计划/联络权限隔离；tests/test_procurement_tools.py 覆盖多候选、无有效价格和采购申请/订单权限隔离
- 验收状态：NOT_VERIFIED

## 权限与审计

Agent 开发管理员灵活授权、范围/字段/工具/Skill 隔离及全过程审计；正式操作人工确认

### FR-113

按角色及项目授权控制查看、录入、修改、审批和导出；价格、成本、利润和项目资料采用适用数据权限。问答、页面、附件下载及导出应执行一致权限，不通过汇总或链接绕过限制。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：authorization.access/predicate/select_fields/fingerprint 统一约束页面、问答工具、运行上下文与字段输出；query_governance_context 返回目标用户有效授权、字段范围、工具/Skill 能力、运行时 security_version 与 authorization_hash；files.readable 在附件关联业务对象后必须重新校验 contact.read，query_governance_context 只返回当前可见附件元数据，不下载、不导出、不解析原文；tool_gateway 将 query_governance_context 绑定 audit.read，Skill 明确禁止自然语言兜底和绕过权限汇总；后端 capability_descriptor 为工具/Skill 统一输出名称、业务类别、部门、类型、人工确认模式和依赖工具；/api/capabilities 与管理员能力分配接口共用同一目录，前端优先使用后端元数据，能力启用仍不扩大数据权限；AgentApprovalDelegation 与 /api/agent-approval-delegations 支持用户把指定 process_key/node_key 的 APPROVE 动作显式委托给 Agent；process_agent_auto_approvals 仅在本轮 agent_permission_mode 为 delegated_auto、流程节点声明 agent_auto_approval、可选 agent_auto_policy.condition 安全条件明确满足、当前待审批席位属于授权用户、授权仍有效且原审批规则允许 APPROVE 时执行，默认 ask 模式即使存在委托也不自动审批；授权变更同步提升 security_version 并进入 authorization fingerprint；前端输入框工具栏提供 Agent 权限模式下拉，选择停留在输入框内并按当前用户本机持久化；/api/runs 将 agent_permission_mode 写入本轮 Run checkpoint，运行历史、worker claim 和中间 checkpoint 均保留该模式；Harness 将本轮权限模式写入模型系统上下文；设置页提供个人自动审批授权/撤销，审批流程配置节点提供 agent_auto_approval 开关和自动审批安全条件；授权选项只由后端返回已发布且显式允许自动审批的节点；confirmation_policy 由后端按来源 Run 和动作类型生成，会话 proposal 卡片与确认弹窗显示必须本人确认、确认后提交审批或确认后授权节点可自动审批；工程联络方案、项目暂停/恢复、项目终止/关闭等 proposal 本人确认后提交 BPM 时继续传递 agent_permission_mode
- 验证证据：tests/test_governance_context_tools.py 覆盖权限矩阵、统一边界说明、无 contact.read 时不泄露联络附件文件名；tests/test_files.py 覆盖上传私有性、附件业务撤权后下载/会话查询不可见、运行附件绑定当前会话；tests/test_agent_api.py 覆盖权限变更后的 security_version/authorization_hash 隔离；tests/test_capability_catalog.py 覆盖后端能力目录元数据，tests/test_agent_api.py 增加 /api/capabilities 元数据断言与输入框 Agent 权限模式进入 Run 历史和 worker 上下文；tests/test_agent_approval_delegation.py 覆盖默认 ask 模式不自动审批、显式 delegated_auto 模式下授权节点可自动审批、未声明自动审批节点不被绕过、自动审批安全条件阻断、撤销授权会改变授权指纹，以及 API 只暴露/接受显式自动审批节点；tests/test_contact_proposals.py 和 tests/test_contact_lifecycle.py 覆盖操作建议的 confirmation_policy 与 delegated_auto 传递到 BPM 提交
- 验收状态：NOT_VERIFIED

### FR-114

关键操作记录操作者、时间、前后状态、原因及依据，包括承接、开工、计划、合同版本、设变、暂停恢复、终止、实际收付款及金额更正。历史有效记录不以直接覆盖方式消除。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：AuditEvent/Outbox 在关键操作 record 时保留操作者、动作、资源、时间和 detail；query_governance_context 按可见项目资源汇总 audit_trail；工程联络附件、项目结项事项、付款更正、暂停恢复等业务模型保留版本、前后引用或修订记录，不通过直接覆盖消除历史；Agent 受托自动审批写入 ApprovalAction.user_snapshot.actor_type=AGENT_DELEGATED 与 delegation_id，approval.decided 审计事件同步记录 actor_type/delegation_id；启用和撤销自动审批授权分别记录 agent.approval_delegation.enabled/revoked
- 验证证据：tests/test_governance_context_tools.py 覆盖审计事件按项目资源返回且包含状态前后和原因依据；tests/test_files.py 覆盖附件版本不可直接删除、旧版本仍可追溯；tests/test_project_closure_tools.py 与 tests/test_finance_context_tools.py 覆盖结项/财务更正上下文的历史依据；tests/test_agent_approval_delegation.py 覆盖 Agent 受托审批动作与审计事件均保留 actor_type/delegation_id
- 验收状态：NOT_VERIFIED

## 通知与附件

Agent 开发通知、待办及附件版本与权限；业务提醒与主动 AI 预警分别管理

### FR-115

待办和提醒关联业务对象、触发条件、接收角色及处理状态，支持确认与追踪。节点变更后更新适用提醒，避免对同一业务重复生成有效任务；手机、站内或其他消息渠道在适配阶段确定。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：Outbox/Inbox/Notification 以业务对象 resource_id、事件 kind、recipients、read 状态追踪消息；Notification 对 event_id/user_id 唯一，message_worker 通过 Inbox 去重；工程联络附件关联生成 contact.attachment_added 事件，按发起人、复验负责人、协作事项创建人、处理人和责任部门负责人计算候选收件人；投递前仍复核 contact.read；query_governance_context 返回项目相关 outbox 发布、投递、通知数、未读数、失败和死信状态，帮助识别重复或失败提醒
- 验证证据：tests/test_governance_context_tools.py 覆盖通知投递、未读计数和关联业务对象；tests/test_messages.py 覆盖 Outbox 发布重试、Inbox 去重和 Notification 唯一投递；tests/test_contacts.py 覆盖工程联络分派通知生成；tests/test_files.py 覆盖工程联络附件关联通知协作参与人并由 message_worker 权限复核后投递
- 验收状态：NOT_VERIFIED

### FR-116

附件保留来源、上传人、时间、版本及对象关联，授权人员可查看和下载。文件格式、大小、保留期限及敏感数据范围后续确认；历史截图中的字段和按钮不自动扩大权限或功能。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：FileObject 保留上传人、会话、文件名、格式、大小、sha256、存储后端和版本；ContactAttachment 保留业务对象、材料标识、版本、前一版本、关联人和时间；附件关联后 AuditEvent.detail 冻结 contact record 明细，包含附件版本、文件名、sha256、前序版本和 file_id；通知 payload 只保留收件人，不携带附件内容；query_governance_context 返回当前授权可见附件的来源、上传人、关联对象、版本链和当前版本状态；不暴露对象存储 key，不读取文件内容；/api/material-templates/{id}/xlsx-preview 可基于本人可见 XLSX 原件、资料模板版本和临时列映射生成待人工核对的 material_data 草稿；解析器不执行宏、外部链接或公式，含公式、缺列、类型错误或重复行标识时返回 NEEDS_REVIEW，不创建业务材料绑定；material_template_xlsx_mapping 与 /api/material-templates/{id}/xlsx-mappings 保存已发布资料模板的 Excel 映射版本、映射哈希、创建人和时间；预览未传临时映射时使用最新保存映射并记录所用映射版本；material_review 与 /api/material-templates/{id}/xlsx-reviews 保存 XLSX 解析核对包，记录模板哈希、映射哈希、原文件 sha256、material_data、issues 和 review_hash；无 issues 的核对包可人工确认并记录确认人、确认时间和审计事件，有 issues 的核对包禁止确认；material_binding 与 SubmitInput.material_review_id 将本人已确认且与审批模板资料版本匹配的核对包绑定到采购或通用业务提交，并把 material_data、绑定 ID、模板/映射哈希、文件 sha256、review_hash 和确认信息冻结进审批实例快照；正式提交仍拒绝客户端直接传 material_data
- 验证证据：tests/test_governance_context_tools.py 覆盖附件上传人、版本、摘要、S3 版本状态和无权不可见；tests/test_files.py 覆盖格式/大小/宏校验、私有下载、S3 版本对象、附件版本冲突、撤权不可见，以及附件关联通知和审计冻结文件名/sha256；tests/test_material_templates.py 覆盖 XLSX 资料预览成功解析、保存映射版本后复用预览、模板哈希冲突阻断、公式单元格/重复行标识进入待核对状态、干净核对包确认成功，以及有 issues 的核对包禁止确认；tests/test_material_binding.py 覆盖已确认核对包提交时冻结为 material_binding、审批快照包含 material_data 与来源哈希，以及未确认或缺失核对包继续阻断提交
- 验收状态：NOT_VERIFIED

## 来源与运行交付

Agent 开发明确来源的受控调用、失败核对、Docker 部署、备份恢复及实施验收；不转发投影或等待 ERP 开发

### FR-117

各数据来源应明确录入或同步责任、确认环节及最终有效系统；同步失败或数据冲突应进入可见的待处理状态，不能静默认定成功。具体接口、重试和人工补录规则列入适配方案。

- 最新口径：按权威来源直接查询/调用，不采用 ERP 镜像、CDC、投影、先本地后 ERP 的查找策略。
- 实现证据：ContactTask、ProjectClosureItem 等来源字段保留 source_system/source_ref/source_as_of 和执行来源；ERPOperation 记录 native_id、state、request_hash、erp_user_id、response/error_code；query_governance_context 明示权威来源策略，返回来源字段、ERP 操作状态和 pending_or_failed_source_operations；UNKNOWN/REJECTED/DISPATCHING 不默认为成功
- 验证证据：tests/test_governance_context_tools.py 覆盖 ERP UNKNOWN/TIMEOUT 进入待处理来源状态并展示不采用镜像/投影策略；tests/test_project_closure_tools.py 覆盖 ERP 来源结项事项必须带原记录引用和核对时点；tests/test_change_intake_tools.py 覆盖联络/设变来源字段可查
- 验收状态：NOT_VERIFIED

### FR-118

部署、用户规模、响应时间、可用性、备份频率、恢复目标及日志保留期限在实施方案中确认并纳入测试。未确认前不设定无依据的性能或准确率承诺。

- 最新口径：完整保留；具体既有动作复用不抵消本条需求。
- 实现证据：新增 query_operations_readiness_context 只读工具和 operations_readiness_review Skill，核对部署拓扑、用户规模、响应时间、可用性、备份频率、恢复目标、日志保留、生产存储和模型运行边界的当前事实与验收缺口；`readiness_summary` 将机器可验证阻断项和仍需人工/实施验收的门槛分开汇总；部署核对只读返回 Python、Node/npm、Docker CLI/daemon/compose、前端构建产物和后端入口文件状态；数据库核对明确返回 PostgreSQL/moldpilot/Navicat 交付基线、实际 SQLAlchemy 方言、PostgreSQL 当前库名和 Alembic 迁移版本一致性，未满足时提示不得用 SQLite 作为交付依据；Redis 核对只读执行 PING/INFO/XINFO，返回消息 stream 和通知消费组是否就绪；备份恢复核对返回受控备份脚本、受控隔离恢复脚本、pg_dump/pg_restore 可用性、Docker PostgreSQL client 可用性、显式客户端路径或常见安装目录发现结果和本机演练前提；日志保留核对返回审计、应用、访问、模型调用日志保留天数配置状态和审计表时间范围；新增 `MOLD_ACCEPTANCE_EVIDENCE_FILE` 和 scripts/acceptance_gates.py，用本地 `.local/acceptance-gates.json` 登记正式验收确认人、确认时间和证据引用，格式不完整不通过；工具只返回脱敏配置形态、健康检查和运行计数，不泄露数据库密码、Redis 密码、API Key、S3 密钥或 Worker 密钥；未确认前显式禁止承诺 SLA、性能、准确率、RTO 或 RPO
- 验证证据：tests/test_operations_readiness_tools.py 覆盖工具/Skill 注册、超级管理员可用性、FR-118 七项门槛未验收状态、备份恢复 native/docker client 模式、日志保留脚本元数据和敏感信息脱敏；scripts/verify_postgres_baseline.py 与运行时工具调用已在本机实际 PostgreSQL `moldpilot` 库通过，返回 `dialect=postgresql`、`current_database=moldpilot`、`alembic_version=d2f0a9b1c3e4` 且仓库 head 相同；scripts/dev_redis.py status/start/init-stream 已在 Docker Redis 上验证 `redis_reachable=True`、`stream_exists=True`、`group_ready=True`；scripts/backup_postgres.py 已通过 Docker `postgres:18-alpine` 真实生成 `.local/backups/moldpilot_20260916_122810.dump`；scripts/restore_postgres.py 已将该备份恢复到隔离库 `moldpilot_restore`，恢复后核对 `admin_count=1`、`alembic=d2f0a9b1c3e4`；scripts/log_retention.py dry-run 已验证审计 365 天、应用 180 天、访问 90 天、模型 180 天的本机保留策略；当前 `readiness_summary.machine_status=MACHINE_PREREQUISITES_READY`、`machine_blocker_count=0`，但 `acceptance_status=NOT_VERIFIED`、`overall_status=BLOCKED`，防止把机器前提就绪当作整体验收完成
- 验收状态：NOT_VERIFIED

## 原文验收场景

以下保留原文；已确认的后续决定按 PRODUCT_CONTRACT.md 执行，不能重新引入已取消范围。

### AT-01 中标有匹配 无匹配 多条匹配

正确呈现来源；无法确定时人工确认，不虚填；FR-004、017

### AT-02 重复邮件及合同上传

不重复建立有效项目或重复累计数据，能追溯处理结果；FR-005

### AT-03 承接拒单与开工限制

拒单保留原因；未满足开工及计划条件不下达执行任务；FR-019至022

### AT-04 合同晚到与催补

项目按已确认条件启动，按预计日期预警，实到后关联；FR-024、029

### AT-05 内部加工含局部委外

任务、供应商成果、收货与检验关联；不误归整套委外；FR-043、054、061

### AT-06 装配和试模前置条件

装配按适用齐套条件；试模前对应装配已完成；FR-038、062、063

### AT-07 普通异常与影响交期异常

前者不强制重排，后者审批联动；内部顺延不改客户承诺；FR-039至041

### AT-08 设变与版本切换

模号不重复，受影响任务按批准方案处理，旧版可追溯；FR-078至090

### AT-09 暂停 恢复及重复恢复操作

受影响任务受控；实际暂停时长仅顺延一次；FR-091至093

### AT-10 委外上报 延期及质量问题

上报、跟进、核验与整改记录完整，扣款有合同和责任依据；FR-071至077

### AT-11 客户签收但验收不通过

不误判验收通过；问题整改复验及影响可追溯；FR-068、069

### AT-12 合同替代与追加

历史已收已付保留，当前金额正确，不重复累计；FR-030、031、108

### AT-13 不同付款节点与分次回款

仅适用事件触发，未到期不标逾期，分次确认可核对；FR-100至102

### AT-14 预付款与审批未支付

按阶段核验，未实际支付不计已付款；FR-056、103、104

### AT-15 重复支付确认与更正

不重复累计，更正及依据可追溯；FR-005、105

### AT-16 正常关闭与终止结算

正常按完整适用条件关闭；终止不强制无关交付验收；FR-094至098

### AT-17 受限金额及项目查询

页面、问答、附件及导出均不返回无权限数据；FR-110至114

### AT-18 同步失败和数据未确认

呈现待处理状态及来源，不虚报完成；FR-004、112、117

## 原文适配事项

以下保留原文；已确认的后续决定按 PRODUCT_CONTRACT.md 执行，不能重新引入已取消范围。

### AD-01 新系统与BPM、U9/ERP的单据责任、审批职责、主数据及最终有效数据归属

总体方案确定前

### AD-02 设计成果编制方式、BOM及路线来源、排产和问答自动化深度

总体方案确定前

### AD-03 客户订单 项目 合同 模具的数量关系、编码规则及客户字段映射

数据模型确定前

### AD-04 邮件、客户平台、ERP、签署及消息接口或人工导入方案；接口可用性与授权

对应接口开发前

### AD-05 承接 合同 计划 设变 付款 关闭等审批矩阵、角色权限、加签退回及代理

流程配置与开发前

### AD-06 项目日历、55天参考周期、任务依赖、齐套率和关键件、各阶段及总进度算法

计划与制造模块开发前

### AD-07 料品与供应商规则、价格优先级、税价有效期、物流参数、寄售与安全采购范围

采购及物流模块开发前

### AD-08 收入成本口径、费用分摊、工时计价、扣款退款更正、回款分配及占用资金公式

财务模块开发前

### AD-09 供应商上报方式、节点证据、验收角色、紧急件时限及操作流程

委外与异常模块开发前

### AD-10 表单必填项、文件限制、看板 甘特图 报表 导出和查询交互样式

界面与报表开发前

### AD-11 历史项目、合同、模具、物料和收付款导入范围、质量及核对责任

数据迁移实施前

### AD-12 部署环境、用户和数据规模、性能、备份恢复、日志及附件保留、运行维护责任

实施方案确定前

### AD-13 软件交付清单、培训、试运行、验收样本、通过标准和缺陷处理机制

测试与上线安排确认前
