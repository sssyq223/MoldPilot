# 本地 PaddleOCR 到 Qwen 文本管线实现计划

> 2026-09-20 已批准修订：本文“prepare 确认后才创建预分类作业”和“Worker 只执行已确认产生的作业”不再适用于自动预分类。上传自身即创建预分类任务，分类结果后才人工确认业务类型；正式业务写入仍受确认保护。接续计划为 `2026-09-20-automatic-document-intake.md`，加入流式文档模型、固定 GLM profile、独立于 Run 的确认来源及原会话后台状态。本文下面的勾选状态是历史实现记录，不能代表新入口或真实合同业务验收完成。

> **面向 AI 代理的工作者：** 必需子技能：使用 executing-plans 逐任务内联实现。项目禁止子智能体；潘总已要求直接在主工作区开发，不创建 worktree、不创建提交。

**目标：** 在既有唯一 `sales_contract_intake` Skill 和 8 个 Agent Tool 不变的前提下，将错误的视觉模型 OCR 改为本地 PaddleOCR GPU 识别图片文字，再由现有 Qwen3 文本模型完成分类和合同字段结构化。

**架构：** Skill 仍只调用 `query_*`/`prepare_*` Tool，prepare Tool 经 proposal 确认后创建异步作业。Document Worker 在后台执行 `PyMuPDF → PaddleOCR（按需）→ 页面文字持久化 → Qwen3 文本结构化 → Schema 校验 → 候选持久化`；Agent Run 不等待、不轮询。PaddleOCR 是本机 Docker 基础设施，不是第二个 Skill，也不注册页面级 Agent Tool。

**技术栈：** Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2、PostgreSQL 18、PyMuPDF、PaddleOCR 3.7.0、PaddlePaddle 3.2.0、CUDA 12.6、Docker Compose、RTX 3050 4GB、现有 Qwen3 OpenAI 兼容文本接口。

**规格：** `docs/superpowers/specs/2026-09-18-sales-contract-pdf-ocr-design.md`

## 全局约束

- 整套业务流程只使用一个 `sales_contract_intake` Skill，不增加 OCR Skill。
- Skill 的 Agent Tool 清单仍为既有 8 个工具；PaddleOCR、PDF 分析和 Qwen3 调用不直接暴露给模型。
- 所有用户发起的写操作继续由 `prepare_*` Tool 生成 proposal；Worker 只执行已确认产生的异步作业。
- 文本型 PDF 直接提取文本层；纯图片 PDF 整页 OCR；图文混合 PDF 合并文本层与 OCR 结果。
- Qwen3 只接收带页码、文字块编号的纯文本，禁止发送 PDF、PNG、图片 URL 或 base64 图片。
- PaddleOCR GPU 为默认，CPU 仅作为人工选择的 Compose Profile；GPU 失败不得静默切换。
- OCR 服务仅监听 `127.0.0.1:18081`，不连接数据库，不持久化 PDF/PNG，不记录识别正文。
- PostgreSQL 测试只允许本机 `127.0.0.1:55432/moldpilot_test`，pytest 严格串行。
- 不运行前端构建或内置浏览器；只允许运行 Vitest 和 `npm run typecheck`。
- 不对 `192.168.3.215:5432/moldpilot` 运行 pytest、Alembic upgrade 或自动 DDL。
- 正式 `mb0` SQL 必须在正式备份恢复库演练后再次取得潘总单独批准。
- `.env`、Token、数据库密码、PDF 原文、页面图片、OCR 全文和模型原始响应不得提交或写日志。
- 当前全部成果继续保留为未提交修改。

---

## 文件结构

### 新增

- `services/paddleocr/app.py`：本机单页 PaddleOCR HTTP 服务、鉴权、健康检查和结果归一化。
- `services/paddleocr/requirements.txt`：固定服务依赖版本。
- `services/paddleocr/Dockerfile`：支持 GPU/CPU 基础镜像参数的服务镜像。
- `docker-compose.ocr.yml`：互斥的 `gpu`、`cpu` Profile。
- `backend/domain_packs/mold/erp/commercial/paddleocr_client.py`：Agent 侧 OCR 服务客户端及响应 Schema。
- `tests/test_paddleocr_client.py`：客户端协议、安全错误和超时测试。
- `services/paddleocr/test_app.py`：服务鉴权、输入限制、归一化和健康检查测试。

### 修改

