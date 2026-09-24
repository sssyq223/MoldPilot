# MoldPilot 本地能力 Skill/Tool 封装与 ERP 接口运行隔离设计

## 1. 目标与范围

本设计针对当前工作区已有的本地新增/修改内容，目标是：

1. 移除 MoldPilot 当前运行链路中的 ERP 外部接口能力。
2. 将本地新增或修改的业务能力统一登记为本地 Skill/Tool。
3. 保持远程仓库 `HEAD` 中未被本地修改的代码不变，不删除、不重命名、不回滚。
4. 不把基础设施、测试、迁移和前端组件伪装成业务 Skill/Tool。

本设计不要求物理删除远程 ERP 源文件。由于远程未修改代码不可触碰，ERP 的处理方式是从本地可修改的能力目录、运行入口和路由层进行运行时隔离，使其不再成为当前 MoldPilot 可发现、可调用或可访问的业务能力。

当前仓库基线为 `4b5e52d`，工作区存在大量未提交的本地修改和新增文件。本设计只约束后续允许修改的本地范围，不覆盖或整理其他既有工作。

## 2. 非目标

以下内容不属于本轮 Skill/Tool 封装对象：

- PaddleOCR 服务本身及其 Docker 运行环境；
- Document Worker、模型调用 Worker、队列和批次缓存；
- PostgreSQL 服务、启动脚本、运行时预检和迁移文件；
- Vue 页面、弹窗、样式和前端测试；
- pytest、测试夹具和测试数据；
- 设计规格、需求追溯和部署说明文档。

这些内容只能作为本地 Skill/Tool 的实现支撑。

## 3. ERP 隔离边界

### 3.1 必须从运行能力中隔离的内容

- ERP HTTP Client、ERP 登录凭据、ERP 地址和 ERP 用户身份；
- ERP 设计 MCP、Node MCP 子进程和 ERP 设计上传服务；
- ERP 设计订单、图纸、BOM、采购、生产进度和核算清单查询/写入工具；
- ERP 来源的后置绑定、ERP 原生 ID、ERP 操作状态和 ERP 来源回执；
- ERP 专用 Skill、ERP 设计工具目录、ERP 外部路由；
- 本地业务代码中对 ERP `source_system`、`source_ref`、`native_id`、`erp_user_id` 等外部事实的依赖。

### 3.2 允许保留但不得进入运行链路的内容

远程仓库中未被本地修改的 ERP 文件、历史迁移、审计资料和兼容模块保留原样。它们不应再出现在：

- 当前用户的 Tool/Skill 能力目录；
- Harness 下发给模型的工具集合；
- 自动激活的 Skill；
- 本地业务流程的执行分支；
- 对外可调用的 ERP 路由。

### 3.3 隔离方式

不修改远程原始代码，而在本地允许修改的入口增加运行时隔离：

1. 在能力目录最终生成阶段过滤 ERP 工具和 ERP Skill。
2. 禁止 ERP MCP 工具进入 `tools/list`、模型工具定义和能力分配结果。
3. 在本地新增业务入口中禁止调用 ERP Client、ERP MCP 和 ERP 后置绑定。
4. 对 ERP 外部路由增加本地运行时拒绝边界；不让其成为当前应用的可用 HTTP 能力。
5. 本地业务只允许使用 MoldPilot 自有数据库、当前会话附件、内部审批、通知和审计。
6. 不能通过本地业务的自然语言参数、附件或工具参数重新指定 ERP URL、ERP ID 或 ERP Token。

这属于“运行时移除”，不是删除远程源文件。若后续要求物理删除远程 ERP 文件，则必须另行批准放宽“远程代码不动”的限制。

## 4. 本地业务能力目录

### 4.1 销售合同文档接收与复核

Skill：`sales_contract_intake`

职责：

- 接收当前会话 PDF；
- 预分类、人工确认文档类型和合同分组；
- 查询 OCR、失败和重试状态；
- 查询机器字段、标准化字段、页码和来源块；
- 人工确认项目、模具、合同关系和字段；
- 准备本地销售合同草稿审批。

Tool：

- `query_uploaded_files`
- `prepare_document_intake`
- `query_document_intake`
- `prepare_document_type_confirmation`
- `prepare_document_ocr_retry`
- `query_sales_contract_intake`
- `prepare_sales_contract_intake_review`
- `prepare_sales_contract_from_intake`

PaddleOCR 和文档模型只能作为后台识别组件，不能作为用户直接调用的 ERP 或外部接口工具。

### 4.2 中标到开工通知

Skill：`bid_to_start_notice`

职责：

- 查询已人工确认的中标事件；
- 准备中标匹配和项目候选确认；
- 追加同一中标接收记录的不可变版本；
- 生成内部开工通知草稿；
- 记录超级管理员承接、整套委外或拒绝决定；
- 向设计、采购、制造、装配、财务等部门分发；
- 记录各部门收到/未收到回执；
- 准备合同与项目资料的本地匹配候选。

Tool：

