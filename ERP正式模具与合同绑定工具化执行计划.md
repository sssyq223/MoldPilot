# ERP 正式模具与合同绑定工具化执行计划

> 状态：待评审。本文只描述实施方案；评审通过前不修改业务代码、不导入 ERP 数据、不建立正式绑定。

## 一、目标

打通以下流程：先在 ERP 导入正式模具 ZIP，确认导入完成后上传合同；合同 OCR 识别项目编号和模具编号；系统查询 ERP 正式项目、正式模具并给出候选；用户逐条确认合同明细与正式模具的关系；确认完成后再进入合同登记和审批。

ERP 继续作为项目和正式模具的权威来源，MoldPilot 保存合同审核结果和 ERP 引用关系，不复制一套可独立修改的 ERP 主数据。

## 二、已确认的 ERP 接口

| 能力 | 接口 | 作用 |
|---|---|---|
| 导入预检查 | `POST /system/mold/import-zip/check` | 检查重复任务、已有模具和冲突 |
| ZIP 导入 | `POST /system/mold/import-zip` | 创建异步导入任务，支持 `project_id` |
| 任务查询 | `GET /system/mold/import-task/{task_id}` | 查询进度、失败明细和最终状态 |
| 任务重试 | `POST /system/mold/import-task/{task_id}/retry` | 用户确认后重试失败任务 |
| 项目查询 | `GET /system/project/list`、`GET /system/project/{project_id}` | 查询正式项目 |
| 模具查询 | `GET /system/mold/list`、`GET /system/mold/{mold_id}` | 查询正式模具 |

接口实现位置：

- `E:\management-system\ruoyi-fastapi-backend\module_admin\controller\mold_controller.py`
- `E:\management-system\ruoyi-fastapi-backend\module_admin\controller\project_controller.py`
- `E:\management-system\ruoyi-fastapi-backend\module_admin\service\project_mold_authority_service.py`

ERP 已有规则：模具已经属于其他项目时，禁止自动改绑；模具编号和项目编号不一致时拒绝建立关系。

## 三、建议封装的工具

### 只读工具

| 工具 | 输入 | 输出 |
|---|---|---|
| `erp_query_projects` | 项目编号、名称、分页 | 项目 ID、编号、名称、状态 |
| `erp_query_molds` | 项目 ID、项目编号或模具编号 | 模具 ID、编号、项目归属、状态 |
| `erp_check_mold_zip_import` | 模具编号 | 是否允许导入、重复任务、冲突原因 |
| `erp_query_mold_import_task` | `task_id` | 状态、进度、失败明细、完成时间 |
| `resolve_contract_project_molds` | 合同分组 ID | 项目候选、模具候选、匹配依据、冲突信息 |

### 写入或准备工具

| 工具 | 作用 | 确认要求 |
|---|---|---|
| `erp_prepare_mold_zip_import` | 校验文件、编号、项目和重复状态，生成预览 | 不需要 |
| `erp_import_mold_zip` | 提交 ERP ZIP 导入任务 | 必须确认 |
| `erp_retry_mold_import_task` | 重试失败任务 | 必须确认 |
| `prepare_contract_mold_mapping` | 生成合同明细与正式模具的绑定建议 | 不需要 |
| `confirm_contract_mold_mapping` | 保存确认后的合同—模具关系 | 必须确认 |
| `prepare_contract_registration` | 生成合同登记提案 | 不需要 |
| `submit_contract_registration` | 提交合同登记或审批 | 必须确认 |

现有 `erp_design_import_new_mold` 是设计清单/BOM 导入工具，不是正式模具 ZIP 主数据导入工具，不能直接替代本方案。

## 四、数据桥接方案

ERP 使用整数 `project_id`、`mold_id`，MoldPilot 使用本地 UUID，不能直接混用。建议保存 ERP 引用关系：

