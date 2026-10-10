"""MoldPilot BPM templates migrated from the ERP workflow catalog.

The ERP definitions are treated as source evidence only.  These templates are
owned by MoldPilot and deliberately contain no ERP approval API calls.  The
same-order ERP nodes are represented as one ALL stage with a union of the
corresponding project roles, which gives one auditable MoldPilot decision gate
while preserving every required approver.
"""
from copy import deepcopy


def _role(role):
    return {"assignment": {"domain_roles": [role], "department_heads_only": False}}


def _any_roles(*roles):
    return {"assignment": {"domain_roles_any": list(roles), "department_heads_only": False}}


def _pools(*roles):
    return [{"key": role.lower(), "name": role, "mode": "ANY",
             "assignment": {"domain_roles": [role], "department_heads_only": False}}
            for role in roles]


def _node(key, name, *, roles=None, any_roles=None, form_schema=None,
          line_item_scope="document", parallel_group=None, task_type="approval",
          external_action=None, assignment_pools=None):
    node = {
        "key": key,
        "name": name,
        "mode": "ALL",
        "reject_rules": [],
        "line_item_scope": line_item_scope,
        "task_type": task_type,
    }
    if assignment_pools:
        node["assignment_pools"] = assignment_pools
    elif roles:
        node.update(_role(roles[0]) if len(roles) == 1 else _any_roles(*roles))
    elif any_roles:
        node.update(_any_roles(*any_roles))
    if form_schema:
        node["form_schema"] = form_schema
    if parallel_group:
        node["parallel_group"] = parallel_group
    if external_action:
        node["external_action"] = external_action
    return node


DATE_FORM = {
    "fields": [
        {"key": "expected_date", "label": "预计完成日期", "type": "date", "required": True},
        {"key": "evidence", "label": "办理依据", "type": "text", "required": True},
    ],
}
LINE_AWARD_FORM = {
    "fields": [
        {"key": "decision", "label": "逐行定标结论", "type": "enum", "options": ["AWARD", "REJECT"], "required": True},
        {"key": "unit_price", "label": "确认单价", "type": "decimal", "required": True, "min": 0},
        {"key": "remark", "label": "定标说明", "type": "text", "required": True},
    ],
}


def _base(process_key, name, nodes, *, business_type="purchase_request", applicability=None,
          form_schema=None, erp_action=None, source_description=""):
    config = {
        "business_type": business_type,
        "nodes": nodes,
        "metadata": {
            "migration_source": "erp.wf_process_definition",
            "migration_status": "MIGRATED_DRAFT",
            "erp_process_key": process_key,
            "source_description": source_description,
            "approval_owner": "moldpilot_bpm",
        },
    }
    if applicability:
        config["applicability"] = applicability
    if form_schema:
        config["form_schema"] = form_schema
    if erp_action:
        # This is a post-approval business operation label.  The adapter is
        # intentionally invoked only after MoldPilot reaches COMPLETED.
        config["integration"] = {
            "system": "erp",
            "on_approve": "business_operation",
            "action": erp_action,
            "payload_fields": ["resource_id", "snapshot_hash"],
        }
    return {"process_key": process_key, "name": name, "config": config}


