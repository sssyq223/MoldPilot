# 本地模型供应商配置

目标：读取和管理 MoldPilot 本机模型供应商、模型目录和默认模型。

步骤：
1. 先调用 `query_model_configuration`，读取公开目录、revision、启停状态和档位能力。
2. 检测或新增模型时先确认供应商地址、协议和模型标识；不接收任意外部 URL。
3. 用户明确要求保存时，调用 `prepare_model_provider_save`、`prepare_model_save` 或 `prepare_model_default` 生成 Proposal。
4. Proposal 只保存非敏感配置元数据；API Key 不由此 Skill 接收、存入会话或回显。
5. 本人确认前不写本机配置；确认时再次校验 revision、文档模型占用和默认模型状态。

边界：普通用户无权访问；目录发现失败、模型档位不支持或 revision 冲突时不静默替换模型。停用或删除被文档识别引用的模型必须明确返回占用门禁。所有配置操作只作用于 MoldPilot 本机，不调用 ERP。