- `backend/domain_packs/mold/erp/commercial/pdf_analysis.py`：提取文本块、识别图像占比、渲染需 OCR 页面并合并去重。
- `backend/domain_packs/mold/erp/commercial/ocr_provider.py`：删除视觉输入，改为只接收页面文字的 Qwen3 结构化 Provider。
- `backend/app/document_worker.py`：分阶段执行、缓存页面识别结果、续租、重试和安全错误。
- `backend/app/config.py`、`.env.example`、`README.md`：区分 PaddleOCR 服务和 Qwen3 文本模型配置。
- `backend/domain_packs/mold/erp/commercial/contract_intake_models.py`、`backend/domain_packs/mold/models.py`：增加不可变页面识别记录。
- `backend/domain_packs/mold/alembic_domain/versions/mb0d0e000011_sales_contract_pdf_intake.py`：在尚未落地的 mb0 中增加页面识别表。
- `tests/test_contract_intake_schema.py`、`tests/test_contract_ocr.py`：覆盖三类 PDF、缓存、来源回溯和纯文本模型输入。
- `docs/DEVELOPMENT_STATUS.md`、`docs/REQUIREMENTS_TRACEABILITY.md`：更新真实 OCR 状态。

---

### 任务 1：增加不可变页面识别记录

**文件：**
- 修改：`backend/domain_packs/mold/erp/commercial/contract_intake_models.py`
- 修改：`backend/domain_packs/mold/models.py`
- 修改：`backend/domain_packs/mold/alembic_domain/versions/mb0d0e000011_sales_contract_pdf_intake.py`
- 修改：`tests/test_contract_intake_schema.py`

- [x] **步骤 1：编写失败的 Schema 测试**

增加断言：

```python
assert "document_recognized_page" in names
columns = m.DocumentRecognizedPage.__table__.c
assert {
    "intake_file_id", "page_number", "source_kind", "source_sha256",
    "pipeline_version", "text", "blocks", "average_confidence", "text_sha256",
} <= set(columns.keys())
```

再插入同一 `(intake_file_id, page_number, source_sha256, pipeline_version)` 两次，断言第二次触发 `IntegrityError`；插入页码 0、未知来源或超出 0～1 的置信度时也必须失败。

- [x] **步骤 2：运行测试确认失败**

运行：

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest tests/test_contract_intake_schema.py -q
```

预期：FAIL，`DocumentRecognizedPage` 尚不存在。

- [x] **步骤 3：实现模型和 mb0**

模型固定字段：

```python
class DocumentRecognizedPage(IdentityMixin, Base):
    __tablename__ = "document_recognized_page"
    intake_file_id = mapped_column(ForeignKey("document_intake_file.id"), index=True)
    page_number = mapped_column(Integer)
    source_kind = mapped_column(String(20))
    source_sha256 = mapped_column(String(64))
    pipeline_version = mapped_column(String(120))
    text = mapped_column(Text)
    blocks = mapped_column(J)
    average_confidence = mapped_column(Numeric(5, 4), nullable=True)
    text_sha256 = mapped_column(String(64))
