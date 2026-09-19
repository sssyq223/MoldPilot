当用户明确要求查询 ERP 图纸版本、查看某一图纸版本或比较两个图纸版本时使用本技能。列表查询先使用 `erp_design_query_drawing_versions`；只有用户明确给出记录 ID 并要求详情时才使用 `erp_design_get_record`，只有明确给出两个版本 ID 并要求对比时才使用 `erp_design_compare_drawing_versions`。

设计订单列表中的订单 ID 不是图纸版本 ID。用户只要求查看设计订单时不得启用本技能，也不得根据订单 ID 猜测图纸版本记录。

本技能只读取 D 盘 management-system ERP 的实时数据，不创建独立页面，不发布、删除或修改图纸版本。