| 字段 | 含义 |
|---|---|
| `local_project_id` / `local_mold_id` | MoldPilot 本地对象 |
| `erp_project_id` / `erp_project_no` | ERP 项目引用 |
| `erp_mold_id` / `erp_mold_no` | ERP 模具引用 |
| `source_checked_at` | 最近核对时间 |
| `source_status` | ERP 当前状态 |

合同模具明细还要保存 `contract_intake_group_id`、`row_key`、`erp_mold_id`、`erp_mold_no`、确认人、确认时间和映射版本。

不建议把 ERP 全量项目和模具复制到 MoldPilot；本地只保存必要的引用和只读摘要。

## 五、实施顺序

### 阶段 1：ERP 适配器

扩展 MoldPilot 的 `ERPClient`，注册固定接口路径，增加项目、模具、导入检查和任务查询方法。统一处理分页、Decimal、401/403、5xx、超时和业务拒绝。正式写入失败或结果未知时不自动重试。

### 阶段 2：正式模具导入

上传 ZIP → 校验文件名和内容中的模具编号 → 调用预检查 → 用户确认 → 创建导入任务 → 查询任务进度 → 任务完成后才能进入合同绑定。

重复任务、正在导入、已完成、文件内容不一致和项目冲突都必须阻止自动提交。

### 阶段 3：项目和模具桥接

以 ERP 项目编号查询项目，以项目 ID 或模具编号查询正式模具，建立本地引用关系。发现编号、项目或 ID 不一致时阻止自动更新。

### 阶段 4：合同匹配

合同 OCR 完成后，用项目编号查询 ERP 项目，再用模具编号查询该项目下的正式模具。每个合同模具行显示候选、匹配依据和状态：自动匹配、人工修正、未匹配、冲突。

### 阶段 5：人工确认和合同登记

用户逐条确认映射；全部模具行、合同字段、项目版本和 ERP 引用均通过检查后，才能生成合同登记提案并提交审批。

## 六、权限和安全边界

建议分开配置：`erp.project.read`、`erp.mold.read`、`erp.mold.import`、`erp.mold.import.retry`、`contract.mold_mapping.confirm`、`sales_contract.create`、`sales_contract.submit`。

- 查询工具可自动执行。
- 导入、重试、绑定确认和合同提交必须人工确认。
- ERP 凭据只在后端保存和解密，不返回前端或交给模型。
- 工具不接收任意 URL、任意路径或原始 SQL。
- ERP 查询失败时显示“无法核对来源”，不能当作空候选继续提交。

## 七、验收用例

必须覆盖：

1. ERP 已有项目，正式模具 ZIP 导入成功。
2. 重复导入、正在导入和失败重试。
3. 模具属于其他项目时禁止改绑。
4. ZIP 文件名与内容模具编号不一致。
5. ERP 401、403、500、超时和结果未知。
6. 合同 OCR 项目编号、模具编号匹配和不匹配。
7. 只确认部分模具行时阻止合同提交。
8. ERP 引用在合同提交前发生变化。

每个异常都必须给出原因和下一步操作，不能只显示“接口不存在”或空下拉框。

## 八、暂停条件

项目或模具查询不唯一、ERP 任务未完成、身份或权限不匹配、编号冲突、ERP 返回结构异常、本地引用和 ERP 不一致时，暂停自动绑定，转人工处理。

## 九、交付物

1. ERPClient 查询和导入方法及统一错误码。
2. 正式模具导入检查、确认、任务查询和重试工具。
3. ERP 项目/模具引用关系。
4. 合同 OCR 到正式模具的候选匹配。
5. 逐行人工确认和审计记录。
6. 合同登记前完整性校验。
7. 必要的 pytest：路径参数、权限、重复导入、项目冲突、ID 映射、异步任务和提交阻断。

## 十、评审后需要确认的决定

1. MoldPilot 是否只保存 ERP 引用，还是增加可搜索的模具缓存摘要。
2. 合同—模具绑定是否只保存在 MoldPilot；目前未发现 ERP 侧已有合同关联接口。
3. ZIP 导入是否必须在 ERP 模具页面完成，还是允许从合同识别页面发起。

以上决定确认后，再进入代码实施阶段。