```

约束固定为：

- `page_number >= 1`；
- `source_kind IN ('TEXT_LAYER','PADDLE_OCR','HYBRID')`；
- `average_confidence IS NULL OR 0 <= average_confidence <= 1`；
- 唯一键 `uq_document_recognized_page_version` 覆盖文件、页码、页面哈希和管线版本。

mb0 的 downgrade 在删除 `document_intake_file` 前删除该表。

- [x] **步骤 4：运行测试和迁移漂移检查**

严格串行运行 Schema 测试，再把空测试库迁移到 head 并执行 `scripts/migrate.py check`。预期测试通过且 Core/Mold 均无新增操作。

---

### 任务 2：实现 PDF 文本层、图片页和混合页分析

**文件：**
- 修改：`backend/domain_packs/mold/erp/commercial/pdf_analysis.py`
- 修改：`tests/test_contract_ocr.py`

- [x] **步骤 1：编写三类 PDF 失败测试**

测试固定覆盖：

```python
def test_text_pdf_uses_embedded_blocks_without_rendering(): ...
def test_image_pdf_renders_page_for_paddleocr(): ...
def test_hybrid_pdf_keeps_text_blocks_and_renders_image_content(): ...
def test_merge_blocks_deduplicates_same_text_at_overlapping_bbox(): ...
```

每个文字块必须包含稳定 `block_id`、页码、文本、矩形坐标、来源和置信度。纯文本页 `image_png is None`；图片和混合页生成 PNG。

- [x] **步骤 2：运行测试确认失败**

运行 `pytest tests/test_contract_ocr.py -k "text_pdf or image_pdf or hybrid_pdf or merge_blocks" -q`，预期旧 `PDFPage` 没有 blocks/图像覆盖信息。

- [x] **步骤 3：实现页面分析**

使用 `page.get_text("blocks")` 构造文本块，使用 `page.get_image_info()` 的 bbox 计算图像覆盖。满足以下任一条件时整页以 300 DPI 为目标上限渲染；若大画幅页面超过 OCR 服务像素限制，则保持宽高比缩放到安全范围：

- 有效文本字符少于配置阈值；
- 页面存在覆盖面积达到阈值的大图；
- 页面没有文本块。

`merge_page_blocks` 按 bbox 阅读顺序排序；规范化文本相同且 bbox 重叠时优先保留文本层块，否则保留 OCR 块。印章和低置信度块不删除，只标记置信度供后续警告。

- [x] **步骤 4：运行测试确认通过**

运行上述测试以及 `tests/test_contract_ocr.py` 全文件，预期全部通过。

---

### 任务 3：建立本地 PaddleOCR GPU 服务

**文件：**
- 创建：`services/paddleocr/app.py`
- 创建：`services/paddleocr/requirements.txt`
- 创建：`services/paddleocr/Dockerfile`
- 创建：`services/paddleocr/test_app.py`
- 创建：`docker-compose.ocr.yml`

- [x] **步骤 1：编写服务失败测试**

测试覆盖：无 Token 返回 401、非 PNG 返回 415、超过限制返回 413、伪造 OCR 结果被规范化为 `blocks`、`/health` 返回 device/CUDA/Paddle/PaddleOCR/模型版本，且响应和日志中没有图片正文。

- [x] **步骤 2：运行测试确认失败**

在服务测试环境运行 `pytest services/paddleocr/test_app.py -q`，预期模块尚不存在。

- [x] **步骤 3：实现最小服务**

固定接口：

```text
GET  /health
POST /v1/ocr/page?page_number=<正整数>&request_id=<UUID>
Authorization: Bearer <token>
Content-Type: image/png
Body: <单页 PNG 原始字节>
```

固定响应：

```json
{
  "page_number": 1,
  "engine": "PaddleOCR",
  "engine_version": "3.7.0",
  "model_version": "PP-OCRv5",
  "device": "gpu:0",
  "blocks": [
    {"block_id":"p1-b0001","text":"销售合同","confidence":0.99,"bbox":[[1,2],[3,2],[3,4],[1,4]]}
  ]
}
```

服务使用 `PaddleOCR(..., ocr_version="PP-OCRv5", lang="ch", text_detection_model_name="PP-OCRv5_mobile_det", text_recognition_model_name="PP-OCRv5_mobile_rec", use_doc_orientation_classify=True, use_doc_unwarping=False, use_textline_orientation=True, text_recognition_batch_size=1)`；通过进程锁保证一次只推理一页。Token 使用常量时间比较，Uvicorn access log 关闭。

- [x] **步骤 4：实现 GPU/CPU Compose Profile**

Dockerfile 默认参数：

```dockerfile
ARG PADDLE_BASE_IMAGE=m.daocloud.io/docker.io/nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04
FROM ${PADDLE_BASE_IMAGE}
ARG PADDLE_PACKAGE=paddlepaddle-gpu
ARG PADDLE_INDEX_URL=https://www.paddlepaddle.org.cn/packages/stable/cu126/
ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

固定安装 `paddleocr==3.7.0` 和 PaddlePaddle 3.2.0。为避免官方 GPU 镜像约 11.75GB 的压缩层占满 Docker 所在系统盘，GPU Profile 使用 DaoCloud 镜像 `m.daocloud.io/docker.io/nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04` 加 Paddle 国内官方 cu126 wheel；CPU Profile 使用 DaoCloud Ubuntu 镜像加 Paddle 国内官方 CPU wheel，其他 Python 依赖使用清华 PyPI 镜像。两个 Profile 都绑定 `127.0.0.1:18081`，不得同时启动；健康检查以 `python3` 调用 `/health`。模型权重仅持久化到 `paddleocr-models` Docker volume，服务仍不持久化 PDF、PNG 或识别正文。

- [x] **步骤 5：运行服务测试并构建镜像**

