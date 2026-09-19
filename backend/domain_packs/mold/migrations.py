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

# Model objects added after the split may live on a table owned by the frozen
# legacy snapshot.  They belong exclusively to the authoritative domain stage
# and must not be reported as drift while an old all-in-one database is being
# adopted.  Keep this list explicit so the compatibility check cannot silently
# ignore unrelated model changes.
LEGACY_MODEL_EXCLUSIONS = frozenset({
    ("column", "design_detail", "source_system"),
    ("column", "design_detail", "source_resource_type"),
    ("column", "design_detail", "source_resource_id"),
    ("column", "design_detail", "source_resource_version"),
    ("column", "design_detail", "source_as_of"),
    ("column", "design_detail", "source_snapshot_hash"),
    ("column", "design_detail", "source_summary"),
    ("column", "design_detail", "source_snapshot"),
    ("index", "design_detail", "ix_design_detail_source_resource"),
    ("column", "payment_stage", "ratio_percent"),
    ("column", "payment_stage", "trigger_event"),
    ("column", "payment_stage", "trigger_date"),
    ("column", "payment_stage", "credit_days"),
    ("column", "payment_stage", "expected_due_date"),
    ("column", "payment_stage", "schedule_confirmed"),
    ("column", "payment_stage", "schedule_evidence"),
    ("column", "payment_stage", "trigger_evidence"),
    ("column", "payment_stage", "special_mark"),
    ("check_constraint", "payment_stage", "payment_stage_ratio_percent"),
    ("check_constraint", "payment_stage", "payment_stage_credit_days"),
    ("check_constraint", "payment_stage", "payment_stage_due_after_trigger"),
    ("check_constraint", "payment_stage", "payment_stage_confirmed_schedule_fields"),
    ("check_constraint", "payment_stage", "payment_stage_trigger_evidence"),
})
