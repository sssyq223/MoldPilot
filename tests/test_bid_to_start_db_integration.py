from datetime import date
from uuid import uuid4

from bid_start_db import bid_context, bid_db  # noqa: F401
from domain_packs.mold import models as m
from domain_packs.mold.erp.commercial.bid_start_workflow import (
    confirm_intake_revision,
    confirm_project_match,
    consume_confirmed_bid_notice,
)
from domain_packs.mold.erp.commercial.post_start_binding import bind_post_start
from domain_packs.mold.erp.project.start_notice_workflow import (
    create_start_notice,
    decide_project_start,
    record_department_ack,
)
from domain_packs.mold.ports.db import now


def test_project_match_intake_confirmation_and_start_notice_are_separate_gates(bid_context, monkeypatch):
    context = bid_context
    db = context.db
    match = consume_confirmed_bid_notice(db, context.owner, context.event.id)
    match_result = confirm_project_match(
        db, context.owner, match.id, expected_row_version=1,
        project_id=context.project.id, project_version=context.project.row_version,
        match_evidence="合成测试人工核对", operation_id="match-op-1",
    )
    assert match_result["status"] == "MATCHED"

    case = m.BidIntakeCase(project_id=context.project.id, created_by=context.owner.id)
    db.add(case); db.flush()
    revision = m.BidIntakeRevision(
        case_id=case.id, version=1, source_kind="UPLOAD", source_ref="synthetic",
        source_fingerprint="b" * 64, received_date=now().date(),
        customer_classification="OTHER", classification_evidence="合成证据",
        classification_confirmed_by=context.owner.id, customer_company="合成客户",
        customer_contact="合成联系人", customer_mold_number=None,
        customer_model_or_material=None, project_name_snapshot=context.project.name,
        amount=None, currency=None, our_recipient="合成人员", external_order_number="ORDER-1",
        external_start_date=date(2026, 9, 25), customer_due_date=date(2026, 9, 30),
        customer_process_confirmed=True,
        customer_process_confirmation_evidence="合成工艺确认",
        matched_quotation_subject_id=None,
        historical_mold_number=None, historical_relation_kind=None, match_result="MANUAL",
        match_evidence="合成匹配", notes="", recorded_by=context.owner.id,
    )
    db.add(revision); db.flush()
    db.add(m.BidIntakeAttachment(
        revision_id=revision.id, file_id=context.blob.id, role="EXTERNAL_START_NOTICE",
        content_sha256=context.blob.sha256, title=context.blob.filename,
    )); db.flush()
    intake_result = confirm_intake_revision(
        db, context.owner, match.id, expected_row_version=2,
        revision_id=revision.id, operation_id="intake-op-1",
    )
    assert intake_result["status"] == "INTAKE_CONFIRMED"

    quote = m.BusinessSubject(
        kind="quote_acceptance", number="QA-" + uuid4().hex,
        project_id=context.project.id, category="commercial", created_by=context.owner.id,
        status="EFFECTIVE", revision=1, remark="合成承接",
    )
    db.add(quote); db.flush()
    db.add(m.BusinessDecisionDetail(
        subject_id=quote.id, source_subject_id=None, decision="ACCEPT",
        execution_mode="INTERNAL", effective_date=date(2026, 9, 20), evidence="合成承接依据",
        amount=None, currency=None,
    )); db.flush()
    mold = m.Mold(internal_number="M-" + uuid4().hex, name="合成模具", status="ACTIVE")
    db.add(mold); db.flush()
    db.add(m.ProjectMold(project_id=context.project.id, mold_id=mold.id)); db.flush()
    for role_key in ("DESIGN_OWNER", "PURCHASE_OWNER", "MANUFACTURING_OWNER", "ASSEMBLY_OWNER", "FINANCE_OWNER"):
        db.add(m.ProjectRoleMember(project_id=context.project.id, role_key=role_key, user_id=context.owner.id))
    db.flush()

    notice_result = create_start_notice(
        db, context.owner, match.id, expected_row_version=3,
        effective_date=date(2026, 9, 21), expected_contract_date=date(2026, 9, 28),
        operation_id="start-op-1",
    )
    assert notice_result["status"] == "DRAFT"
    assert notice_result["version"] == 1
    acks = db.query(m.StartNoticeDepartmentAck).filter_by(
        start_notice_id=notice_result["start_notice_id"],
    ).all()
    assert len(acks) == 5
    assert all(ack.status == "PENDING" for ack in acks)
    assert all(ack.handoff_status == "QUEUED" for ack in acks)
    assert all(ack.recipient_snapshot == [{"user_id": context.owner.id, "name": context.owner.display_name, "department": context.owner.department or "未设置部门"}] for ack in acks)
    assert db.query(m.Outbox).filter(
        m.Outbox.kind == "start_notice.department_handoff",
    ).count() == 5

    replay = create_start_notice(
        db, context.owner, match.id, expected_row_version=3,
        effective_date=date(2026, 9, 21), expected_contract_date=date(2026, 9, 28),
        operation_id="start-op-1",
    )
    assert replay["start_notice_id"] == notice_result["start_notice_id"]

    for department_key in ("DESIGN", "PURCHASE", "MANUFACTURING", "ASSEMBLY", "FINANCE"):
        record_department_ack(
            db, context.owner, notice_result["start_notice_id"],
            department_key=department_key, expected_row_version=1,
            status="ACCEPTED", evidence="合成部门已核对", operation_id="ack-" + department_key,
        )
    decision = decide_project_start(
        db, context.owner, notice_result["start_notice_id"],
        decision="PROJECT_ACCEPTED", reason="合成项目部最终核对",
        operation_id="decision-op-1",
    )
    assert decision["decision"] == "PROJECT_ACCEPTED"

    contract = m.BusinessSubject(
        kind="sales_contract", number="C-" + uuid4().hex,
        project_id=context.project.id, category="sales", created_by=context.owner.id,
        status="EFFECTIVE", revision=1, remark="合成合同",
    )
    db.add(contract); db.flush()
    db.add(m.ContractDetail(
        subject_id=contract.id, customer_id=None, supplier_id=None,
        amount="100.00", currency="CNY", contract_number=contract.number,
        signed_date=None, external_order_number=None, expected_date=None,
        replaces_id=None, relation_type="ORIGINAL", settlement_allocation_evidence=None,
    )); db.flush()
    binding = bind_post_start(
        db, context.owner, decision_id=decision["decision_id"],
        start_notice_id=notice_result["start_notice_id"], target_type="SALES_CONTRACT",
        target_id=contract.id, target_version=1,
        mold_rows=[{"mold_id": mold.id, "line_no": 1}], operation_id="binding-op-1",
    )
    assert binding["target_id"] == contract.id

    monkeypatch.setattr(
        "domain_packs.mold.erp.commercial.checklist_binding.resolve_reference",
        lambda *args, **kwargs: {
            "kind": "accounting_checklist", "id": "42", "project_id": None,
            "revision": 1, "fingerprint": "c" * 64, "mold_no": mold.internal_number,
            "line_count": 1, "source_system": "ERP", "source_type": "production_cost_sheet_file",
        },
    )
    checklist_binding = bind_post_start(
        db, context.owner, decision_id=decision["decision_id"],
        start_notice_id=notice_result["start_notice_id"], target_type="ACCOUNTING_CHECKLIST",
        target_id="erp:production_cost_sheet_file:42", target_version=1,
        mold_rows=[{"mold_id": mold.id, "line_no": 1}], operation_id="binding-op-checklist-1",
    )
    assert checklist_binding["target_type"] == "ACCOUNTING_CHECKLIST"
    assert checklist_binding["target_fingerprint"] == "c" * 64
