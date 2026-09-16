from datetime import date, timedelta
from decimal import Decimal


import pytest
from sqlalchemy import select

from app import bpm, business, models as m
from app.authorization import PERMISSIONS, fingerprint
from pg_db import factory as pg_factory
from app.tool_gateway import execute, tool_schema


def factory():
    return pg_factory()


def user(db, username="operator", super_admin=False):
    row = m.User(username=username, display_name=username, password_hash="test", super_admin=super_admin)
    db.add(row)
    db.flush()
    return row


def project(db, code, name="财务节点项目", status="ACTIVE"):
    row = m.Project(code=code, name=name, status=status)
    db.add(row)
    db.flush()
    return row


def grant(db, admin, target, permission, project_id=None, category=None, fields=None):
    scope = {}
    if project_id:
        scope["project_id"] = [project_id]
    if category:
        scope["category"] = [category]
    db.add(
        m.Grant(
            user_id=target.id,
            permission=permission,
            effect="ALLOW",
            scope=scope or {"all": True},
            fields=fields or PERMISSIONS[permission],
            reason="unit test",
            granted_by=admin.id,
        )
    )


def capability(db, target, key, kind="TOOL"):
    db.add(m.Capability(user_id=target.id, kind=kind, key=key, enabled=True))


def profile(db, project, owner, mode="FULL_OUTSOURCE"):
    customer = m.Customer(code="FIN-CUS", name="财务客户")
    mold = m.Mold(internal_number="FIN-MOLD-001", name="财务模具")
    db.add_all([customer, mold])
    db.flush()
    db.add(m.ProjectProfile(project_id=project.id, customer_id=customer.id, owner_user_id=owner.id, execution_mode=mode, customer_due_date=date.today() + timedelta(days=30), settlement_status="OPEN"))
    db.add(m.ProjectMold(project_id=project.id, mold_id=mold.id))
    return customer


