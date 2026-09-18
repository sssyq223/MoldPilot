目标：使用 D 盘 management-system ERP 已有的钢料价格、重量和加工费规则，核算当前新模上传会话的钢料清单价格。

适用条件：用户说“算价格”“核算价格”“价格核算”“重新核价”“钢料核价”或“核算单价”，且已经存在本人发起的新模上传会话。

边界：`erp_design_reprice_rows` 是钢料专用接口。五金清单直接使用 ERP 解析结果中的有效已审批价、附图核算价和采购数量，不得把五金行发送给钢料核价接口，也不得在 Agent 中另写价目匹配或加工费规则。

步骤：
1. 使用 `erp_design_get_upload_result` 读取本次上传会话的完整明细和 `sheetType`。
2. `sheetType` 为 `steel` 时，把当前已核对的完整 `previewRows` 传给 `erp_design_reprice_rows`。
3. 展示 ERP 返回的核算单价、核算金额、总价、计算过程、警告和错误；不得把核价回执表述为已导入、已审批或已下单。
