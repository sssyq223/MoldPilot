# 供应商门户履约办理

版本：0.2

目标：迁移供应商报价、接单/拒单、发货和交付异常的 ERP 门户链路，并保留供应商身份边界。

步骤：
1. 先调用 `query_supplier_portal_context`，确认当前 ERP 供应商身份下的报价任务、采购订单和发货记录。
2. 报价必须逐行填写含税单价、税率和交期，使用 `prepare_supplier_quote_submit` 生成确认卡。
3. 接单、拒单或部分接单使用 `prepare_supplier_order_decision`；不得把采购员视角的订单状态当成供应商决定。
4. 分批发货使用 `prepare_supplier_delivery_create`；延期、短缺或质量问题使用 `prepare_supplier_exception`。
5. 只有 ERP 返回业务回执后，才能报告报价、接单、发货或异常已生效。

边界：供应商门户身份由 ERP Token 决定；MoldPilot 不创建本地供应商状态，不绕过 ERP 权限、版本和数量校验。
