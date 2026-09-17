处理 ERP 设变时，先使用 `erp_design_query_changes`、`erp_design_get_record` 或 `erp_design_analyze_change` 核对目标设变及影响范围。需要查看明细时，调用 `erp_design_query_change_items` 并传入设变编号。

新建、修改、删除、提交、评审、确认设变申请均使用 `erp_design_manage_change`；新增、批量新增、修改、删除、执行明细使用 `erp_design_manage_change_items`。先展示目标编号、操作和 ERP 请求字段，再由用户明确确认。删除、确认和执行不得根据模型推测自行调用。

ERP 回执是设变状态和执行结果的唯一依据。MoldPilot 的工程联络和 BPM 可用于内部协作，但不会替代或伪造 ERP 设变回执。
