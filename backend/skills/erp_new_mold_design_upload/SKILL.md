当用户要在 ERP 中上传新模钢料或五金设计清单时，先确认当前会话中已附加一份 XLSX 文件，并使用 `erp_design_parse_new_mold_upload`。该工具只建立 ERP 上传会话，不代表图纸处理完成。

随后使用 `erp_design_get_drawing_status` 轮询同一 `session_id`。仅当 ERP 返回图纸处理完成后，使用 `erp_design_get_upload_result` 获取解析明细；如需修改明细或确认可导入性，使用 `erp_design_validate_rows`，价格相关字段变更后使用 `erp_design_reprice_rows`。

如果 ERP 明确返回历史无图明细，先展示受影响数量和当前图纸状态。只有用户明确确认重新匹配后，才可使用 `erp_design_rematch_no_drawing`；随后继续查询图纸状态，不把重新匹配请求当成图纸已归档。

在创建 ERP 请购或审批数据前，先使用 `erp_design_get_approval_config` 展示 ERP 审批配置、解析结果和异常。只有用户明确确认导入后，才可使用 `erp_design_import_new_mold`，且必须传入 `confirm_import: true`、交期和已经核对的明细。检测到重复上传时，说明 ERP 的重复提示并等待用户决定是否允许重复导入。

不得把图纸处理排队、解析成功、明细校验通过或审批配置读取成功表述为 ERP 请购已经创建、审批已经通过或采购已经下单；这些分别以 ERP 的导入及后续业务回执为准。
