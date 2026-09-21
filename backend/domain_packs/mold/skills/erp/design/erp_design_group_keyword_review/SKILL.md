# ERP 分组关键词查询与维护

查询 ERP 设计分组关键词时，必须调用 `erp_design_query_group_keywords` 取得当前库中记录。数据来自 ERP 的 `design_group_keyword` 表，通过现有 MCP 和 `/design/group-keyword/list` 接口读取。

- 查询全部关键词时省略 `keyword_text`；筛选时传入用户给出的 `keyword_text`，ERP 按关键词文本模糊查询。不要传模具号、材质、分类或状态等该表没有的筛选条件。
- 使用 `page_num`、`page_size` 分页，默认第一页、每页 500 条。根据 ERP 的 `total`、`hasNext` 说明本次返回范围；需要继续读取时再查询下一页，不把一页说成全部。
- 完整 ERP 回执由对话中的只读表格展示，字段为记录 ID、关键词、备注、创建时间和更新时间。回答简述匹配总数、返回条数及查询时间即可，无需重复抄写完整表格。
- 空结果如实说明 ERP 未找到匹配记录；失败如实说明查询失败。不能用模型知识、历史回答或预置关键词代替查库结果，也不能自行补充分类或启停状态。
- 仅在用户明确要求新增、修改或删除时使用 `erp_design_manage_group_keyword`。先查询并核对记录 ID、关键词及备注，展示变更内容；用户确认后再传 `confirm: true`，以 ERP 写入回执为准。
