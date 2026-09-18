目标：使用 ERP 新模钢料上传会话自带的技术要求和公差表，判断当前方料明细的公差档位、长度允许范围、宽度允许范围、厚度允许范围和对角公差。

适用条件：用户说“判断公差”“公差档位”“公差范围”“长宽厚允许范围”或“对角公差”，且已经存在本人发起的 ERP 新模钢料上传会话。

执行：
1. 只调用一次 `erp_design_evaluate_tolerances`，传入 `session_id`。工具会自行读取会话的完整明细和 `techRequirements.tolerance_table`，不需要先调用上传结果工具。
2. 如果用户已在当前页面修改了尚未写回 ERP 的钢料明细，可将当前完整 `preview_rows` 一并传入；否则省略该字段。
3. 展示工具返回的 `toleranceTier`、`lengthAllowedRange`、`widthAllowedRange`、`thicknessAllowedRange` 和 `diagonalTolerance`，并说明非方料或缺少有效长宽的行不适用该公差表。

边界：公差档位和偏差值必须来自该 ERP 会话当次返回的公差表；不在 Agent 提示词、技能或对话中维护另一份固定数值。该工具只读，不代表验收合格、已导入、已审批或已下单。
