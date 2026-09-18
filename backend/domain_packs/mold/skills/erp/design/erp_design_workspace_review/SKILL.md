当用户要求核对 ERP 的设计资料，或使用“设计与物料清单、钢料清单、五金清单、模具物料”等说法时，按问题选择对应工具：设计订单使用 `erp_design_query_orders`，图纸版本使用 `erp_design_query_drawing_versions`，BOM 及采购进度使用 `erp_design_query_bom` 或 `erp_design_query_bom_report`，设变使用 `erp_design_query_changes` 或 `erp_design_analyze_change`，标准件图纸、密度、分组规则和关键词使用对应的设计查询工具。修模或改模图纸异常使用审批批次、委外审批或加工商响应状态对应的读取工具。

出现 `M250238-P4` 这类 ERP 模具号时优先核对 ERP 设计订单；查询设计与物料清单时先查订单定位真实记录，再按需读取订单详情、BOM 或 BOM 报表。需要查看单条记录时使用 `erp_design_get_record`；需要比较图纸改版时使用 `erp_design_compare_drawing_versions`。只依据 ERP MCP 返回的原始状态说明事实。

本技能不提交、评审、确认、执行或删除 ERP 设计订单、BOM、设变和配置；图纸文件预览或下载应在 ERP 原系统完成。
