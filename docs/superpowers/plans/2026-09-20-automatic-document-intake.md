# 上传即自动文档识别实现计划

> 面向 AI 工作者：使用 executing-plans 在主工作区内联实现，禁止子智能体、worktree、提交。2026-09-20 潘总已批准本轮对话中的入口解耦方案。

**目标：** 上传 PDF 后不发送聊天指令、不创建 Agent Run、不确认接收，即自动预分类并在原会话显示类型确认入口；本人确认销售合同后自动提取字段。

**架构：** 文件保存与识别任务在同一数据库事务提交。通过产品 manifest 的可选上传回调进入既有 contract_intake 服务。批量上传共用一个接收批次，非 PDF 不进入合同识别。确认直接绑定 intake/版本/授权指纹与 HumanIntent，不伪造 Run/Step。后台沿用 Document Worker、页面缓存、纯文本 Provider 与 Outbox/Inbox，文档模型固定引用已验证的 GLM-5.3-Flash profile。既有 Skill/Tool 保留为辅助办理能力，而非上传的必要入口。

**技术栈：** 现有 FastAPI、SQLAlchemy/PostgreSQL、Vue、PyMuPDF/PaddleOCR、OpenAI 兼容纯文本模型。

**2026-09-20 执行状态：** 后端和前端代码已接入，本机服务已加载；勾选表示对应实现/代码回归，不代表页面或合同业务验收。组合回归 262 项、最后相关回归 176 项通过；追加边界验证 28 项通过、1 项因测试凭证长度错误失败，改为等长篡改后该项通过。真实 GLM 22 页只读提取约 92.7 秒，44 个合并候选通过结构/来源校验，不代表准确完整。详细证据见 `docs/DEVELOPMENT_STATUS.md`；部署清单 `.local/backups/document_auto_20260920_171107/deployment.json`。原真实失败任务保持不变。

**仍待验收/补强：** 无聊天的真实页面上传、确认、完整字段及通知；低置信度专项视觉提示；多 Worker 同批并发状态聚合压力验证。总时限当前在流式进度检查点判定，不声称硬截止中断阻塞读取。未运行前端构建或浏览器。

**规格：** 本轮已批准的会话设计优先于 `2026-09-18-sales-contract-pdf-ocr-design.md` 中“接收 proposal 确认后才预分类”的旧入口约束。底层纯文本管线仍遵循 `2026-09-19-local-paddleocr-text-pipeline.md`。

## 全局约束

- 不运行数据库迁移；优先复用现有表。不得代用户确认或重新排队真实失败合同。
- 仅在 127.0.0.1:55432/moldpilot_test 串行运行必要 pytest，测试启动的迁移钩子替换为只读 schema 存在检查。
- 不运行前端构建、浏览器或子智能体。前端页面由潘总实际验收。
- 正文、原始模型输出、密码和 Token 不写日志。自动候选不视为正式事实。
- 不新增 ERP 菜单或 OCR Agent Tool；前端状态直接读取后台任务，不让模型循环查询。
- 真实 Qwen 只读证据：前四页相同输入非流式 60 秒超时；流式首输出 0.17 秒、140.42 秒完成、17 字段，输出 2528 tokens。

## 任务 1：服务端原子上传与自动预分类

文件：`backend/app/files.py`、`backend/domain_packs/mold/manifest.py`、新增 `backend/domain_packs/mold/erp/commercial/document_workflow.py`、`tests/test_automatic_document_intake.py`。

- [x] 先添加 HTTP 上传测试：上传后 intake/PRECLASSIFY 存在，Run/Step/HumanIntent 均为 0；同请求重放不重复排队；批量 PDF 共用一个 intake；非 PDF 不排队；回调失败事务回滚。
- [x] 运行观察失败；再实现可选 manifest `after_files_uploaded(db,user,blobs,request_key)`，在 commit 前调用既有 `contract_intake.create`。
- [x] 批量上传以 multipart 请求一次提交，复用文件名/内容/大小/配额/所有权校验，派生每个文件幂等键；不在前端上传后再调用排队接口。
- [x] 必要时新增并固定 multipart 解析依赖；同时保留原单文件 API 自动处理。
- [x] 重跑新测试及文件权限测试。旧手工接收兼容测试通过明确构造历史未接收文件，不关闭生产自动入口。

