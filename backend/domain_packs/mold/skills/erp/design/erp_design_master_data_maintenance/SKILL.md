查询 ERP 设计材质密度、设计分组规则或分组关键词时，使用唯一的只读工具 `erp_design_query_master_data`。查询材质密度时把牌号放在顶层 `material_mark`；工具默认一次返回三类基础资料，如用户只需其中一类，可用 `include` 收窄为 `densities`、`group_rules` 或 `group_keywords`。

当用户明确要求维护 ERP 的设计材质密度、设计分组规则或分组关键词时，先使用 `erp_design_query_master_data` 和 `erp_design_get_record` 核对当前记录、影响范围和记录 ID。新增、修改、启停或删除前，都要逐项展示拟写入的值及影响；仅在用户明确确认后才传入 `confirm: true` 执行。

材质密度的新增、修改、删除统一使用 `erp_design_manage_density`，通过 `operation` 指定 `create`、`update` 或 `delete`；修改、删除时提供记录 `id`，新增、修改时提供 `material_mark` 和 `density`。

设计分组规则的新增、修改、删除、启用和停用统一使用 `erp_design_manage_group_rule`；通过 `operation` 指定 `create`、`update`、`delete` 或 `set_status`。修改、删除、启停时提供规则 `id`；新增、修改时提供 `keyword_text`，分类、范围、前缀、优先级等字段按需提供；启停时提供目标 `status`。

设计分组关键词的新增、修改、删除统一使用 `erp_design_manage_group_keyword`，通过 `operation` 指定 `create`、`update` 或 `delete`；修改、删除时提供关键词 `id`，新增、修改时提供 `keyword_text` 和可选 `remark`。

删除和停用会影响后续设计清单的分组或核价，不能依据相似名称自动选择记录，也不能在 ERP 拒绝后猜测替代规则。每次变更后以 ERP 回执为准，并说明该变更不会重算或改写既有设计订单。
