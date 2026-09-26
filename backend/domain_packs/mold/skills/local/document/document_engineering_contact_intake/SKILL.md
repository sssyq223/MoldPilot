# 工程联络单文档接收

目标：在上传文件已经由本人确认属于工程联络单后，使用文档识别证据和本地项目候选，编排工程联络单创建 Proposal；不直接写业务事实。

激活条件：当前 Agent Run 由工程联络单文档分类确认触发，并绑定原始上传文件。原始文件可以是 PDF、PNG、JPG 或可编辑 DOCX；DOCX 由本地 Office 转换为 PDF 后再识别，原文件仍是业务附件。

步骤：
1. 调用 `query_document_intake`，不传参数读取当前 Agent Run 绑定文件对应的接收批次；如需单文件再传 `file_id`。不要把附件 `file_id` 当作 `document_intake_id`，读取分类快照、字段候选、来源页码/区块和版本；不把模型候选当作已确认事实。
2. 调用 `query_business_object_candidates` 查询项目候选；只有候选唯一且项目版本明确时，才继续准备创建建议。零候选或多候选必须要求本人选择。
3. 向本人展示识别字段、来源证据、项目候选、`ONLINE`/`HISTORY` 办理模式和原始附件版本；字段缺失或来源冲突时停止。
4. 使用 `prepare_contact_create` 生成工程联络单创建 Proposal；不得直接调用数据库、联系人 API 或任何未登记写入动作。
5. 创建 Proposal 经本人确认后，再查询新联络单版本并使用 `prepare_contact_attach` 生成原始附件关联 Proposal。
6. 附件关联确认后，若办理模式为 `ONLINE`，再使用 `prepare_contact_task` 生成责任事项 Proposal；若为 `HISTORY`，只允许补录已发生的线下过程。
7. 后续分派、反馈、处理方案、独立复验和关闭分别遵循 `engineering_contact_collaboration` Skill，不把反馈或方案批准说成执行完成或关闭。

识别前处理：可编辑 DOCX 先在本机转换为 PDF，不调用外部服务；转换结果只作为 OCR/版面解析输入，原始 Word 的 SHA256、派生 PDF 的 SHA256 和转换器信息写入完成事件，转换失败则不进入模型识别。

边界：
- 分类确认不等于工程联络单创建；创建 Proposal 不等于写入。
- 不自动选择项目、责任部门、处理人、紧急程度、处理方案或关闭结果。
- 不接受 ERP ID、外部 Token、任意外部 URL，不调用 ERP HTTP、MCP、远程数据库或远程文件目录。
- 原始附件、页码、来源块、字段置信度和人工修正必须保留为可追溯依据；API Key、Token、完整提示词和 PDF 正文不得写入审计日志。
- 版本、授权、附件摘要或候选变化时重新查询并重新准备 Proposal。

失败恢复：OCR 失败、分类为 OTHER、来源不足、项目候选不唯一、权限变化或版本冲突时保持原记录不变；先查询最新状态，再重新准备。