def templates():
    """Return all 17 migrated definitions as independent dictionaries."""
    return [
        _base("design_new_model_approval", "设计新模审批", [
            _node("design_review", "设计主管审批", roles=["DESIGN_OWNER"]),
            _node("purchase_review", "采购主管审批", roles=["PURCHASE_OWNER"]),
        ], business_type="design_route", applicability={"design_types": ["NEW_MOLD"]},
        erp_action="design.new_model.commit", source_description="START→设计主管→采购主管→END"),
        _base("design_modify_model_approval", "修改模审批", [
            _node("design_review", "设计主管审批", roles=["DESIGN_OWNER"]),
        _node("management_review", "总经理与模具主管会签", assignment_pools=_pools("PROJECT_OWNER", "MOLD_OWNER"), parallel_group="management_review"),
            _node("purchase_review", "采购主管审批", roles=["PURCHASE_OWNER"]),
        ], business_type="design_route", applicability={"design_types": ["MOLD_CHANGE"]},
        erp_action="design.modify_model.commit", source_description="设计主管→(总经理、模具主管)→采购主管"),
        _base("purchase_request_approval", "采购申请审批", [
            _node("purchase_review", "采购主管审批", roles=["PURCHASE_OWNER"]),
        ], erp_action="purchase.request.commit", source_description="采购主管审批"),
        _base("production_outsource_approval", "排产委外审批", [
        _node("manufacturing_review", "生管审批", roles=["MOLD_OWNER"]),
        ], erp_action="production.outsource.commit", source_description="生管审批"),
        _base("design_order_approval", "设计订单审批", [
            _node("design_management_review", "设计主管与总经理会签", assignment_pools=_pools("DESIGN_OWNER", "PROJECT_OWNER"), parallel_group="design_management_review"),
        ], business_type="design_route", erp_action="design.order.commit", source_description="设计主管与总经理同序会签"),
        _base("purchase_reconcile_internal_approval", "采购对账内部确认", [
            _node("internal_confirm", "品质、仓库确认", assignment_pools=_pools("QUALITY_OWNER", "WAREHOUSE_OWNER"), parallel_group="internal_confirm"),
            _node("purchase_confirm", "采购主管确认", roles=["PURCHASE_OWNER"]),
            _node("finance_confirm", "财务确认", roles=["FINANCE_OWNER"]),
            _node("management_confirm", "总经理确认", roles=["PROJECT_OWNER"]),
        ], erp_action="purchase.reconcile.commit", source_description="品质与仓库同序确认→采购→财务→总经理"),
        _base("outsource_order_approval", "委外下单审批", [
            _node("purchase_review", "采购主管审批", roles=["PURCHASE_OWNER"]),
            _node("management_review", "总经理审批", roles=["PROJECT_OWNER"]),
        ], erp_action="outsource.order.commit", source_description="采购主管→总经理"),
        _base("procure_price_approval", "采购价格审批", [
            _node("price_review", "价格决策审批", roles=["PROJECT_OWNER"], form_schema=LINE_AWARD_FORM, line_item_scope="line_required"),
        ], business_type="purchase_price", erp_action="procurement.price.commit", source_description="价格决策审批"),
        _base("mold_repair_design_change_approval", "修改图纸发布审批", [
            _node("design_change_review", "设计主管审批修改图纸异常", roles=["DESIGN_OWNER"]),
        ], business_type="design_route", erp_action="design.drawing_release.commit", source_description="设计主管审批修改图纸异常"),
        _base("purchase_manual_dispatch_approval", "无人接单直派审批", [
            _node("purchase_review", "采购主管审批", roles=["PURCHASE_OWNER"]),
            _node("management_review", "总经理审批", roles=["PROJECT_OWNER"]),
        ], erp_action="purchase.manual_dispatch.commit", source_description="采购主管→总经理"),
        _base("mold_repair_order_reapproval", "修改图纸关联委外审批（三段）", [
            _node("design_change_review", "设计主管审批修改图纸异常", roles=["DESIGN_OWNER"]),
            _node("purchase_review", "采购主管审批", roles=["PURCHASE_OWNER"]),
            _node("management_review", "总经理审批", roles=["PROJECT_OWNER"]),
        ], business_type="design_route", erp_action="outsource.drawing_change.commit", source_description="设计主管→采购主管→总经理"),
        _base("purchase_supplier_rank_adjustment_approval", "供应商候选排名调整审批", [
            _node("purchase_review", "采购主管审批", roles=["PURCHASE_OWNER"]),
        ], erp_action="purchase.supplier_rank.commit", source_description="采购主管审批"),
        _base("purchase_split_group_adjustment_approval", "临时拆单分组审批", [
            _node("purchase_review", "采购主管审批", roles=["PURCHASE_OWNER"]),
        ], erp_action="purchase.split_group.commit", source_description="采购主管审批"),
        _base("procure_hardware_award_approval", "五金逐行定标审批", [
            _node("line_award", "总经理逐行定标", roles=["PROJECT_OWNER"], form_schema=LINE_AWARD_FORM, line_item_scope="line_required"),
        ], erp_action="procurement.hardware_award.commit", source_description="逐行定标"),
        _base("price_sensitive_access_approval", "价格敏感信息查看审批", [
            _node("price_access_review", "价格敏感信息授权审批", roles=["PROJECT_OWNER"], form_schema={"fields": [{"key": "reason", "label": "查看理由", "type": "text", "required": True}]}),
        ], erp_action="procurement.price_sensitive_access.commit", source_description="价格敏感信息授权审批"),
        _base("supplier_exception_deduction_approval", "供应商异常扣款审批", [
            _node("supplier_exception_review", "供应商异常扣款审批", roles=["PURCHASE_OWNER"], form_schema={"fields": [{"key": "amount", "label": "扣款金额", "type": "decimal", "required": True, "min": 0}, {"key": "reason", "label": "扣款原因", "type": "text", "required": True}]}),
        ], erp_action="supplier.exception_deduction.commit", source_description="供应商异常扣款审批"),
        _base("design_new_model_attached_square_approval", "新模五金附图方料采购审批", [
            _node("design_review", "设计主管审批", roles=["DESIGN_OWNER"]),
            _node("delivery_confirm", "附图方料交期确认审批", roles=["DELIVERY_OWNER"], form_schema=DATE_FORM),
            _node("purchase_review", "采购主管审批", roles=["PURCHASE_OWNER"]),
        ], business_type="design_route", applicability={"design_types": ["NEW_MOLD"]},
        erp_action="purchase.attached_square.commit", source_description="设计主管→交期确认→采购主管"),
    ]


def template_map():
    return {item["process_key"]: deepcopy(item) for item in templates()}


__all__ = ["templates", "template_map"]
