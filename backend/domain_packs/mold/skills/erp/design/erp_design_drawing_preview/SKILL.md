目标：预览当前新模上传会话中已经由 ERP 匹配到的正式图纸。

适用条件：用户说“看图纸”“图纸预览”“预览图纸”“查看图纸”或“打开图纸”，并且上传结果行含有 `drawing_resource_id`。

边界：只预览当前账号拥有的上传会话内的图纸。使用 `erp_design_preview_drawing` 从会话明细读取 `drawing_preview_url`，复用 D 盘 ERP 已有的 DWG/DXF 转图和预览逻辑；不得要求用户重新上传正式目录中已经存在的图纸，也不得接受任意 URL。

步骤：
1. 使用 `erp_design_get_upload_result` 确认目标明细及其 `drawing_resource_id`。
2. 使用 `erp_design_preview_drawing`，只传 `session_id` 和该行的 `drawing_id`。
3. 将返回的当前会话私有预览附件展示给用户；空文件或格式异常时如实报告，不把预览失败表述为图纸不存在。
