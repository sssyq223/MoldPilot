from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app import models as m
from app.authorization import PERMISSIONS
from app.models import Base
from app.tool_gateway import execute, tool_schema


@pytest.fixture
def pg_session_factory(test_engine):
    tables = ",".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with test_engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))
    return sessionmaker(test_engine, expire_on_commit=False)


def user(db, username="operator", super_admin=False):
    row = m.User(username=username, display_name=username, password_hash="test", super_admin=super_admin)
    db.add(row)
    db.flush()
    return row


def project(db, code, name="整套委外项目", status="ACTIVE"):
    row = m.Project(code=code, name=name, status=status)
    db.add(row)
    db.flush()
    return row


def grant(db, admin, target, permission, project_id=None, category=None, warehouse_id=None, fields=None):
    scope = {}
    if project_id:
        scope["project_id"] = [project_id]
    if category:
        scope["category"] = [category]
    if warehouse_id:
        scope["warehouse_id"] = [warehouse_id]
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


def supplier(db, code="OUT-SUP"):
    row = m.Supplier(code=code, name="整套委外供应商", category="outsource")
    db.add(row)
    db.flush()
    return row


def material(db, code="OUT-MAT"):
    row = m.Material(code=code, name="整套委外工序包", category="outsource", unit="SET")
    db.add(row)
    db.flush()
    return row


def warehouse(db):
    row = m.Warehouse(code="WH-OUT", name="委外交付仓", active=True, scope_confirmed=True, opening_evidence="期初确认")
    db.add(row)
    db.flush()
    return row


def full_outsource_profile(db, project, owner):
    db.add(
        m.ProjectProfile(
            project_id=project.id,
            owner_user_id=owner.id,
            execution_mode="FULL_OUTSOURCE",
            customer_due_date=date.today() + timedelta(days=30),
            settlement_status="OPEN",
        )
    )


def acceptance(db, project, creator):
    subject = m.BusinessSubject(kind="quote_acceptance", number="QA-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(
        m.BusinessDecisionDetail(
            subject_id=subject.id,
            source_subject_id=None,
            decision="ACCEPT",
            execution_mode="FULL_OUTSOURCE",
            effective_date=date.today(),
            evidence="客户确认整套委外",
            amount=Decimal("120000.00"),
            currency="CNY",
        )
    )
    return subject


def outsource_contract(db, project, creator, sup):
    subject = m.BusinessSubject(
        kind="full_outsource_contract",
        number="FOC-" + project.code,
        project_id=project.id,
        category="outsource",
        created_by=creator.id,
        status="EFFECTIVE",
    )
    db.add(subject)
    db.flush()
    db.add(
        m.ContractDetail(
            subject_id=subject.id,
            customer_id=None,
            supplier_id=sup.id,
            amount=Decimal("100000.00"),
            currency="CNY",
            contract_number="FOC-SECRET-" + project.code,
            expected_date=date.today() + timedelta(days=20),
            replaces_id=None,
        )
    )
    stage = m.PaymentStage(contract_id=subject.id, name="验收付款", amount=Decimal("50000.00"), currency="CNY", condition="客户验收后付款", condition_confirmed=True, condition_evidence="条件确认")
    db.add(stage)
    db.flush()
    payment = m.BusinessSubject(kind="supplier_payment", number="PAY-" + project.code, project_id=project.id, category="outsource", created_by=creator.id, status="EFFECTIVE")
    db.add(payment)
    db.flush()
    db.add(m.PaymentRequestDetail(subject_id=payment.id, stage_id=stage.id, amount=Decimal("30000.00"), currency="CNY", reservation=Decimal("30000.00")))
    db.add(m.PaymentConfirmation(request_id=payment.id, amount=Decimal("10000.00"), currency="CNY", paid_date=date.today(), reference="PAYREF-" + project.code, evidence="付款回单", confirmed_by=creator.id, reversal_of_id=None))
    return subject


