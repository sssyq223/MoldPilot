# 采购工作台认领与拆单

版本：0.2

目标：把 RYmodle 的采购责任域、申请认领、办理摘要和询价入口迁移为 MoldPilot 的 ERP 事实读取与人工确认流程。

步骤：
1. 先调用 `query_purchase_workbench_context`，用项目号、模具号或真实采购申请 ID 定位 ERP 申请。
2. 只展示 ERP 返回的申请版本、责任人、材料类别、拆单状态和候选供应商；不得根据自然语言补齐申请明细。
3. 认领申请使用 `prepare_purchase_claim`；五金询价使用 `prepare_hardware_inquiry`。两者都必须由本人确认后执行。
4. 拆单仍使用 `prepare_raw_material_split` 或后续五金拆单工具，确认前必须保留 ERP 版本和数量守恒证据。

边界：查询结果不等于已认领、已询价或已拆单；ERP 版本冲突、权限不足和执行未知时必须停止并重新核对。
