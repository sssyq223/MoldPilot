本技能仅处理 ERP **设计模块**的修模/改模图纸流程：上传修改后的 DXF，与当前正式零件图比较，登记修改图纸异常，跟踪设计主管审批、采购或委外订单关联和加工商图纸变更响应。不得把它用于设备维修、模具保养、生产异常或普通设变申请。

上传前确认当前会话中的目标附件是 DXF，原文件名以 `M` + 6 位数字 + `-P` 开头，例如 `M250238-P5修模.dxf`。说明文件名和影响，并取得用户明确确认后调用 `erp_design_upload_mold_repair_drawing`。默认 `skip_vision: false`；只有用户明确要求跳过视觉识别或 ERP 运维明确指示时才设为 `true`。只能使用当前用户、当前会话的附件，不得引用模型看到的任意本机路径。

上传是正式 ERP 写入：ERP 会拆分和比较零件图、登记异常，并按现有规则自动启动能够启动的设计主管审批。上传回执至少核对模号、修改/新增/删除数量、`baselineRequiredCount`、`baselineMatchedCount`、缺少正式基线图的零件、异常明细、是否复用重复上传、审批批次和警告。`deduplicated: true` 表示复用字节完全相同的历史上传，不得表述为又创建了一批异常。正式基线图缺失、入库状态查询失败、接收方未解析或 `blockedReason` 均需原样提示，不得自行补造业务事实。

普通上传已经由 ERP 自动启动可启动的审批，不要再要求用户手工提交，也不要重复调用审批提交工具。只有 ERP 明确返回仍可提交的草稿批次，并且相关阻塞已经解决时，才先用 `erp_design_get_mold_repair_approval` 核对批次、状态、异常范围和阻塞原因；用户确认后可调用 `erp_design_submit_mold_repair_approval_batches`。多个批次必须来自同一次上传。`inbound_blocked` 表示零件已入库形成的受阻批次，不能把它描述为可正常批准。接收方分类覆盖仅允许 `hardware`（五金采购/供应商）或 `attached_order`（附图订购/加工商）。

当上传结果的 `quantityRecognition.status` 为 `needs_confirmation` 时，展示异常 ID、零件号、旧图数量、识别的新图数量、已下单数量和缺少数量。取得用户对正整数 PCS 数量的明确确认后，调用 `erp_design_confirm_mold_repair_quantity`。只有上传人、设计主管或 ERP 授权账号可确认；数量确认不等于审批通过、补料下单或加工完成。

异常与订单关联必须使用 ERP 回执或审批详情给出的真实候选订单和 `orderLineKey`。五金异常只能以 `purchase_order` 关联采购单明细；附图订购异常只能以 `entrust_outsource_order` 关联委外单零件范围。展示异常、订单、行范围和接收方，用户确认后调用 `erp_design_confirm_mold_repair_order_link`。不得根据模号或零件号自行拼造订单 ID 或行键。

加工商响应前先调用 `erp_design_get_mold_repair_processor_response`。只有当前加工商账号已收到对应图纸变更通知、审批已通过且委外工单仍处于允许响应的状态时，才能在用户确认后调用 `erp_design_respond_mold_repair_processor`。`agree` 表示同意改图，不传已加工明细；`processed_feedback` 表示零件已经加工，必须逐条提供原异常 ID、已加工数量、异常类型、说明和可选处置/预计工时。`sourceMessageId` 必须取自 ERP 发给当前账号的原通知，不得编造。

查看设计审批使用 `erp_design_get_mold_repair_approval`；查看委外定标触发的审批使用 `erp_design_get_mold_repair_outsource_approval`。下载异常新旧图、审批图纸或授权包时，从查询结果取得相应异常、批次、订单、组标识和图纸种类，再使用 `erp_design_download_file`；不要猜测标识或扩大当前账号可见范围。

ERP 返回的异常记录、正式图版本、审批状态、订单关联、通知投递和加工商响应是最终业务事实。上传成功不代表审批通过，审批通过不代表采购或委外已经下单，订单关联不代表加工商已收到通知，加工商同意改图也不代表已加工异常已经闭环。
