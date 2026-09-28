"""Mold-specific proposal cards and side-effect confirmation handlers.

The generic harness knows only the handler protocol. Replacing this pack can
replace these tool/action mappings without changing the harness or UI routes.
"""
from dataclasses import dataclass
from importlib import import_module

from domain_packs.mold.tools.erp.design import erp_design_mcp


DELIVERY_LOGISTICS_MODULE = "domain_packs.mold.erp.procurement.delivery_logistics"
ASSEMBLY_TRIAL_MODULE = "domain_packs.mold.tools.erp.manufacturing.assembly_trial_tools"


@dataclass(frozen=True)
class ProposalHandler:
    action: str
    module: str
    tools: frozenset[str]

    def matches(self, tool: str) -> bool:
        return tool in self.tools

    def implementation(self):
        return import_module(self.module)


HANDLERS = (
    ProposalHandler("model_configuration.execute", "domain_packs.mold.tools.local.model_configuration_tools", frozenset({
        "prepare_model_provider_save", "prepare_model_save", "prepare_model_default",
    })),
    ProposalHandler("local_change.execute", "domain_packs.mold.tools.local.change_intake_tools", frozenset({
        "prepare_local_change_intake", "prepare_local_change_association", "prepare_local_change_acceptance",
    })),
    ProposalHandler("document_workflow.execute", "domain_packs.mold.erp.commercial.document_workflow", frozenset()),
    ProposalHandler("contact.execute", "domain_packs.mold.tools.erp.change.contact_tools", frozenset({
        "prepare_contact_create", "prepare_contact_note", "prepare_contact_task", "prepare_contact_form_tasks",
        "prepare_contact_assign", "prepare_contact_respond", "prepare_contact_attach",
        "prepare_contact_review", "prepare_contact_close", "prepare_contact_cancel_task",
        "prepare_contact_set_reviewer", "prepare_contact_resolution",
    })),
    ProposalHandler("project_control.execute", "domain_packs.mold.tools.erp.project.project_control_tools", frozenset({
        "prepare_project_pause", "prepare_project_resume",
    })),
    ProposalHandler("project_closure.execute", "domain_packs.mold.tools.erp.project.project_closure_tools", frozenset({
        "prepare_project_closure_checklist", "prepare_project_termination",
        "prepare_project_closure_item", "prepare_project_normal_close",
        "prepare_project_settlement_close",
    })),
    ProposalHandler("project_plan.execute", "domain_packs.mold.tools.erp.project.plan_tools", frozenset({
        "prepare_project_plan_draft", "prepare_project_plan_baseline", "prepare_project_plan_change",
        "prepare_plan_department_confirmation",
    })),
    ProposalHandler("internal_start.execute", "domain_packs.mold.tools.erp.project.start_tools", frozenset({"prepare_internal_start"})),
    ProposalHandler("mold_handoff.execute", "domain_packs.mold.tools.erp.project.mold_handoff_tools", frozenset({"prepare_project_mold_handoff"})),
    ProposalHandler("quote_acceptance.execute", "domain_packs.mold.tools.erp.commercial.quote_tools", frozenset({"prepare_quote_acceptance_decision"})),
    ProposalHandler("quotation.execute", "domain_packs.mold.tools.erp.commercial.quotation_tools", frozenset({
        "prepare_quotation_form", "prepare_quotation_version", "prepare_quotation_feedback",
    })),
    ProposalHandler("bid_intake.execute", "domain_packs.mold.tools.erp.commercial.bid_intake_tools", frozenset({
        "prepare_bid_intake_draft",
    })),
    ProposalHandler("bid_start.execute", "domain_packs.mold.tools.erp.commercial.bid_start_tools", frozenset({
        "prepare_bid_notice_match", "prepare_bid_project_match",
        "prepare_bid_intake_confirmation", "prepare_start_notice",
        "prepare_department_ack", "prepare_project_start_decision",
        "prepare_post_start_binding",
    })),
    ProposalHandler("admin_start_notice.execute", "domain_packs.mold.tools.erp.commercial.admin_start_notice_tools", frozenset({
        "prepare_admin_start_notice_update", "prepare_admin_start_notice_decision",
        "prepare_admin_start_department_dispatch", "prepare_admin_start_department_ack",
        "prepare_contract_match_confirmation",
    })),
    ProposalHandler("contract.execute", "domain_packs.mold.tools.erp.commercial.contract_tools", frozenset({
        "prepare_contract_record", "prepare_contract_signing_record",
    })),
    ProposalHandler("document_intake.execute", "domain_packs.mold.tools.erp.commercial.document_intake_tools", frozenset({
        "prepare_document_intake", "prepare_document_type_confirmation",
        "prepare_document_ocr_retry", "prepare_sales_contract_intake_review",
    })),
    ProposalHandler("contract_intake.execute", "domain_packs.mold.tools.erp.commercial.contract_intake_tools", frozenset({
        "prepare_sales_contract_from_intake",
    })),
    ProposalHandler("design_approval.execute", "domain_packs.mold.tools.erp.design.design_approval_tools", frozenset({
        "prepare_design_order_approval",
    })),
    ProposalHandler("assembly_trial.execute", ASSEMBLY_TRIAL_MODULE, frozenset({
        "prepare_assembly_execution", "prepare_trial_result",
    })),
    ProposalHandler("design_erp.execute", "domain_packs.mold.tools.erp.design.design_action_tools",
                    frozenset(key for key, spec in erp_design_mcp.TOOL_SPECS.items()
                              if spec.get("write") is True)),
    ProposalHandler("full_outsource.execute", "domain_packs.mold.tools.erp.procurement.full_outsource_tools", frozenset({
        "prepare_supplier_material_handoff", "prepare_supplier_material_verification",
        "prepare_supplier_progress_policy",
        "prepare_supplier_progress_report",
        "prepare_outsource_change_negotiation",
    })),
    ProposalHandler("procurement_erp.execute", "domain_packs.mold.tools.erp.procurement.migration_tools", frozenset({
        "prepare_raw_material_split", "prepare_purchase_decision", "prepare_hardware_quote",
        "prepare_raw_material_order", "prepare_hardware_order", "prepare_supplier_delivery_change",
        "prepare_purchase_claim", "prepare_hardware_inquiry", "prepare_supplier_order_decision",
        "prepare_supplier_quote_submit", "prepare_supplier_delivery_create", "prepare_supplier_exception",
        "prepare_purchase_split_adjustment", "prepare_purchase_split_adjustment_submit",
    })),
    ProposalHandler("finance.execute", "domain_packs.mold.tools.erp.finance.finance_context_tools", frozenset({
        "prepare_finance_correction", "prepare_supplier_payment_condition", "prepare_supplier_payment_request",
        "prepare_customer_receivable_schedule", "prepare_customer_receipt_confirmation", "prepare_supplier_payment_confirmation",
        "prepare_supplier_deduction_settlement", "prepare_mold_transfer_receipt",
    })),
    ProposalHandler("delivery_logistics.execute", DELIVERY_LOGISTICS_MODULE, frozenset({
        "prepare_logistics_route", "prepare_logistics_quote", "prepare_customer_delivery_signature",
        "prepare_customer_acceptance", "prepare_outbound_release",
    })),
)


def handler_for_action(action: str):
    return next((handler for handler in HANDLERS if handler.action == action), None)


def handler_for_tool(tool: str):
    return next((handler for handler in HANDLERS if handler.matches(tool)), None)