def effective_start(db, project, creator):
    subject = m.BusinessSubject(kind="internal_start", number="START-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(m.BusinessDecisionDetail(subject_id=subject.id, source_subject_id=None, decision="START", execution_mode="FULL_OUTSOURCE", effective_date=date.today(), evidence="开工通知给财务", amount=None, currency=None))
    return subject


def sales_contract(db, project, creator, customer):
    subject = m.BusinessSubject(kind="sales_contract", number="SC-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(m.ContractDetail(subject_id=subject.id, customer_id=customer.id, supplier_id=None, amount=Decimal("100000.00"), currency="CNY", contract_number="SC-SECRET-" + project.code, expected_date=date.today() + timedelta(days=60), replaces_id=None))
    stage = m.PaymentStage(contract_id=subject.id, name="DFM认证款", amount=Decimal("30000.00"), currency="CNY", condition="DFM认证通过后15天", condition_confirmed=False, condition_evidence=None)
    db.add(stage)
    db.flush()
    return subject, stage


def customer_receipt(db, project, contract, stage, creator):
    row = m.CustomerReceiptConfirmation(
        project_id=project.id,
        contract_subject_id=contract.id,
        stage_id=stage.id,
        amount=Decimal("12000.00"),
        currency="CNY",
        received_date=date.today(),
        reference="RCPT-SECRET-" + project.code,
        evidence="银行回单",
        confirmed_by=creator.id,
        source_system="MANUAL",
        source_ref="BANK-" + project.code,
        note="客户分次回款",
    )
    db.add(row)
    return row


def outsource_contract_and_payment(db, project, creator):
    supplier = m.Supplier(code="FIN-SUP", name="财务供应商", category="outsource")
    db.add(supplier)
    db.flush()
    contract = m.BusinessSubject(kind="full_outsource_contract", number="FOC-" + project.code, project_id=project.id, category="outsource", created_by=creator.id, status="EFFECTIVE")
    db.add(contract)
    db.flush()
    db.add(m.ContractDetail(subject_id=contract.id, customer_id=None, supplier_id=supplier.id, amount=Decimal("70000.00"), currency="CNY", contract_number="FOC-SECRET-" + project.code, expected_date=date.today() + timedelta(days=40), replaces_id=None))
    stage = m.PaymentStage(contract_id=contract.id, name="验收付款", amount=Decimal("40000.00"), currency="CNY", condition="供应商验收后付款", condition_confirmed=True, condition_evidence="财务核验条件")
    db.add(stage)
    db.flush()
    payment = m.BusinessSubject(kind="supplier_payment", number="PAY-" + project.code, project_id=project.id, category="outsource", created_by=creator.id, status="EFFECTIVE")
    db.add(payment)
    db.flush()
    db.add(m.PaymentRequestDetail(subject_id=payment.id, stage_id=stage.id, amount=Decimal("35000.00"), currency="CNY", reservation=Decimal("25000.00")))
    paid = m.PaymentConfirmation(request_id=payment.id, amount=Decimal("10000.00"), currency="CNY", paid_date=date.today(), reference="PAY-FIN-" + project.code, evidence="付款凭证", confirmed_by=creator.id, reversal_of_id=None)
    db.add(paid)
    db.flush()
    correction = m.BusinessSubject(kind="finance_correction", number="FC-" + project.code, project_id=project.id, category="outsource", created_by=creator.id, status="EFFECTIVE")
    db.add(correction)
    db.flush()
    reversal = m.PaymentConfirmation(request_id=payment.id, amount=Decimal("-2000.00"), currency="CNY", paid_date=date.today(), reference="REV-FIN-" + project.code, evidence="冲正凭证", confirmed_by=creator.id, reversal_of_id=paid.id)
    db.add(reversal)
    db.flush()
    db.add(m.FinanceCorrectionDetail(subject_id=correction.id, original_payment_id=paid.id, reason="付款金额更正", reversal_evidence="退款凭证", reversal_date=date.today(), reversal_id=reversal.id))
    return contract, payment


def contact_cost(db, project, creator):
    group = m.AssignmentGroup(kind="DEPARTMENT", name="工程部")
    db.add(group)
    db.flush()
    case = m.ContactCase(project_id=project.id, category="outsource", title="设变扣款和额外工时", description="供应商质量导致扣款", mode="ONLINE", created_by=creator.id, request_key="finance-" + project.code, request_hash="hash", problem_source="OUTSOURCE_DEFECT", current_stage="验收", change_type="EXCEPTION", urgency="URGENT")
    db.add(case)
    db.flush()
    db.add(m.ContactTask(case_id=case.id, department_id=group.id, title="核对扣款", created_by=creator.id, status="RESPONDED", affected_type="FINANCE", affected_ref="DEDUCT-001", impact_description="扣款与返工工时", planned_action="REWORK", estimated_amount=Decimal("3000.00"), currency="CNY", actual_hours=Decimal("2.00"), actual_amount=Decimal("2500.00"), actual_currency="CNY", execution_evidence="费用依据"))
    return case


def closure_finance(db, project, creator):
    case = m.ProjectClosureCase(project_id=project.id, mode="NORMAL", status="OPEN", current_stage="财务关闭", opened_by=creator.id)
    db.add(case)
    db.flush()
    db.add_all(
        [
            m.ProjectClosureItem(case_id=case.id, item_key="INVOICE", label="发票已核对", status="DONE", result="发票齐全", evidence="发票记录", source_system="MANUAL", updated_by=creator.id),
            m.ProjectClosureItem(case_id=case.id, item_key="CUSTOMER_RECEIPT", label="客户回款已核对", status="PENDING", result="等待客户回款台账", evidence=None, source_system="MANUAL", updated_by=creator.id),
            m.ProjectClosureItem(case_id=case.id, item_key="SUPPLIER_SETTLEMENT", label="供应商结算", status="DONE", result="供应商结算完成", evidence="结算单", source_system="MANUAL", updated_by=creator.id),
        ]
    )
    return case


def seed_finance_project(db, code="FIN-M001", with_customer_receipt=True):
    admin = user(db, "admin", True)
    p = project(db, code)
    customer = profile(db, p, admin)
    effective_start(db, p, admin)
    contract, stage = sales_contract(db, p, admin, customer)
    if with_customer_receipt:
        customer_receipt(db, p, contract, stage, admin)
    outsource_contract_and_payment(db, p, admin)
    contact_cost(db, p, admin)
    closure_finance(db, p, admin)
    return admin, p


def test_finance_context_schema_and_summary():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            seed_finance_project(db)
        schema = tool_schema("query_finance_context")["function"]["parameters"]
        assert {"project_id", "identifier"} <= set(schema["properties"])
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_finance_context", {"identifier": "FIN-M001"})
            assert result["resolution"] == "RESOLVED"
            analysis = result["data"][0]["analysis"]
            status = analysis["derived_status"]
            assert status["has_effective_start_notice"] is True
            assert status["has_sales_contract_payment_nodes"] is True
            assert status["has_customer_actual_receipt_ledger"] is True
            assert status["has_supplier_payment_request"] is True
            assert status["has_confirmed_supplier_payment"] is True
            assert status["has_open_supplier_payment_reservation"] is True
            assert status["has_finance_correction"] is True
            assert status["has_cost_or_deduction_signal"] is True
            assert analysis["customer_receipt_summary"]["confirmed_totals"] == [{"currency": "CNY", "amount": "12000.00"}]
            assert analysis["customer_receipt_summary"]["by_stage"][0]["stage_name"] == "DFM认证款"
            assert analysis["supplier_payment_summary"]["confirmed_totals"] == [{"currency": "CNY", "amount": "8000.00"}]
            assert "审批通过不等于已付款" in "".join(analysis["warnings"])
            assert "未返回：客户实际回款确认" not in "".join(result["limitations"])
            assert "不同事实" in "".join(result["limitations"])
    finally:
        engine.dispose()


def test_finance_context_keeps_customer_receivable_nodes_separate_without_receipts():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            seed_finance_project(db, "FIN-NO-RECEIPT", with_customer_receipt=False)
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_finance_context", {"identifier": "FIN-NO-RECEIPT"})
            analysis = result["data"][0]["analysis"]
            assert analysis["derived_status"]["has_sales_contract_payment_nodes"] is True
            assert analysis["derived_status"]["has_customer_actual_receipt_ledger"] is False
            assert analysis["customer_receipt_summary"]["confirmed_totals"] == []
            assert "未见客户实际回款确认" in "".join(analysis["warnings"])
    finally:
        engine.dispose()


def test_finance_context_does_not_leak_amounts_without_finance_permissions():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin, p = seed_finance_project(db, "FIN-LIMITED")
            operator = user(db)
            grant(db, admin, operator, "project.read", project_id=p.id)
            grant(db, admin, operator, "project.dossier.read", project_id=p.id)
            capability(db, operator, "query_finance_context")
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_finance_context", {"identifier": "FIN-LIMITED"})
            assert result["resolution"] == "RESOLVED"
            assert result["data"][0]["analysis"]["sales_contracts"] == []
            assert result["data"][0]["analysis"]["supplier_payment_summary"]["requests"] == []
            text = str(result)
            assert "SC-SECRET-FIN-LIMITED" not in text
            assert "FOC-SECRET-FIN-LIMITED" not in text
            assert "RCPT-SECRET-FIN-LIMITED" not in text
            assert "100000.00" not in text
            assert "12000.00" not in text
            assert "供应商付款申请" in "".join(result["limitations"])
            assert "客户实际回款确认" in "".join(result["limitations"])
    finally:
        engine.dispose()


def test_finance_context_reports_multiple_candidates_without_deciding():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "FIN-A", "共同财务项目A")
            project(db, "FIN-B", "共同财务项目B")
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_finance_context", {"identifier": "共同财务项目"})
            assert result["resolution"] == "MULTIPLE_CANDIDATES"
            assert {row["code"] for row in result["data"]} == {"FIN-A", "FIN-B"}
            assert "请使用项目 ID" in "".join(result["limitations"])
    finally:
        engine.dispose()


def test_prepare_customer_receipt_requires_confirmation_then_records_receipt():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin, p = seed_finance_project(db, "FIN-RCPT-PREPARE", with_customer_receipt=False)
            contract = db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.project_id == p.id, m.BusinessSubject.kind == "sales_contract"))
            stage = db.scalar(select(m.PaymentStage).where(m.PaymentStage.contract_id == contract.id))
            conversation = m.Conversation(user_id=admin.id, title="客户回款确认")
            db.add(conversation)
            db.flush()
            run = m.Run(conversation_id=conversation.id, user_id=admin.id, security_version=admin.security_version,
                prompt="准备登记客户实际回款", status="SUCCEEDED",
                checkpoint={"authorization_hash": fingerprint(db, admin), "agent_permission_mode": "ask"})
            db.add(run)
            db.flush()
            args = {
                "project_id": p.id,
                "project_version": p.row_version,
                "contract_subject_id": contract.id,
                "stage_id": stage.id,
                "amount": "12000.00",
                "currency": "CNY",
                "received_date": date.today().isoformat(),
                "reference": "RCPT-PREPARE-001",
                "evidence": "财务银行回单",
                "source_ref": "BANK-PREPARE-001",
                "note": "客户分次回款",
            }
        schema = tool_schema("prepare_customer_receipt_confirmation")["function"]["parameters"]
        assert {"project_id", "project_version", "contract_subject_id", "amount", "reference"} <= set(schema["properties"])
        with Session.begin() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            run = db.scalar(select(m.Run).where(m.Run.user_id == admin.id))
            evidence = execute(db, admin, "prepare_customer_receipt_confirmation", args, run=run)
            assert evidence["proposal"]["kind"] == "customer_receipt"
            assert evidence["proposal"]["requires_approval"] is False
            assert evidence["proposal"]["display"]["银行流水/凭证号"] == "RCPT-PREPARE-001"
            assert db.scalar(select(m.CustomerReceiptConfirmation).where(m.CustomerReceiptConfirmation.reference == "RCPT-PREPARE-001")) is None
            step = m.Step(run_id=run.id, sequence=0, tool="prepare_customer_receipt_confirmation", request_hash="hash", result=evidence)
            db.add(step)
            db.flush()
            payload = {"step_id": step.id, "proposal_hash": bpm.content_hash(evidence["proposal"])}
            intent = business.create_intent(db, admin, "finance.execute", step.id, payload)
            receipt = business.confirm_intent(db, admin, intent["id"], intent["challenge"])
            assert receipt["status"] == "CONFIRMED"
            row = db.get(m.CustomerReceiptConfirmation, receipt["customer_receipt_id"])
            assert row.reference == "RCPT-PREPARE-001"
            assert row.amount == Decimal("12000.00")
            assert row.stage_id == args["stage_id"]
    finally:
        engine.dispose()