def plan(db, project, creator):
    today = date.today()
    subject = m.BusinessSubject(kind="project_plan", number="PLAN-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(m.PlanDetail(subject_id=subject.id, reason="整套委外协同计划"))
    task = m.PlanTask(
        plan_id=subject.id,
        key="outsource_acceptance",
        name="供应商生产质检装配试模验收节点",
        owner_user_id=creator.id,
        planned_start=today,
        planned_end=today + timedelta(days=12),
        actual_start=today,
        status="RUNNING",
    )
    db.add(task)
    db.flush()
    return subject, task


def order_flow(db, project, creator, mat, sup, wh):
    today = date.today()
    request = m.PurchaseRequest(number="PR-" + project.code, project_id=project.id, created_by=creator.id, status="APPROVED", remark="整套委外执行")
    db.add(request)
    db.flush()
    request_line = m.PurchaseLine(request_id=request.id, material_id=mat.id, quantity=Decimal("1"), due_date=today)
    db.add(request_line)
    db.flush()
    order = m.PurchaseOrder(request_id=request.id, project_id=project.id, supplier_id=sup.id, number="PO-OUT-" + project.code, status="ISSUED", currency="CNY", issued_by=creator.id, issued_at=None)
    db.add(order)
    db.flush()
    line = m.OrderLine(order_id=order.id, source_line_id=request_line.id, material_id=mat.id, quantity=Decimal("1"), unit_price=Decimal("100000.00"), agreed_ship_date=today)
    db.add(line)
    db.flush()
    shipment = m.SupplierShipment(order_line_id=line.id, quantity=Decimal("1"), shipped_date=today, reference="SHIP-OUT-SECRET-" + project.code, evidence="供应商发货", confirmed_by=creator.id)
    db.add(shipment)
    db.flush()
    receipt = m.GoodsReceipt(shipment_id=shipment.id, warehouse_id=wh.id, quantity=Decimal("1"), reference="GR-OUT-" + project.code, received_by=creator.id, evidence="我方收货")
    db.add(receipt)
    db.flush()
    db.add(m.ReceiptInspection(receipt_id=receipt.id, accepted_quantity=Decimal("0"), rejected_quantity=Decimal("1"), inspector_id=creator.id, evidence="验收不合格"))
    return order


def supplier_progress(db, project, creator, sup, task=None, status="AT_RISK"):
    row = m.SupplierProgressReport(
        project_id=project.id,
        supplier_id=sup.id,
        contract_subject_id=None,
        plan_task_id=task.id if task else None,
        stage_key="supplier_trial",
        stage_name="供应商试模与整改",
        report_date=date.today(),
        status=status,
        progress_percent=70,
        next_due_date=date.today() - timedelta(days=1),
        issue_summary="试模整改延期，等待复验",
        evidence="供应商进度上报",
        source_system="MANUAL",
        source_ref="SPR-" + project.code,
        reported_by=creator.id,
        followed_by=creator.id,
    )
    db.add(row)
    db.flush()
    return row


def material_handoff(db, project, creator, sup, contract=None, status="APPROVED"):
    row = m.SupplierMaterialHandoff(
        project_id=project.id,
        supplier_id=sup.id,
        contract_subject_id=contract.id if contract else None,
        file_id=None,
        document_title="客户原始资料包",
        document_type="CUSTOMER_MATERIAL",
        approval_status=status,
        provided_date=date.today(),
        provided_to="供应商项目经理",
        handoff_channel="EMAIL",
        evidence="邮件交接回执",
        source_system="MANUAL",
        source_ref="HANDOFF-" + project.code,
        provided_by=creator.id,
        verified_by=creator.id if status == "APPROVED" else None,
    )
    db.add(row)
    db.flush()
    return row


def contract_signing(db, contract, creator, status="SIGNED"):
    row = m.ContractSigningRecord(
        contract_subject_id=contract.id,
        template_name="整套委外合同模板",
        signing_method="OFFLINE_FILE",
        status=status,
        signed_date=date.today() if status == "SIGNED" else None,
        signed_file_id=None,
        signed_file_title="整套委外合同签署扫描件.pdf" if status == "SIGNED" else "",
        supplier_signer="供应商负责人" if status == "SIGNED" else "",
        buyer_reviewer_id=creator.id,
        approved_by=creator.id if status == "SIGNED" else None,
        evidence="人工签署文件上传记录",
        source_system="MANUAL",
        source_ref="SIGN-" + contract.number,
        recorded_by=creator.id,
    )
    db.add(row)
    db.flush()
    return row


def contact_issue(db, project, creator):
    group = m.AssignmentGroup(kind="DEPARTMENT", name="采购部")
    db.add(group)
    db.flush()
    case = m.ContactCase(
        project_id=project.id,
        category="outsource",
        title="委外供应商延期与质量扣款",
        description="供应商延期且验收不合格",
        mode="ONLINE",
        created_by=creator.id,
        request_key="outsource-" + project.code,
        request_hash="hash",
        problem_source="OUTSOURCE_DEFECT",
        current_stage="委外客户验收",
        change_type="EXCEPTION",
        urgency="URGENT",
    )
    db.add(case)
    db.flush()
    task = m.ContactTask(
        case_id=case.id,
        department_id=group.id,
        title="供应商整改复验及扣款核对",
        created_by=creator.id,
        status="ASSIGNED",
        affected_type="CONTRACT",
        affected_ref="FOC",
        impact_description="质量延期，需要整改复验并核对扣款",
        planned_action="REWORK",
        delivery_impact_days=5,
        estimated_amount=Decimal("8000.00"),
        currency="CNY",
    )
    db.add(task)
    db.flush()
    return case, task


def supplier_deduction(db, project, creator, sup, contract=None, case=None, task=None, status="SETTLED"):
    row = m.SupplierDeductionSettlement(
        project_id=project.id,
        supplier_id=sup.id,
        contract_subject_id=contract.id if contract else None,
        contact_case_id=case.id if case else None,
        contact_task_id=task.id if task else None,
        reason="供应商质量延期责任扣款",
        responsibility="SUPPLIER",
        deduction_amount=Decimal("8000.00"),
        currency="CNY",
        status=status,
        settlement_reference="SETTLE-" + project.code if status == "SETTLED" else None,
        responsibility_evidence="责任确认单",
        settlement_evidence="供应商结算扣款单" if status == "SETTLED" else "",
        confirmed_by=creator.id,
        settled_by=creator.id if status == "SETTLED" else None,
        settled_at=None,
        source_system="MANUAL",
        source_ref="DEDUCT-" + project.code,
    )
    db.add(row)
    db.flush()
    return row


def change_negotiation(db, project, creator, sup, contract=None, case=None, task=None, status="APPROVED"):
    row = m.OutsourceChangeNegotiation(
        project_id=project.id,
        supplier_id=sup.id,
        contract_subject_id=contract.id if contract else None,
        contact_case_id=case.id if case else None,
        contact_task_id=task.id if task else None,
        customer_quote_amount=Decimal("12000.00"),
        supplier_quote_amount=Decimal("10000.00"),
        negotiated_amount=Decimal("9000.00"),
        currency="CNY",
        schedule_impact_days=3,
        task_impact_summary="供应商整改复验节点顺延3天，追加加工费用9000",
        requires_contract_change=True,
        status=status,
        customer_evidence="客户报价确认邮件",
        supplier_evidence="供应商议价回复",
        negotiation_evidence="采购与供应商议价记录",
        approved_by=creator.id if status == "APPROVED" else None,
        source_system="MANUAL",
        source_ref="NEG-" + project.code,
    )
    db.add(row)
    db.flush()
    return row


def closure_acceptance(db, project, creator):
    case = m.ProjectClosureCase(project_id=project.id, mode="NORMAL", status="OPEN", current_stage="委外交付验收", opened_by=creator.id)
    db.add(case)
    db.flush()
    db.add(
        m.ProjectClosureItem(
            case_id=case.id,
            item_key="CUSTOMER_ACCEPTANCE",
            label="客户验收完成",
            status="DONE",
            result="客户验收通过",
            evidence="客户验收单",
            source_system="MANUAL",
            updated_by=creator.id,
        )
    )
    return case


def test_full_outsource_schema_and_context_summary(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        p = project(db, "OUT-M001")
        full_outsource_profile(db, p, admin)
        sup = supplier(db)
        wh = warehouse(db)
        acceptance(db, p, admin)
        contract = outsource_contract(db, p, admin, sup)
        contract_signing(db, contract, admin)
        material_handoff(db, p, admin, sup, contract)
        _, task = plan(db, p, admin)
        supplier_progress(db, p, admin, sup, task)
        order_flow(db, p, admin, material(db), sup, wh)
        case, contact_task = contact_issue(db, p, admin)
        supplier_deduction(db, p, admin, sup, contract, case, contact_task)
        change_negotiation(db, p, admin, sup, contract, case, contact_task)
        closure_acceptance(db, p, admin)
    schema = tool_schema("query_full_outsource_context")["function"]["parameters"]
    assert {"project_id", "identifier"} <= set(schema["properties"])
    with Session() as db:
        admin = db.query(m.User).filter_by(username="admin").one()
        result = execute(db, admin, "query_full_outsource_context", {"identifier": "OUT-M001"})
        assert result["resolution"] == "RESOLVED"
        analysis = result["data"][0]["analysis"]
        status = analysis["derived_status"]
        assert status["has_full_outsource_mode"] is True
        assert status["has_effective_full_outsource_contract"] is True
        assert status["has_signed_full_outsource_contract_file"] is True
        assert status["has_unsigned_contract_signing_record"] is False
        assert status["has_outsource_plan_node"] is True
        assert status["has_supplier_progress_report"] is True
        assert status["has_supplier_progress_risk"] is True
        assert status["has_overdue_supplier_progress_followup"] is True
        assert status["has_approved_supplier_material_handoff"] is True
        assert status["has_draft_or_revoked_supplier_material_handoff"] is False
        assert status["has_confirmed_supplier_deduction"] is True
        assert status["has_settled_supplier_deduction"] is True
        assert status["has_pending_supplier_deduction"] is False
        assert status["has_approved_outsource_change_negotiation"] is True
        assert status["has_open_outsource_change_negotiation"] is False
        assert status["has_contract_change_negotiation"] is True
        assert status["has_supplier_shipment_or_receipt"] is True
        assert status["has_rejected_receipt"] is True
        assert status["has_open_outsource_issue"] is True
        assert status["has_deduction_or_cost_impact_signal"] is True
        assert status["has_supplier_payment_request"] is True
        assert status["has_customer_acceptance_or_close_evidence"] is True
        assert analysis["supplier_progress_reports"][0]["stage_name"] == "供应商试模与整改"
        assert analysis["supplier_progress_reports"][0]["overdue_followup"] is True
        assert analysis["supplier_material_handoffs"][0]["document_title"] == "客户原始资料包"
        assert analysis["supplier_material_handoffs"][0]["approval_status"] == "APPROVED"
        assert analysis["contract_signing_records"][0]["signed_file_title"] == "整套委外合同签署扫描件.pdf"
        assert analysis["contract_signing_records"][0]["status"] == "SIGNED"
        assert analysis["supplier_deduction_settlements"][0]["deduction_amount"] == "8000.00"
        assert analysis["supplier_deduction_settlements"][0]["status"] == "SETTLED"
        assert analysis["outsource_change_negotiations"][0]["negotiated_amount"] == "9000.00"
        assert analysis["outsource_change_negotiations"][0]["schedule_impact_days"] == 3
        assert analysis["outsource_change_negotiations"][0]["requires_contract_change"] is True
        assert "供应商门户" in "".join(analysis["gaps"])
        warnings = "".join(analysis["warnings"])
        assert "不合格" in warnings
        assert "供应商节点风险" in warnings
        assert "超过下次跟进日期" in warnings


def test_full_outsource_does_not_leak_orders_without_order_tool(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        operator = user(db)
        p = project(db, "OUT-LIMITED")
        full_outsource_profile(db, p, admin)
        sup = supplier(db)
        wh = warehouse(db)
        contract = outsource_contract(db, p, admin, sup)
        contract_signing(db, contract, admin)
        supplier_progress(db, p, admin, sup)
        material_handoff(db, p, admin, sup, contract)
        supplier_deduction(db, p, admin, sup, contract)
        change_negotiation(db, p, admin, sup, contract)
        order_flow(db, p, admin, material(db, "SECRET-OUT-MAT"), sup, wh)
        grant(db, admin, operator, "project.read", project_id=p.id)
        grant(db, admin, operator, "full_outsource_contract.read", project_id=p.id, category="outsource")
        capability(db, operator, "query_full_outsource_context")
    with Session() as db:
        operator = db.query(m.User).filter_by(username="operator").one()
        result = execute(db, operator, "query_full_outsource_context", {"identifier": "OUT-LIMITED"})
        analysis = result["data"][0]["analysis"]
        assert analysis["derived_status"]["has_effective_full_outsource_contract"] is True
        assert analysis["derived_status"]["has_signed_full_outsource_contract_file"] is True
        assert analysis["derived_status"]["has_supplier_progress_report"] is True
        assert analysis["derived_status"]["has_approved_supplier_material_handoff"] is True
        assert analysis["derived_status"]["has_settled_supplier_deduction"] is True
        assert analysis["derived_status"]["has_approved_outsource_change_negotiation"] is True
        assert analysis["derived_status"]["has_supplier_shipment_or_receipt"] is False
        assert "SHIP-OUT-SECRET" not in str(result)
        assert "PO-OUT-OUT-LIMITED" not in str(result)
        assert result["data"][0]["analysis"]["supplier_execution_tracking"]["orders"] == []
        assert analysis["contract_signing_records"][0]["source_ref"] == "SIGN-FOC-OUT-LIMITED"
        assert analysis["supplier_progress_reports"][0]["source_ref"] == "SPR-OUT-LIMITED"
        assert analysis["supplier_material_handoffs"][0]["source_ref"] == "HANDOFF-OUT-LIMITED"
        assert analysis["supplier_deduction_settlements"][0]["source_ref"] == "DEDUCT-OUT-LIMITED"
        assert analysis["outsource_change_negotiations"][0]["source_ref"] == "NEG-OUT-LIMITED"
        assert "正式订单" in "".join(result["limitations"])


def test_full_outsource_reports_multiple_candidates_without_deciding(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        project(db, "OUT-A", "共同委外项目A")
        project(db, "OUT-B", "共同委外项目B")
    with Session() as db:
        admin = db.query(m.User).filter_by(username="admin").one()
        result = execute(db, admin, "query_full_outsource_context", {"identifier": "共同委外项目"})
        assert result["resolution"] == "MULTIPLE_CANDIDATES"
        assert {row["code"] for row in result["data"]} == {"OUT-A", "OUT-B"}
        assert "请使用项目 ID" in "".join(result["limitations"])
