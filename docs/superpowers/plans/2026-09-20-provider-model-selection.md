# 供应商多模型与会话思考档位实现计划

> 面向 AI 工作者：在当前主工作区使用 executing-plans 内联执行，每项走 TDD。用户禁止子智能体、worktree、提交、迁移、前端构建和浏览器。

**目标：** 一套供应商连接管理多个模型；安全检测目录、手动添加、会话独立选择模型与真实 API 思考档位。

**架构：** 本机配置 v3 分离连接与模型，旧 profile ID 作为稳定模型引用。目录检测与能力确认分离。后端固定每个 Run 的模型选择，Worker/Adapter 据此调用；前端只展示公开目录及模型能力。

**技术栈：** FastAPI/Pydantic、httpx、现有 JSON 配置、SQLAlchemy/PostgreSQL、Vue。

**规格：** `../specs/2026-09-20-provider-model-selection-design.md`，批准依据为本轮两次“可以”。

## 执行状态（2026-09-20）

- 后端及两个独立前端组件已接入。勾选代表实现与所列回归，不代表真实供应商或页面验收。
- 组合 12 文件 `263 passed`（638.78s）；补充普通账号不可枚举、文档协议保护及旧停用草稿兼容后，相关 7 文件 `84 passed`；追加审批续办/删除停用阻断 3 项通过。最后原生 OpenAI token 参数适配后 9 文件 **216 passed**（241.64s），7 个依赖弃用警告。
- 使用真实临时配置、隔离 PostgreSQL、httpx.MockTransport，以及真实 HTTPX/httpcore 配合网络流替身验证代理 CONNECT/SNI；没有调用真实供应商，没有使用真实 Key 发起测试。并发回归为线程并发，跨进程压力门禁尚未执行。
- `.env` 与真实 `.local/model-config.json` 未改写，原存储仍 v2；读时投影、第一次本人保存才变 v3。文档 GLM profile ID 和 low 保留。部署与校验清单：`.local/backups/model_catalog_20260920_190506/deployment.json`（备份含 Key，不得输出内容）。
- 最后补强代码已重新加载本机四服务：API/5173 代理/PaddleOCR 健康 200，新目录与聊天模型接口未认证 401；PG 会话仍在本机库，原失败 intake 保持 OCR_FAILED/version4，文档待处理作业 0。此为加载证据，不是页面或真实供应商验收。
- 原生 OpenAI、GLM、Ollama 思考模板及实际请求字段已分开；手工声明不代表已确认该供应商支持。逐模型计费“测试调用”按钮未提供；真实目录/生成兼容性及前端交互仍待用户验收。

## 全局约束

- 不读出/打印真实 Key，不在未经确认时发送真实供应商请求；用临时配置和 mock 网络先验证。
- 不写正式业务、模型配置或 .env，不自动重载服务；正式切换在兼容回归通过后单独说明。
- pytest 数据库只允许 `127.0.0.1:55432/moldpilot_test` 串行执行；migration_runtime.upgrade_all 在测试进程内替换为只读现有表检查。
- 仅普通文件格式转换，不执行数据库迁移。保留现有 Document Worker profile ID 和独立 reasoning_effort。
- 用本文件跟踪任务；每阶段保存验证证据，不把未做的 UI/真实请求验收勾选。

## 文件职责

- 新 `backend/app/model_catalog.py`：v3 配置兼容投影、公开元数据、供应商/模型增删改、revision/锁/原子保存。
- 新 `backend/app/model_discovery.py`：受限目录请求、DNS/连接保护、响应归一化及安全错误。
- 新 `backend/app/model_catalog_api.py`：管理员目录管理、检测路由与请求 schema。
- 新 `backend/agent_core/model_capabilities.py`：思考参数契约与合法档位，不包含网络或凭据。
- 新 `backend/app/run_model_selection.py`：当前权限下的模型解析、Run 固定参数、配置变更阻断。
- 修改 `config.py`：v3 兼容读取，旧写接口防降级；公开配置不泄密。
- 修改 `api.py`、`schemas.py`、`internal.py`、`agent_worker.py`、`agent_resume.py`：选择的创建/领取/恢复；不改变 Tool 业务授权。
- 修改 `model_adapter.py`、`ollama_adapter.py`：明确参数适配。
- 新 `web/src/components/ModelProviderSettings.vue`、`ModelSelector.vue`：供应商编辑和聊天选择，不继续扩大 App.vue。
- 修改 `SettingsPage.vue`、`App.vue`、`style.css`：接入组件及会话偏好。

## 任务 1：供应商多模型存储及兼容

- [x] 在 `tests/test_model_catalog.py` 编写临时文件测试：v2 读取不写文件，原模型 ID/GLM 文档引用保持；一个供应商两个模型只存一次 Key；同供应商重复 API ID 拒绝；跨供应商同名允许；revision 过期不覆盖；文档引用删除/停用阻断。
- [x] 运行 `pytest tests/test_model_catalog.py -q`，确认新能力缺失导致红灯。
- [x] 实现 `read_catalog()` / `public_catalog()` / `save_provider(data, provider_id=None, expected_revision=...)` / `save_model(data, model_id=None, expected_revision=...)` / 删除函数。内部 v3 数据形状：

