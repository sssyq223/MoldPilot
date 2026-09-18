当用户要维护 ERP 厂内标准件图纸时，先使用 `erp_design_query_standard_hardware` 查询目标目录或文件，确认相对路径、目录归属和现有命名。图纸预览与下载继续在 ERP 原系统完成，不能把目录查询结果说成已查看图纸内容。

上传时，只能使用当前会话中用户已附加的文件。先展示待上传文件、目标文件夹名称和可能的同名风险；用户明确确认后，调用 `erp_design_upload_standard_hardware` 并传入文件 ID、文件夹名称和 `confirm: true`。不接受模型编造的本机路径。

改名或删除前，必须展示当前相对路径和拟执行动作；只有用户明确确认后，才调用 `erp_design_rename_standard_hardware` 或 `erp_design_delete_standard_hardware`。ERP 回执是唯一的上传、改名或删除结果；失败时不得推断文件已被保留或移除。