先运行服务单元测试，再执行 GPU Profile 构建。不得把模型下载日志、Token 或识别文本加入仓库。

---

### 任务 4：实现 PaddleOCR 客户端和纯文本 Qwen Provider

**文件：**
- 创建：`backend/domain_packs/mold/erp/commercial/paddleocr_client.py`
- 修改：`backend/domain_packs/mold/erp/commercial/ocr_provider.py`
- 修改：`backend/app/config.py`
- 修改：`.env.example`
- 创建：`tests/test_paddleocr_client.py`
- 修改：`tests/test_contract_ocr.py`

- [x] **步骤 1：编写客户端协议失败测试**

使用 `httpx.MockTransport` 断言：Bearer Token、单页 PNG、页码和 request ID 正确发送；非法 bbox、重复 block ID、置信度越界、响应页码不匹配分别返回安全错误码；超时映射为 `OCR_SERVICE_UNAVAILABLE`。

- [x] **步骤 2：编写 Qwen 纯文本边界失败测试**

拦截模型请求并断言：

```python
payload_text = json.dumps(payload)
assert "image_url" not in payload_text
assert "data:image" not in payload_text
assert "base64" not in payload_text
assert "p1-b0001" in payload_text
```

模型字段若引用不存在的 `source_block_ids`，必须返回 `DOCUMENT_FIELD_SOURCE_INVALID`。

- [x] **步骤 3：运行测试确认失败**

预期当前 `OpenAICompatibleOCR` 仍发送图片，且 PaddleOCR 客户端不存在。

- [x] **步骤 4：实现配置和客户端**

新增配置：

```text
AGENT_OCR_SERVICE_URL=http://127.0.0.1:18081
AGENT_OCR_SERVICE_TOKEN=<secret>
AGENT_OCR_SERVICE_CONNECT_TIMEOUT=5
AGENT_OCR_SERVICE_READ_TIMEOUT=120
AGENT_OCR_RENDER_DPI=300
AGENT_OCR_MIN_TEXT_CHARS=40
AGENT_OCR_IMAGE_COVERAGE_THRESHOLD=0.25
AGENT_DOCUMENT_MODEL_BASE_URL=<Qwen OpenAI endpoint>
AGENT_DOCUMENT_MODEL_API_KEY=<optional>
AGENT_DOCUMENT_MODEL=Qwen3-30B-A3B-Instruct
```

旧 `AGENT_OCR_BASE_URL/API_KEY/MODEL` 删除，避免继续表达“视觉模型就是 OCR”。

- [x] **步骤 5：实现纯文本结构化 Provider**

将 Provider 改名为 `DocumentTextProvider`，输入统一 `RecognizedDocument`。分类只发送前 3 页文本；合同字段按 4 页一组发送。每个字段输出增加 `source_block_ids`，Provider 校验这些 ID 存在且页码一致，再从来源块计算 bbox 和机器原值。

- [x] **步骤 6：运行客户端和 Provider 测试**

预期所有请求都只有文字，异常只返回安全错误码。

---

### 任务 5：让 Worker 缓存 OCR 页面并按 Skill 作业状态推进

**文件：**
- 修改：`backend/app/document_worker.py`
- 修改：`tests/test_contract_ocr.py`
- 修改：`tests/test_document_intake_tools.py`

- [x] **步骤 1：编写缓存与重试失败测试**

场景固定为：图片 PDF 第一次 PaddleOCR 成功、Qwen 返回非法 JSON，作业进入重试；第二次运行 Qwen 成功。断言 PaddleOCR 客户端只调用一次、`document_recognized_page` 只有一个不可变版本、最终字段引用该页文字块。

再测试 PRECLASSIFY 只识别前 3 页，人工确认销售合同后的 FULL_CONTRACT 复用前 3 页并识别剩余页面。

- [x] **步骤 2：运行测试确认失败**

预期旧 Worker 每次把原 PDF 直接交给视觉 Provider，无法缓存页面结果。

- [x] **步骤 3：实现分阶段 Worker**

`process_claim` 固定顺序：

1. 校验租约并读取不可变文件；
2. 解析 PDF 页面；
3. 按页面哈希和 pipeline version 查询缓存；
4. 对缺失页调用 PaddleOCR 并以独立短事务写入 `document_recognized_page`；
5. 再次校验/续租；
6. 从缓存组成纯文本 `RecognizedDocument`；
7. 调用 Qwen3 分类或字段提取；
8. 在最终事务中校验租约并保存候选。

