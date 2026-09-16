# MoldPilot · 模具项目智能工作台

独立的 Vue3 + FastAPI Agent 项目。核心是 Agent、Harness、LLM、受控 Tool 和独立编写的 Skills；统一会话在左中区，人工业务操作在右侧可收展工作区。完整范围依据需求 V1.1、技术 V3.6 和用户最新约定。开发前检查 ERP 已有能力：现有业务复用 ERP 接口，正式操作必须人工确认；Agent 新增辅材、办公用品和试模料采购，不重复原材、五金或委外采购。对照见 [ERP_SCOPE_AUDIT.md](docs/ERP_SCOPE_AUDIT.md)，实现进度见 [DEVELOPMENT_STATUS.md](docs/DEVELOPMENT_STATUS.md)。

## 当前本地运行

完整范围按 [V1.1 逐条覆盖表](docs/REQUIREMENTS_TRACEABILITY.md) 跟踪，包含报价、中标、合同上传、项目大节点维护等全部需求。下方列举的模块不是穷尽清单；ERP 有基础接口不等于完成 Agent 的业务流程。

审批统一由 Agent 自建的可配置 BPM 提供，只参考 ERP 既有业务审批规则，不调用旧审批流。ERP 业务执行与 Agent 审批结果分开记录。

管理员通过同一 BPM 配置新模/改模设计上传、采购价格等审批模板。异常处理及用于解决异常的工程联络单也在 Agent 新开发，包含方案评估、审批、整改、复验和关闭；不调用 ERP 异常流程。具体约束见 [BPM_AND_EXCEPTION_CONTRACT.md](docs/BPM_AND_EXCEPTION_CONTRACT.md)。

流程中的采购下单、拆单复用 ERP 已有业务能力；辅材、办公用品、试模料新增采购及发货车辆、物流信息维护由 Agent 完善。审批编排与业务执行分开，不重复开发已有业务动作或建立重复台账。

- 网页：http://127.0.0.1:5173
- FastAPI：http://127.0.0.1:8000/api/health
- 独立 PostgreSQL：以本机 `.env` 的 `MOLD_DATABASE_URL` 为准；当前开发库为 `127.0.0.1:5432/moldpilot`。Navicat 连接后可运行 [verify_moldpilot_navicat.sql](database/verify_moldpilot_navicat.sql) 核对当前库、连接用户、admin 超级管理员、关键表行数和 Alembic 迁移版本。
- 本地模拟账号保存在 `.local/test-accounts.txt`。该文件、`.env` 和 `.local` 原目录不得提交或打包。交接包仅单独导出数据库备份及已登记业务原件，不包含本机密码和运行目录。
- ERP 源码、结构文件只作为关联参考，没有导入旧业务数据，没有修改 ERP，没有建立转发或投影数据库。

已有环境启动命令（分别在项目根目录的终端执行）：

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --no-access-log
```

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -m app.agent_worker
```

```powershell
cd web
npm.cmd run dev -- --host 127.0.0.1
```

Python 使用 3.12；首次搭建可用 `requirements.lock` 安装本次验证的依赖，前端使用 `npm ci`。复制 `.env.example` 后填写独立环境凭据；不要覆盖已有 `.env`。迁移通过 `alembic upgrade head` 执行，必须显式配置迁移账号，运行账号不得持有迁移权限。首次超级管理员使用 `python -m app.bootstrap --username admin --name 管理员` 在终端交互创建。

本机 PostgreSQL 以 `.env` 为唯一权威配置；不要再使用 SQLite 作为开发业务库。当前环境已初始化，不要重复 initdb 或重新播种；如需核对数据库，用 Navicat 连接 `moldpilot` 后运行 `database/verify_moldpilot_navicat.sql`。

