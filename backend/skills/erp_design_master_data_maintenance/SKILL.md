当用户要求维护 ERP 的设计材质密度、设计分组规则或分组关键词时，先使用对应查询工具和 `erp_design_get_record` 核对当前记录、影响范围和记录 ID。新增、修改、启停或删除前，都要逐项展示拟写入的值及影响；仅在用户明确确认后才传入 `confirm: true` 执行。

材质密度使用 `erp_design_create_density`、`erp_design_update_density` 和 `erp_design_delete_density`；分组规则使用 `erp_design_create_group_rule`、`erp_design_update_group_rule`、`erp_design_toggle_group_rule` 和 `erp_design_delete_group_rule`；分组关键词使用 `erp_design_create_group_keyword`、`erp_design_update_group_keyword` 和 `erp_design_delete_group_keyword`。

删除和停用会影响后续设计清单的分组或核价，不能依据相似名称自动选择记录，也不能在 ERP 拒绝后猜测替代规则。每次变更后以 ERP 回执为准，并说明该变更不会重算或改写既有设计订单。
