# ERP 分组关键词查询验收

分组关键词现在使用独立的 `erp_design_group_keyword_review` 技能。与材质密度一样，Harness 自动开放对应只读工具并要求 ERP 工具证据。改动位于领域技能配置和工具参数契约，未增加模型猜测或失败兜底分支。

查询工具 `erp_design_query_group_keywords` 经已有 MCP 调用 ERP `/design/group-keyword/list`。本机实际 ERP 工程位于 `D:\work2\management-system`，DAO 从 `design_group_keyword` 表读取，条件为 `is_deleted = 0`，按 ID 倒序排列。对照材质密度的数据表为 `material_density`。

工具显式接收 `keyword_text`、`page_num`、`page_size`，转换为 ERP 的 `keywordText`、`pageNum`、`pageSize`；旧字符串查询也按关键词转换，不再误传 `moldNo`。不支持的筛选条件会被拒绝，避免 ERP 忽略条件后返回全表。默认每页 500 条，完整 ERP 回执保留用于审计和界面展示，模型读取精简的总数和分页信息。

当前对话复用只读表格组件，展示 ID、关键词、备注、创建时间和更新时间。超过 8 条时提供“查看关键词表”入口；空结果明确显示未找到记录，不新增菜单、维护页面或 Agent 业务数据表。原基础资料授权兼容该更窄的只读能力，不增加写入权限。

2026-09-20 只读联调结果：

| 场景 | 实际 ERP 返回 |
|---|---|
| 全部关键词 | 324 条，第一页返回 324 条，无下一页 |
| 包含“定位” | 5 条：定位件、外定位、定位销、定位键、内定位 |
| 包含“导柱” | 0 条，按实际 ERP 结果展示 |
| 无匹配测试文本 | 0 条 |
| 第二页，每页 10 条 | 总数 324，本页 10 条，还有下一页 |
| 材质密度对照 | 32 条 |

验证：相关后端测试 171 项通过；关键词前端测试 4 项通过；Mold/Template 类型检查与 Mold 生产构建通过。浏览器使用真实 ERP 回执检查完整表格弹窗、筛选小表、空结果和分页摘要。

验证限制：现有 `test_persisted_tool_signatures_are_postgresql_jsonb_safe` 因测试环境不符合本地隔离库 `moldpilot_test` 要求而未执行，没有修改或清理业务库。真实模型探针在模型调用阶段返回 `MODEL_NETWORK_ERROR`，因此没有宣称完成真实模型端到端验收；工具直连 ERP 和 Harness 路由回归已验证。

本机运行环境检查：模型服务 TLS 连接返回 `UNEXPECTED_EOF_WHILE_READING`；Agent 共享 PostgreSQL 连接被远端关闭，运行中的 `/api/health` 返回 500。当前后端未启用热重载，未在无法确认任务状态时重启服务。网络和共享库恢复后需重启 API 与 Worker，使新技能注册生效并完成在线对话验收。

2026-09-21 复查：上述连接错误仍存在。`Find-NetRoute` 显示共享库 `192.168.3.215` 的出口是 FlClash 虚拟网卡。模型服务分别使用已配置的 TLS 1.2 和证书验证开启的自动 TLS 协商，均返回同一握手 EOF，因此不能将错误归因于仅限制 TLS 1.2。当前需确认共享库应走局域网还是 VPN，再排查对应网络；未修改代理规则、路由、TLS 校验或数据库地址。

2026-09-21 08:14 更新：共享数据库连接及 `/api/health` 已恢复。确认本机没有排队或运行中的 Agent 任务后，重启了当前工作区的 API 和 Worker，新 API 健康检查返回 200。启动日志为 `.local/logs/keyword-api-20260921-081406.err.log` 与同时间的 Worker 日志。网络配置未改动。

模型服务仍未恢复：除默认配置外，证书验证开启的 X25519 TLS 协商及 Windows curl/Schannel 对同一服务也无法完成握手。没有切换模型、降级证书校验或写入模拟模型答复。当前剩余验收项为真实模型驱动的完整在线对话查询。
