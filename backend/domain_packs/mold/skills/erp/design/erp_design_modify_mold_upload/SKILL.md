本技能处理 ERP 设计上传页面中“类型”选择“改模”的采购清单流程。它适用于上传改模钢料清单或改模五金清单，ERP 业务值固定为 `repair_other`。它不处理修改后 DXF 图纸的异常比较、数量确认、订单关联或加工商响应；这些属于 `erp_design_mold_repair`。也不得把本技能用于设备维修、模具保养或普通设变申请。

用户要求上传修模/改模采购清单，或明确说“上传时类型选改模”时，确认当前会话中有一份 XLSX、XLS 或 CSV 附件，调用 `erp_design_parse_modify_mold_upload`。默认省略 `sheet_type` 或使用 `auto`，由 ERP 按现有表头规则识别钢料或五金；只有用户明确指定时才传 `steel` 或 `hardware`。该工具会把 `designOrderType` 固定为 `repair_other`，不得改用新模解析工具，也不得把 `repair_other` 当作请购原因。

解析只建立 ERP 上传会话。若 ERP 正在处理图纸，使用 `erp_design_get_drawing_status` 等待完成，再用 `erp_design_get_upload_result` 读取完整表格。图纸匹配、预览、无图重匹配、钢料核价、公差判断和按图纸自动修正复用设计上传的共享工具；这些工具的结果不改变本会话的改模业务类型。只有用户明确要求重新匹配无图明细时，才在确认后调用 `erp_design_rematch_no_drawing`。

导入前用 `erp_design_validate_rows` 校验当前明细，并使用 `erp_design_get_modify_mold_approval_config` 读取 `repair_other` 对应的审批配置。不得使用新模审批配置工具代替。ERP 的目标流程是“设计修改模审批”；最终流程名称、节点和状态以 ERP 当次回执为准。

改模导入必须提供交期和请购原因。请购原因只能使用 ERP 当前枚举：`customer_change`（客户设变）、`design_abnormal`（设计异常）、`machining_abnormal`（加工异常）、`assembly_abnormal`（组立异常）、`trial_mold_abnormal`（试模异常）、`outsource_abnormal`（外协异常）、`process_improvement`（制程改善）、`other_abnormal`（其他异常）。用户只给出中文时转换成对应枚举；语义不明确时先请用户选择，不得自行猜测。

用户在 ERP 清单弹窗中明确说“把交期改为某日”或“交期改为 N 天后”时，可将解析出的今天或未来日期写入弹窗交期草稿；这只更新待导入表单，仍须用户点击“确认导入”才提交 ERP。明确修改的日期早于今天时必须拒绝并要求重新输入；用户只发送一个日期时，先追问是否要把交期改为该日，收到肯定答复后才更新草稿。

只有用户已经核对模号、清单类型、明细、交期、请购原因、紧急程度和备注，并明确确认“导入并发起审批”后，才调用 `erp_design_import_modify_mold`，传入 `confirm_import: true`。该工具会再次调用 ERP 校验，随后以 `designOrderType: repair_other`、`importMode: new_request` 导入，不能降级为 `new_model`。

如果 ERP 返回重复上传提示，展示重复单号或其他 ERP 证据，不得直接重试。只有用户针对该重复上传再次明确确认后，才能以相同会话和明细传 `allow_duplicate: true`。解析成功、图纸处理完成、明细校验通过或审批配置读取成功都不等于请购已创建；只有导入回执才能证明已创建并发起相应流程，审批通过、采购下单和加工完成仍需后续 ERP 状态证明。
