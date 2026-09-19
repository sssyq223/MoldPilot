"""Ordered generic-host and mold-domain migration repositories."""

STAGES = (
    {
        "name": "core",
        "config": "alembic-core.ini",
        "version_table": "alembic_core_version",
    },
    {
        "name": "mold",
        "config": "backend/domain_packs/mold/alembic-domain.ini",
        "version_table": "alembic_mold_version",
    },
)

# Existing deployments are upgraded through this frozen compatibility chain,
# checked for model drift, then stamped into the two authoritative stages.
LEGACY = {
    "config": "backend/domain_packs/mold/alembic.ini",
    "version_table": "alembic_version",
}

# Frozen ownership snapshot at legacy head f60e6c8a3d48.  The compatibility
# repository must never start comparing post-split models, otherwise every
# new domain migration would make an existing legacy installation impossible
# to adopt.  Missing tables in this set still appear as drift.
LEGACY_TABLES = frozenset({
    "agent_approval_delegation", "agent_capability_assignment", "agent_run_file",
    "ai_conversation", "ai_run", "ai_step", "app_user", "app_user_profile",
    "approval_action", "approval_candidate", "approval_instance",
    "approval_proxy_delegation", "approval_seat", "assembly_detail",
    "assembly_execution", "assignment_group", "assignment_member", "audit_event",
    "business_decision_detail", "business_subject", "change_impact",
    "contact_attachment", "contact_case", "contact_record", "contact_resolution",
    "contact_task", "contract_detail", "contract_signing_record", "customer",
    "customer_acceptance_record", "customer_delivery_signature",
    "customer_receipt_confirmation", "delivery_exception", "design_detail",
    "design_item", "engineering_change_detail", "erp_identity", "erp_operation",
    "file_object", "finance_correction_detail", "goods_receipt",
    "human_action_intent", "inbox_event", "login_session", "logistics_quote",
    "logistics_route", "material", "material_binding", "material_review",
    "material_template", "material_template_xlsx_mapping", "mold", "notification",
    "order_price_snapshot", "outbox_event", "outsource_change_negotiation",
    "pause_record", "pause_task_shift", "payment_confirmation",
    "payment_request_detail", "payment_stage", "permission_grant",
    "plan_department_confirmation", "plan_detail", "plan_task", "price_detail",
    "project", "project_closure_case", "project_closure_detail",
    "project_closure_item", "project_closure_item_revision", "project_mold",
    "project_pause_detail", "project_profile", "project_role_config",
    "project_role_member", "purchase_order", "purchase_order_line",
    "purchase_request", "purchase_request_line", "receipt_inspection",
    "risk_analysis_result", "risk_policy", "stock_balance", "stock_movement",
    "supplier", "supplier_deduction_settlement", "supplier_material_handoff",
    "supplier_material_verification", "supplier_progress_policy",
    "supplier_progress_report", "supplier_shipment", "task_dependency",
    "trial_detail", "trial_result", "warehouse", "wf_timer", "workflow_calendar",
    "workflow_category", "workflow_definition", "workflow_escalation_task",
})