- `query_confirmed_bid_notices`
- `prepare_bid_notice_match`
- `prepare_bid_project_match`
- `prepare_bid_intake_confirmation`
- `prepare_start_notice`
- `query_admin_start_notices`
- `prepare_admin_start_notice_update`
- `prepare_admin_start_notice_decision`
- `prepare_admin_start_department_dispatch`
- `prepare_admin_start_department_ack`
- `prepare_department_ack`
- `prepare_project_start_decision`
- `prepare_contract_match_confirmation`
- `prepare_post_start_binding`

所有绑定只引用 MoldPilot 自己的项目、模具、合同和文件版本，不再支持 ERP 核算清单或 ERP 原生档案绑定。

### 4.3 本地模型供应商与模型目录配置

Skill：`model_provider_configuration`

职责：

- 查询本地供应商和模型目录；
- 检测供应商模型目录；
- 新增、修改、停用、删除供应商；
- 新增、批量新增、修改、停用模型；
- 设置默认模型；
- 配置思考策略、档位、超时和连接参数；
- 保证文档模型和聊天模型的身份隔离。

建议 Tool：

- `query_model_catalog`
- `prepare_model_provider_discovery`
- `prepare_model_provider_save`
- `prepare_model_provider_update`
- `prepare_model_provider_remove`
- `prepare_model_save`
- `prepare_model_batch_save`
- `prepare_model_remove`
- `prepare_default_model_change`

这些 Tool 仅允许超级管理员使用。供应商检测是模型基础设施能力，不是 ERP 接口；检测结果只能说明目录可见，不能推断模型可调用能力。

### 4.4 文档专用模型配置

Skill：`document_model_configuration`

职责：

- 查询当前固定文档模型；
- 修改文档模型引用；
- 校验文档模型供应商、模型和思考档位；
- 阻止聊天模型切换覆盖文档模型；
- 阻止停用仍被文档识别引用的模型。

该 Skill 只管理本地模型配置，不暴露 API Key，不把密钥写入 Proposal、审计或模型上下文。

### 4.5 已存在的本地协作能力

当前工作区已有的工程联络、合同、计划、治理和运行就绪能力，只有在对应文件属于本地允许修改范围时才可调整。远程未修改的既有 Skill/Tool 不改名、不重构、不删除。

## 5. Skill 通用契约

每个本地业务 Skill 必须包含：

1. 触发语和适用条件；
2. 首轮只读 Tool；
3. 参数不足、多候选、权限不足和版本冲突处理；
4. `prepare_*` Tool 只生成持久化 Proposal；
5. 用户本人确认后才执行写入；
6. 执行前重新校验权限、版本、文件所有权和状态；
7. 明确“建议、审批、执行、复验、关闭”不可互相替代；
8. 返回来源、时间、限制和下一步；
9. 幂等和失败恢复规则；
10. 不调用 ERP，不接受任意外部 URL、外部 ID 或外部 Token。

## 6. 本地 Tool 通用契约

- 查询 Tool 必须只读，返回 MoldPilot 数据、来源和截至时间。
- 写入 Tool 必须使用 `prepare_*` 命名并生成 Proposal。
- Proposal 绑定用户、Run、业务对象、版本、附件和权限指纹。
- 确认不能由模型、Worker 或外部 ERP 令牌完成。
- 确认后必须在同一事务中保存业务记录、审计事件、通知和幂等回执。
- 业务对象版本变化、授权变化、部门/人员资格变化或附件替换时，旧 Proposal 失效。
- 所有实际动作只落本地 MoldPilot 数据库和本地私有附件。

## 7. 允许修改范围

后续实现只允许修改：

- 当前工作区已经被本地修改的文件；
- 当前工作区已经新增的文件；
- 为本地 Skill/Tool 新增的文件；
- 为运行时隔离新增的本地入口保护代码；
- 对应的本地测试、契约和文档。

禁止修改：

- `git diff` 中未出现的远程原始行；
- 远程仓库中未被本地修改的 ERP 文件；
- 远程既有工程联络、计划、审批等代码；
- 远程历史迁移和远程测试实现。

实现时必须先保存允许修改文件白名单，并对每次改动执行 diff 范围检查。

## 8. 验证策略

不运行前端构建，不执行数据库迁移，不联调 ERP。

后端验证包括：

- 能力目录不再返回 ERP Tool/Skill；
- ERP MCP Tool 不进入 Harness 工具列表；
- ERP 外部路由访问被本地运行边界拒绝；
- 本地文档接收、合同复核、中标到开工、部门回执、合同候选和模型配置 Tool 均有 schema；
- 每个写 Tool 都先产生 Proposal，未确认前不写库；
- 确认后写入本地数据库、审计和通知；
- 权限、版本、幂等、失败重试和撤权路径通过测试；
- 代码检查只能覆盖允许修改的本地文件。

验证结果不得声称 ERP 已联调或 ERP 已删除，只能说明当前 MoldPilot 运行链路不再暴露或调用 ERP 能力。

## 9. 交付顺序

1. 固化本地修改文件白名单。
2. 建立 ERP 运行时隔离层。
3. 补齐本地业务 Skill/Tool 目录和契约。
4. 将本地业务工具从 ERP 语义中解耦。
5. 补充本地权限、Proposal、确认、审计和幂等测试。
6. 更新本地能力文档和需求追溯。
7. 由潘总审阅规格后，再编写实现计划。
