综合查询 ERP 设计基础资料时使用 `erp_design_query_master_data`，可用 `include` 收窄类别。仅查询材质密度时使用材质密度技能的 `erp_design_query_densities`；仅查询分组关键词时使用分组关键词技能的 `erp_design_query_group_keywords`，由 ERP 查库并在对话中展示只读表格。查询设计分组规则时使用 `include: ["group_rules"]`，不附带无关类别。

当用户明确要求维护 ERP 的设计分组规则时，先使用 `erp_design_query_master_data` 和 `erp_design_get_record` 核对当前记录、影响范围和记录 ID。新增、修改、启停或删除前，都要逐项展示拟写入的值及影响；仅在用户明确确认后才传入 `confirm: true` 执行。

材质密度的查询与维护由 `erp_design_density_review` 技能处理。

设计分组规则的新增、修改、删除、启用和停用统一使用 `erp_design_manage_group_rule`；通过 `operation` 指定 `create`、`update`、`delete` 或 `set_status`。修改、删除、启停时提供规则 `id`；新增、修改时提供 `keyword_text`，分类、范围、前缀、优先级等字段按需提供；启停时提供目标 `status`。

设计分组关键词的查询与维护由 `erp_design_group_keyword_review` 技能处理。

删除和停用会影响后续设计清单的分组或核价，不能依据相似名称自动选择记录，也不能在 ERP 拒绝后猜测替代规则。每次变更后以 ERP 回执为准，并说明该变更不会重算或改写既有设计订单。
