# 独立本地浏览器验收

用于当前运行库无法安全升级时，对当前源码做真实浏览器与模型联调。仅使用合成资料，不连接真实 ERP，不替代生产验收。

## 首次准备与启动

在仓库根目录依次运行：

```powershell
.venv/Scripts/python.exe scripts/browser_acceptance.py prepare
.venv/Scripts/python.exe scripts/browser_acceptance.py start
```

需要已有本机 PostgreSQL、Redis、Python 依赖和 web/node_modules。沿用本机数据库连接账号，创建 `moldpilot_browser_acceptance`，正常运行仓库 Core/Mold 迁移和结构检查，再创建合成管理员、项目及客户验收待确认卡。Redis 使用独立的空数据库 15；被占用时拒绝准备。数据库已存在时不覆盖、不采用、不修改版本戳。

该脚本创建的是隔离浏览器验收环境，入口仍为 `http://localhost:5174/`，代理独立 API `127.0.0.1:8001`；它不代表主开发入口。日常开发与本轮验收统一使用 `http://127.0.0.1:5173/`，不启动 5174。`VITE_API_PROXY_TARGET` 可配置开发代理，默认仍为原 API 8000。

本机凭据、独立模型配置、文件存储、进程回执和日志位于忽略目录 `.local/browser-acceptance/`。`runtime.json` 包含合成账号密码和连接凭据，只在本机读取，不提交到 Git 或复制到测试报告。准备时复制现有模型配置，后续验收不改原配置。

启动 API、Agent Worker、Message Worker、Vite 的窗口均隐藏，日志按组件分别保存。`processes.json` 逐个保存进程句柄；已有回执或端口占用时拒绝重复启动。发生部分启动失败时应先检查回执、日志及实际进程，不盲目重跑，不凭端口猜测并终止原服务。本脚本不提供自动删除、重建或重启数据库的操作。

## 浏览器核对

1. 登录合成账号，进入“客户质量验收确认”。展开并核对 `ACCEPTANCE-COMPOUND-001` 的未通过验收、供应商责任和原件依据。该卡由合成种子准备，不能声称由真实模型生成。
2. 本人确认后核对持久化回执、模型恢复答复和历史详情。登记的扣款金额与计划影响只是验收事实，不代表财务扣款或改计划。
3. 分别测试同一会话双事项、新会话不同问法、单事项、普通结束语。对照 `ai_step` 和最终正文，不以 `SUCCEEDED` 作为语义通过标准。
4. 此种子的执行焦点应为 `baseline_plan`，收尾焦点为 `delivery_acceptance`；已有客户签收和未通过验收事实，不能推断后续阶段均未开始。只读查询不能产生新业务记录。
5. 查看已处理确认卡，两项办理按钮应禁用；检查浏览器控制台及错误日志。

2026-09-20 当前模型 Qwen3-30B-A3B-Instruct 的结果见 [接手核查记录](HANDOVER_AUDIT_2026-09-20.md)。复合问题仍有语义失败，保持未验收；本环境不解决原库缺失的历史迁移 `mb0d0e000013`。
