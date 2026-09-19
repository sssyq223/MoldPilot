"""Mold-specific proposal cards and side-effect confirmation handlers.

The generic harness knows only the handler protocol. Replacing this pack can
replace these tool/action mappings without changing the harness or UI routes.
"""
from dataclasses import dataclass
from importlib import import_module


DELIVERY_LOGISTICS_MODULE = "domain_packs.mold.erp.procurement.delivery_logistics"


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
    ProposalHandler("contact.execute", "domain_packs.mold.tools.erp.change.contact_tools", frozenset({
        "prepare_contact_create", "prepare_contact_note", "prepare_contact_task",
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
        "prepare_project_plan_baseline", "prepare_project_plan_change",
        "prepare_plan_department_confirmation",
    })),
    ProposalHandler("internal_start.execute", "domain_packs.mold.tools.erp.project.start_tools", frozenset({"prepare_internal_start"})),
    ProposalHandler("quote_acceptance.execute", "domain_packs.mold.tools.erp.commercial.quote_tools", frozenset({"prepare_quote_acceptance_decision"})),
    ProposalHandler("contract.execute", "domain_packs.mold.tools.erp.commercial.contract_tools", frozenset({
        "prepare_contract_record", "prepare_contract_signing_record",
    })),
    ProposalHandler("design_approval.execute", "domain_packs.mold.tools.erp.design.design_approval_tools", frozenset({
        "prepare_design_order_approval",
    })),
    ProposalHandler("full_outsource.execute", "domain_packs.mold.tools.erp.procurement.full_outsource_tools", frozenset({
        "prepare_supplier_material_handoff", "prepare_supplier_material_verification",
        "prepare_supplier_progress_policy",
        "prepare_supplier_progress_report",
    })),
    ProposalHandler("finance.execute", "domain_packs.mold.tools.erp.finance.finance_context_tools", frozenset({
        "prepare_customer_receipt_confirmation", "prepare_supplier_payment_confirmation",
        "prepare_supplier_deduction_settlement",
    })),
    ProposalHandler("delivery_logistics.execute", DELIVERY_LOGISTICS_MODULE, frozenset({
        "prepare_logistics_route", "prepare_logistics_quote",
    })),
)


def handler_for_action(action: str):
    return next((handler for handler in HANDLERS if handler.action == action), None)


def handler_for_tool(tool: str):
    return next((handler for handler in HANDLERS if handler.matches(tool)), None)