测试核心断言：
```python
assert db.scalar(select(func.count()).select_from(m.DocumentOcrJob)) == 1
assert db.scalar(select(func.count()).select_from(m.Run)) == 0
assert db.scalar(select(func.count()).select_from(m.HumanIntent)) == 0
```

## 任务 2：不依赖 Run 的类型确认和状态投影

文件：`document_workflow.py`、新增 `document_workflow_api.py`、`proposal_handlers.py`、`contract_intake.py`、新测试同上。

- [x] 先测试分类候选可查询；查看/创建 intent 不确认类型，不建立全文任务；通过 `/human-actions/{id}/confirm` 才创建 FULL_CONTRACT；重复确认仅一份任务。
- [x] 覆盖跨用户、撤权、归档、版本变化、凭证过期和 payload 篡改；不要求 Agent Tool 授权来触发系统预分类，但确认始终检查当前文件访问权和用户授权指纹。
- [x] 新增只绑定文档来源的 HumanIntent handler，复用现有 `_preview_types` / `_preview_retry` 与领域确认方法；不调用模型，不创建 Step。
- [x] 查询返回安全阶段、时间、尝试次数、下一次重试时间、页面缓存数和终态错误；完成后返回合同候选来源，不自动创建合同或匹配项目。

确认请求形状：
```python
payload = {"operation": "types", "input": validated_input,
           "authorization_hash": fingerprint(db,user), "display_hash": content_hash(display)}
```

## 任务 3：流式字段提取、租约及可恢复批次

文件：`ocr_provider.py`、`document_worker.py`、`config.py`、`.env.example`、`tests/test_contract_ocr.py`。

- [x] 增加先失败测试：流式响应持续生成不受整体 60 秒限制；空闲/总时限仍受控；错误保留细分码；长请求期间续租，失租不提交。
- [x] Provider 改用现有 `ModelAdapter.generate_stream`，设置单批总时限并通过进度回调检查；Worker 以节流短事务续租。
- [x] 批次成功后持久化候选及不含正文的批次完成凭据，重试按输入/模型/管线指纹复用；无字段批次也必须有完成凭据。只在整个作业成功后展示可复核字段。
- [x] 测试第一批成功、下一批失败、重试不重复第一批；最终合并与一次完整结果一致；变更指纹不使用旧批次缓存。
- [x] 修改领取时批次状态同步，区分排队/处理中/重试/失败。记录 job/phase/耗时/安全错误码，不输出正文。

## 任务 4：通知实际送达且定位原会话

文件：`document_worker.py`、`notification_policy.py`、`backend/app/api.py`、`tests/test_automatic_document_intake.py`、`tests/test_messages.py`。

- [x] 增加成功/失败事件从 Outbox 经 deliver 到 Notification 的回归，重复消费不重复通知，撤权不泄露。
- [x] Worker 向 intake 创建人发送事件；通知策略从 job→file→intake 校验拥有者及当前文件访问权。
- [x] 通知定位信息由服务端经当前权限解析到 conversation，不能信任模型提供的路径。

## 任务 5：原会话自动状态与人工确认 UI

文件：新增 `web/src/domain-packs/mold/components/DocumentActivity.vue`、两个业务包的 `uiPolicy.ts`、`web/src/App.vue`、`web/src/api.ts`。

- [x] 使用批量上传接口，取消 Mold 的自动附件 Agent Run；其他业务包保持原行为。
- [x] 在当前会话渲染产品提供的文档活动组件；自动读状态，不向模型发送消息；切会话/退出登录清理旧状态，忽略过期请求。
- [ ] 类型选择可修正，合同分组可编辑，明确重复文件及低置信度警告；两阶段 HumanIntent 核对确认，不把自然语言当批准。
- [x] 处理中显示真实阶段、耗时和重试；失败提供本人确认重试入口；成功显示机器候选来源，通知能返回会话。
- [x] 不运行前端构建或浏览器；做代码审查并交由潘总页面验收。

## 任务 6：回归、文档与运行验收

- [x] 串行 pytest：自动入口、文件、Document Tool、合同 OCR/复核、消息、Agent API/Harness 和领域包边界。
- [x] 更新旧规格、管线计划、README/开发状态，明确自动处理与正式确认边界及未完成验收。
- [x] `git diff --check`；仅必要本机服务在无活跃任务时重启，不改模型 Key、不执行迁移。
- [ ] 区分证据：只读真实 Qwen 复演、自动化 HTTP→Worker→确认→通知回归、潘总页面不发送消息的真实上传验收。未执行的项目不得标记通过。