```python
{"version": 3, "default_model_id": "old-profile-id",
 "providers": [{"id": "provider-id", "name": "供应商", "protocol": "company",
                "enabled": True, "base_url": "https://example.com/v1", "api_key": "server-only"}],
 "models": [{"id": "old-profile-id", "provider_id": "provider-id", "model": "api-model-id",
             "name": "显示名", "enabled": True, "max_output_tokens": 8192,
             "context_window": 32768, "max_turns": 12, "reasoning_policy": "default"}]}
```

- [x] 在 config._profile_document 中加入 v3 扁平兼容视图；旧写接口禁止降级 v3。只在保存时转换，读取不修改正式文件。
- [x] 运行新测试及 `tests/test_model_profiles.py`、文档 profile 回归；记录实际输出。

## 任务 2：安全目录检测与管理 API

- [x] 在 `tests/test_model_discovery.py` 使用真实 httpx 客户端和 MockTransport：检查 /v1/models、Authorization、无自动生成请求、名称去重、未保存草稿、旧 Key 复用、换地址拒绝旧 Key、DNS 私网与重定向拒绝、401/404/429/超时/畸形/超大响应。
- [x] 运行该文件，观察红灯；实现 `discover_models(data, transport=None)`，使用本次草稿或已有供应商连接，GET 目录不调用生成。
- [x] DNS 核验后固定目标 IP，保留原 Host 与 TLS SNI；TLS/代理沿用连接配置。上限 2 MiB / 1000 条，受限并发；分页未取完返回 incomplete，不宣称已获取供应商所有模型。
- [x] API 路由定义：GET `/api/model-catalog`；POST/PUT/DELETE `/api/model-providers[/{id}]`；POST/PUT/DELETE `/api/catalog-models[/{id}]`；POST `/api/model-providers/discover`。写入携带 revision；管理员检测/管理，不返回 Key。
- [x] `tests/test_model_catalog_api.py` 验证未认证/非管理员拒绝、草稿检测不保存、保存多模型、错误安全返回及 CSRF；只在隔离库运行。

## 任务 3：能力与实际请求参数

- [x] 新 `tests/test_model_reasoning.py`：GLM low/high/max 请求体；medium/disabled 非法；未知模型只允许默认且不发送参数；Ollama 控制独立；流式/非流式和工具轮次参数一致。
- [x] 能力解析接口 `reasoning_capability(protocol, model, policy)` 返回 `levels/default/parameter`。GLM 使用已有官方核对及实际 low 证据；其余规则只有显式可验证协议才开放，目录名称不构成能力证据。
- [x] `ModelAdapter` 新增受验证的 reasoning 参数，经统一 `_payload` 发送，不改变温度冒充思考；保留文档 `_DocumentAdapter` low 的独立性。
- [x] 保存模型时验证默认档位和预算；公开能力元数据给选择器。运行 Adapter/文档回归。

## 任务 4：按 Run 固定选择，隔离全局配置

- [x] 新 `tests/test_run_model_selection.py`：两个会话分别选择不同模型/档位；默认模型变化不影响排队 Run；重复领取、checkpoint、审批续办参数不丢失；不能伪造路由；禁用/删除模型明确阻断；无 Key 泄露。
- [x] RunInput 增加可选 `model_profile_id`、`reasoning_effort`。发起任务时服务端解析，固定非敏感模型身份/档位/预算到宿主状态并记录安全审计。未提供选择时解析默认，旧 Run 兼容首领固定。
- [x] claim 返回宿主模型选择，checkpoint 保留该状态；Worker 领取后按任务创建 Adapter，不再先用全局模型决定所有任务。凭据仅从服务端目录读取，路由身份变化失败，不静默换模型。
- [x] 文档模型继续走独立 document_model_settings，选聊天档位不写供应商默认或文档 .env。
- [x] 运行 Agent API、Harness、确认续办及文档 profile 定向回归。

## 任务 5：设置页与图二风格选择器

- [x] 供应商组件：连接草稿、Key 留空保留、显式检测、搜索/多选添加、手动添加、每模型参数；目录结果与输入版本绑定。成功检测不冒充成功调用。
- [x] 独立 ModelSelector：供应商分组、模型显示名/ID、离散思考滑条、恢复默认、不支持提示；暗色/窄屏/键盘与弹层关闭。
- [x] App 保存按用户/会话隔离的偏好，发送时带模型 ID 和档位；新会话草稿偏好创建后归属会话。清理退出登录状态，不污染别的会话。
- [x] SettingsPage 点击供应商卡仅编辑，不再全局 activate；保留明确管理员默认模型设置。普通用户权限不在此轮扩大。
- [x] 静态审查，前端由潘总验证，不运行构建/浏览器；不以源码断言冒充界面交互通过。

## 任务 6：回归和交付门禁

- [x] 串行跑必要 pytest；差异检查；确认 .env / 真实模型文件未变，未触发真实计费请求。
- [x] 更新 README/开发状态及本计划，区分已实现、mock 验证、真实目录检测和页面验收。
- [x] 兼容回归全部通过后，另行备份真实配置并说明加载操作；不自动迁移数据库，不重试原文档任务。
- [ ] 人工验收：输入供应商→检测目录→勾选多个模型→图四选择同供应商不同模型→切换思考档位→实际请求生效。真实测试调用需明确告知计费；使用非业务合成输入，不发送合同正文。