命令行也可用同一份 SQL 核对 PostgreSQL 基线：

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe scripts/verify_postgres_baseline.py
```

该脚本只读取 `.env`，拒绝 SQLite，确认连接到 `moldpilot`、检查 `admin` 为启用的超级管理员，并比较数据库 `alembic_version` 与仓库 Alembic head；输出不会包含密码哈希。

本地逻辑备份脚本：

```powershell
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe scripts/backup_postgres.py --dry-run
.venv/Scripts/python.exe scripts/backup_postgres.py
.venv/Scripts/python.exe scripts/restore_postgres.py
.venv/Scripts/python.exe scripts/restore_postgres.py --backup .local/backups/moldpilot_YYYYMMDD_HHMMSS.dump
```

脚本只支持 PostgreSQL，拒绝 SQLite，默认输出到 `.local/backups`，不会提交到 Git。数据库密码只通过 `PGPASSWORD` 环境变量传给 `pg_dump` / `pg_restore`，不会打印到控制台或写入命令参数。恢复脚本默认读取 `MOLD_RESTORE_DATABASE_URL`，应指向 `moldpilot_restore` 这类隔离库；默认 dry-run，不会改库。真实恢复必须额外传 `--execute --i-understand-this-will-change-target-db`，且默认拒绝恢复到主库 `moldpilot`。若本机未安装 PostgreSQL 客户端工具，dry-run 会提示 `pg_dump_available=False` 或 `pg_restore_available=False`，需安装客户端或显式指定路径后再执行正式备份和隔离恢复演练。

日志保留期限也通过 `.env` 显式配置。`MOLD_AUDIT_LOG_RETENTION_DAYS`、`MOLD_APP_LOG_RETENTION_DAYS`、`MOLD_ACCESS_LOG_RETENTION_DAYS`、`MOLD_MODEL_LOG_RETENTION_DAYS` 默认为 `0`，表示尚未确认，不会被运行就绪工具视为已验收。设置具体天数后，仍需补充日志采集位置、脱敏、归档、检索和删除策略的验收证据。

运行就绪工具会只读探测 `MOLD_REDIS_URL`：执行 `PING` / `INFO` / `XINFO`，核对业务事件 stream 和通知消费组是否已初始化；不会创建 stream/group，也不会发布或消费消息。Redis URL 中的密码只显示为布尔状态，不会出现在返回结果中。

部署运行前提也由同一个工具只读核对：Python 运行时、Node/npm、Docker CLI、Docker daemon、Docker compose、前端 `web/dist/index.html` 和后端 API/Agent/消息 Worker 入口文件。该核对不会启动服务、不会构建前端、不会执行 Docker 操作；缺失项会保持 FR-118 部署拓扑门槛未通过。

运行就绪返回中的 `readiness_summary` 会把机器可验证阻断项和仍需人工/实施验收的门槛分开列出。模型回答交付状态时应引用该汇总，不能只因为某个配置存在或某个本机探测通过就宣称整体已交付。

## 模型接入

模型供应商、地址和模型名均通过本机 `.env` 配置；仓库只提供无凭据的示例值。若必须连接私网 HTTP 模型服务，需通过 `MOLD_LLM_TRUSTED_HTTP_ORIGIN` 显式批准精确的主机和端口；此类请求不携带公网密钥、不读取环境代理、不跟随重定向，也不自动回退公网地址。修改配置后需重启 API 和 Agent worker。

除明确配置的私网 HTTP 服务外，模型地址仍要求 HTTPS 并验证证书。连接与读取超时独立设置。历史公网 TLS 兼容选项对当前 HTTP 内网请求不生效，系统代理设置无需修改。密钥只从环境配置读取，不交给浏览器、模型提示词或业务工具。

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe scripts/model_probe.py
```

此命令只向已配置模型发送短测试消息，不读取 ERP 或业务数据库。模型独立探测成功不替代工作台端到端验证。模型生成读取超时仍会明确失败，不伪造结果或把聊天文字当审批回执。

## 验证

```powershell
.venv/Scripts/python.exe -m pytest -q
cd web
npm.cmd run build
```

数据库测试只在显式配置的 `agent_test` 内清理合成数据，禁止把测试 DSN 指向业务库。真实模型验证通过工作台发起查询，可查看持久化工具证据。模型网络请求不放进数据库事务。

当前仅本地开发验证；完整业务、ERP 只读连接、Redis 消费链和 Docker 部署尚未全部完成，不能作为生产发布版本。

## 维护者

- 作者：sssyq
- GitHub：[@sssyq223](https://github.com/sssyq223)
- 邮箱：2372822523@qq.com