数据库中已有同版本页面时使用 `ON CONFLICT DO NOTHING` 幂等复用。日志只记录 job ID、phase、page count、耗时和安全错误码。

- [x] **步骤 4：保持 Agent Tool 边界不变**

断言 `sales_contract_intake` 的 required/optional Tool 集合仍恰好为原 8 个工具；不得新增 `paddleocr_*`、`extract_pdf_*` 或 `document_model_*` Agent Tool。Skill 仍通过 query Tool 读取状态，通过 prepare Tool 生成 proposal。

- [x] **步骤 5：运行 Worker、Tool、Skill 回归**

严格串行运行 `tests/test_contract_ocr.py`、`tests/test_document_intake_tools.py`、`tests/test_model_harness.py`、`tests/test_contract_intake_proposal.py`。

---

### 任务 6：真实 GPU OCR 联调、文档和正式迁移材料重做

**文件：**
- 修改：`README.md`
- 修改：`docs/DEVELOPMENT_STATUS.md`
- 修改：`docs/REQUIREMENTS_TRACEABILITY.md`
- 修改：`docs/superpowers/specs/2026-09-18-sales-contract-pdf-ocr-design.md`
- 修改：`docs/superpowers/plans/2026-09-18-sales-contract-pdf-ocr.md`

- [x] **步骤 1：启动并验证 GPU 服务**

启动 `docker compose -f docker-compose.ocr.yml --profile gpu up -d`。`/health` 必须报告 `device=gpu:0`、CUDA 可用及固定模型版本；用 `nvidia-smi` 验证容器推理期间确有 GPU 进程和显存占用。

- [ ] **步骤 2：运行脱敏真实样本**

覆盖纯文本、纯图片、图文混合、中英文、旋转/倾斜和多页合同。不得把样本、图片、OCR 全文或模型原始响应提交；只保存通过/失败统计、耗时和安全错误码。

当前部分证据：3 份匿名真实图片 PDF 已完成 GPU OCR → Qwen3 纯文本分类，均返回 `OTHER`；真实大画幅样本发现并通过 TDD 修复客户端渲染超过服务像素上限的问题。因本机没有真实销售合同、文本/混合/旋转/多页样本，本步骤仍保持未完成。

- [x] **步骤 3：严格串行回归**

运行合同 Schema、OCR、Document Tool、复核、proposal、文件权限、Harness、附件 Run 和迁移测试；再运行 Vitest、两个 TypeScript 配置、py_compile 和 `git diff --check`。不得运行前端 build。

- [x] **步骤 4：废弃旧正式 SQL 并重新生成**

删除尚未执行的旧 `apply_m40_sales_contract_pdf_20260919_112458.sql`。以已校验正式备份恢复临时库，生成包含 `document_recognized_page` 的新一次性 SQL，带数据库名、Core/Mold 起始 Revision、对象不存在前置断言和执行后校验。

- [x] **步骤 5：在正式备份恢复库演练**

执行新 SQL，断言 Mold Revision 为 `mb0d0e000011`、新增对象和约束完整、两阶段 `scripts/migrate.py check` 无新增操作。删除临时恢复库和演练副本，恢复本机 PostgreSQL 临时配置。

- [x] **步骤 6：再次提交正式迁移审批材料**

向潘总提供备份路径/哈希、新 SQL 路径/哈希、演练结果、锁影响和回滚命令。未获得新的单独批准前不得写正式库。

材料已于 2026-09-19 重新生成并完成完整数据恢复演练；正式库仍未写入，等待潘总单独批准。任务 6 步骤 2 的真实销售合同样本门禁仍未完成，不得据此宣称整份计划完成。

---

## 完成门禁

- 唯一 Skill 和既有 8 个 Agent Tool 边界未变化；
- 图片型与混合型 PDF 经 PaddleOCR 后只把文字交给 Qwen3；
- Qwen 请求中不存在任何图片内容；
- OCR 页面结果按文件、页码、页面哈希和管线版本不可变追溯；
- Qwen 失败重试不重复 OCR；
- GPU Profile 实际使用 RTX 3050，CPU Profile 可人工切换且不会静默降级；
- 所有写业务动作仍通过 proposal；
- 本地严格串行回归和真实脱敏样本通过；
- 新正式 SQL 经恢复演练并获得潘总单独批准后才能执行；
- 不创建 worktree、不创建提交、不写正式库。
