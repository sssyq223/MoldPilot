先用 `erp_design_query_bom`、`erp_design_query_bom_report` 或 `erp_design_query_bom_shortage` 核对 BOM、模具和缺料范围。新增、修改、删除使用 `erp_design_manage_bom`，执行前说明目标 BOM 编号与字段变更，并取得用户明确确认。

按模具导入仅可使用当前会话中用户上传的 XLSX 文件。先核对模具 ID、文件名和导入范围，再调用 `erp_design_import_bom` 并设置确认标记。

导入回执中的新增、更新和错误数量是唯一结果；不能将上传或导入请求表述为采购、收货或生产已完成。