def test_prepare_customer_receipt_rejects_duplicate_reference_and_stage_overflow():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin, p = seed_finance_project(db, "FIN-RCPT-BLOCK", with_customer_receipt=True)
            contract = db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.project_id == p.id, m.BusinessSubject.kind == "sales_contract"))
            stage = db.scalar(select(m.PaymentStage).where(m.PaymentStage.contract_id == contract.id))
            conversation = m.Conversation(user_id=admin.id, title="客户回款阻断")
            db.add(conversation)
            db.flush()
            run = m.Run(conversation_id=conversation.id, user_id=admin.id, security_version=admin.security_version,
                prompt="准备登记客户实际回款", status="SUCCEEDED",
                checkpoint={"authorization_hash": fingerprint(db, admin), "agent_permission_mode": "ask"})
            db.add(run)
            db.flush()
            base = {
                "project_id": p.id,
                "project_version": p.row_version,
                "contract_subject_id": contract.id,
                "stage_id": stage.id,
                "amount": "1000.00",
                "currency": "CNY",
                "received_date": date.today().isoformat(),
                "reference": "RCPT-SECRET-FIN-RCPT-BLOCK",
                "evidence": "重复银行回单",
            }
        with Session.begin() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            run = db.scalar(select(m.Run).where(m.Run.user_id == admin.id))
            with pytest.raises(Exception) as duplicate:
                execute(db, admin, "prepare_customer_receipt_confirmation", base, run=run)
            assert getattr(duplicate.value, "code", None) == "RECEIPT_DUPLICATE"
            overflow = {**base, "reference": "RCPT-OVERFLOW-001", "amount": "20000.01"}
            with pytest.raises(Exception) as over:
                execute(db, admin, "prepare_customer_receipt_confirmation", overflow, run=run)
            assert getattr(over.value, "code", None) == "RECEIPT_STAGE_OVERFLOW"
    finally:
        engine.dispose()

