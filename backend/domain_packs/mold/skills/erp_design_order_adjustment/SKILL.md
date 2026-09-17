当用户要求修改 ERP 设计订单明细或处理闲置料时，先用 `erp_design_query_orders` 和 `erp_design_get_record` 核对真实订单、明细 ID、当前可编辑状态及 `detail_version`。不得按模号猜测明细 ID，也不得将设计订单详情中的状态推断为采购订单、收货或审批已完成。

修改明细前，向用户说明将修改的材质标识、规格和修改原因；只有用户明确确认后，才可调用 `erp_design_update_order_item` 并传入 `confirm: true`。ERP 返回结果是唯一的修改回执。

处理闲置料时，先展示可用候选、拟使用数量和当前版本。只有用户明确确认使用、部分使用或跳过后，才可调用 `erp_design_save_scrap_decision`；释放既有占用时使用 `erp_design_release_scrap_decision`。并发版本不一致、明细不可编辑或 ERP 拒绝时，说明原因并重新查询，不能重试旧版本。

本技能不调用 ERP 设计订单审批、驳回、重提或采购审批接口。MoldPilot 的审批继续由本项目 BPM 处理。
