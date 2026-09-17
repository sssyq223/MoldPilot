from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app import models as m
from app import bpm, business
from app.authorization import PERMISSIONS, fingerprint
from app.db import now
from app.errors import DomainError
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


def project(db, code, name="交付物流项目", status="ACTIVE"):
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


def material(db, code="SHIP-MAT", name="交付零件", category="hardware"):
    row = m.Material(code=code, name=name, category=category, unit="PCS")
    db.add(row)
    db.flush()
    return row


def supplier(db, category="hardware"):
    row = m.Supplier(code="SUP-" + category, name="物流测试供应商", category=category)
    db.add(row)
    db.flush()
    return row


def warehouse(db):
    row = m.Warehouse(code="WH-SHIP", name="成品仓", active=True, scope_confirmed=True, opening_evidence="期初确认")
    db.add(row)
    db.flush()
    return row


def plan(db, project, creator):
    today = date.today()
    subject = m.BusinessSubject(kind="project_plan", number="PLAN-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(m.PlanDetail(subject_id=subject.id, reason="交付物流计划"))
    task = m.PlanTask(
        plan_id=subject.id,
        key="delivery",
        name="出库发货与客户验收",
        owner_user_id=creator.id,
        planned_start=today,
        planned_end=today + timedelta(days=3),
        status="RUNNING",
        actual_start=today,
    )
    db.add(task)
    db.flush()
    return subject, task


def order_with_flow(db, project, creator, mat, wh):
    today = date.today()
    req = m.PurchaseRequest(number="PR-" + project.code, project_id=project.id, created_by=creator.id, status="APPROVED", remark="交付物流测试")
    db.add(req)
    db.flush()
    line = m.PurchaseLine(request_id=req.id, material_id=mat.id, quantity=Decimal("10"), due_date=today)
    db.add(line)
    db.flush()
    order = m.PurchaseOrder(request_id=req.id, project_id=project.id, supplier_id=supplier(db, mat.category).id, number="PO-" + project.code, status="ISSUED", currency="CNY", issued_by=creator.id, issued_at=None)
    db.add(order)
    db.flush()
    order_line = m.OrderLine(order_id=order.id, source_line_id=line.id, material_id=mat.id, quantity=Decimal("10"), unit_price=Decimal("5"), agreed_ship_date=today)
    db.add(order_line)
    db.flush()
    shipment = m.SupplierShipment(order_line_id=order_line.id, quantity=Decimal("10"), shipped_date=today, reference="SHIP-SECRET-" + project.code, evidence="供应商发货单", confirmed_by=creator.id)
    db.add(shipment)
    db.flush()
    receipt = m.GoodsReceipt(shipment_id=shipment.id, warehouse_id=wh.id, quantity=Decimal("10"), reference="GR-" + project.code, received_by=creator.id, evidence="仓库签收")
    db.add(receipt)
    db.flush()
    db.add(m.ReceiptInspection(receipt_id=receipt.id, accepted_quantity=Decimal("8"), rejected_quantity=Decimal("2"), inspector_id=creator.id, evidence="入库检验报告"))
    balance = m.StockBalance(warehouse_id=wh.id, material_id=mat.id, project_id=project.id, quantity=Decimal("8"))
    db.add(balance)
    db.flush()
    db.add(m.StockMovement(balance_id=balance.id, quantity=Decimal("-2"), kind="OUTBOUND", source_key="OUT-" + project.code, confirmed_by=creator.id, evidence="出库记录"))
    return order


def trial_result(db, project, creator, passed=True):
    assembly = m.BusinessSubject(kind="assembly_issue", number="ASM-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(assembly)
    db.flush()
    design = m.BusinessSubject(kind="design_route", number="DES-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(design)
    db.flush()
    db.add(m.DesignDetail(subject_id=design.id, design_type="NEW_MOLD", drawing_revision="A1", drawing_evidence="图纸", reviewer_id=creator.id))
    db.add(m.AssemblyDetail(subject_id=assembly.id, design_id=design.id, supervisor_id=creator.id, prerequisites_evidence="齐套", planned_date=date.today(), execution_status="DONE"))
    trial = m.BusinessSubject(kind="trial_request", number="TRIAL-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(trial)
    db.flush()
    db.add(m.TrialDetail(subject_id=trial.id, assembly_id=assembly.id, planned_date=date.today(), location="机台A", acceptance_criteria="试模报告", responsible_id=creator.id))
    db.add(m.TrialResult(trial_id=trial.id, passed=passed, actual_date=date.today(), evidence="试模报告", findings="合格" if passed else "不合格", change_id=None, confirmed_by=creator.id))
    return trial


def closure_acceptance(db, project, creator, status="DONE"):
    case = m.ProjectClosureCase(project_id=project.id, mode="NORMAL", status="OPEN", current_stage="客户验收", opened_by=creator.id)
    db.add(case)
    db.flush()
    db.add(
        m.ProjectClosureItem(
            case_id=case.id,
            item_key="CUSTOMER_ACCEPTANCE",
            label="客户验收完成或说明不适用",
            status=status,
            result="客户验收通过" if status == "DONE" else "",
            evidence="客户验收单" if status == "DONE" else "",
            source_system="MANUAL",
            updated_by=creator.id,
        )
    )
    return case


def contact_issue(db, project, creator):
    group = m.AssignmentGroup(kind="DEPARTMENT", name="品质部")
    db.add(group)
    db.flush()
    case = m.ContactCase(
        project_id=project.id,
        category="hardware",
        title="客户验收尺寸问题",
        description="客户验收发现尺寸问题",
        mode="ONLINE",
        created_by=creator.id,
        request_key="acceptance-" + project.code,
        request_hash="hash",
        problem_source="QUALITY_ISSUE",
        current_stage="客户验收",
        change_type="EXCEPTION",
        urgency="URGENT",
    )
    db.add(case)
    db.flush()
    db.add(
        m.ContactTask(
            case_id=case.id,
            department_id=group.id,
            title="验收问题整改",
            created_by=creator.id,
            status="ASSIGNED",
            affected_type="OTHER",
            affected_ref="CUSTOMER_ACCEPTANCE",
            impact_description="客户验收不通过，需要整改复验",
            planned_action="REWORK",
        )
    )
    return case


def logistics_quote(db, project, creator, settlement=True):
    today = date.today()
    route = m.LogisticsRoute(
        project_id=project.id,
        route_code="ROUTE-" + project.code,
        origin="昆山工厂",
        destination="客户工厂",
        carrier_name="顺达物流",
        vehicle_type="4.2米厢车",
        weight_kg=Decimal("2500.000"),
        transport_mode="TRUCK",
        price_unit="车次",
        tax_mode="TAX_INCLUDED",
        valid_from=today - timedelta(days=10),
        valid_to=today + timedelta(days=355),
        evidence="物流路线审批记录",
    )
    db.add(route)
    db.flush()
    quote = m.LogisticsQuote(
        route_id=route.id,
        supplier_id=supplier(db, "logistics").id,
        unit_price=Decimal("1800.00"),
        currency="CNY",
        valid_from=today - timedelta(days=1),
        valid_to=today + timedelta(days=180),
        status="EFFECTIVE",
        settlement_for_project_id=project.id if settlement else None,
        quote_evidence="物流报价审批单",
        approved_by=creator.id,
        pricing_method="NEGOTIATED",
        comparison_count=1,
        reconciliation_basis="按审批报价、实际发运单和承运商对账单核对",
    )
    db.add(quote)
    db.flush()
    return route, quote


def customer_signature(db, project, creator, reference="CUSTOMER-SIGN-001"):
    row = m.CustomerDeliverySignature(
        project_id=project.id,
        shipment_reference=reference,
        signed_date=date.today(),
        signer_name="客户代表",
        sign_status="SIGNED",
        move_type="MOLD_TRANSFER",
        evidence="客户签收单",
        recorded_by=creator.id,
    )
    db.add(row)
    db.flush()
    return row


def customer_acceptance(db, project, creator, signature, result="FAILED", recheck=False, deduction=True):
    row = m.CustomerAcceptanceRecord(
        project_id=project.id,
        signature_id=signature.id,
        acceptance_type="RECHECK" if recheck else "INITIAL",
        result=result,
        accepted_date=date.today(),
        issue_description="客户验收尺寸偏差" if result == "FAILED" else "复验通过",
        responsibility="SUPPLIER" if result == "FAILED" else "UNKNOWN",
        corrective_due_date=date.today() + timedelta(days=5) if result == "FAILED" else None,
        contact_case_id=None,
        supplier_id=None,
        deduction_amount=Decimal("500.00") if deduction else None,
        currency="CNY" if deduction else None,
        schedule_impact_days=2 if result == "FAILED" else 0,
        contract_change_required=result == "FAILED",
        evidence="客户验收记录",
        confirmed_by=creator.id,
    )
    db.add(row)
    db.flush()
    return row


def test_delivery_logistics_schema_and_context_summary(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        p = project(db, "DLV-M001")
        wh = warehouse(db)
        plan(db, p, admin)
        order_with_flow(db, p, admin, material(db), wh)
        trial_result(db, p, admin, passed=True)
        closure_acceptance(db, p, admin, status="DONE")
        contact_issue(db, p, admin)
    schema = tool_schema("query_delivery_logistics_context")["function"]["parameters"]
    assert {"project_id", "identifier"} <= set(schema["properties"])
    with Session() as db:
        admin = db.query(m.User).filter_by(username="admin").one()
        result = execute(db, admin, "query_delivery_logistics_context", {"identifier": "DLV-M001"})
        assert result["resolution"] == "RESOLVED"
        analysis = result["data"][0]["analysis"]
        status = analysis["derived_status"]
        assert status["has_delivery_plan_node"] is True
        assert status["has_supplier_shipment"] is True
        assert status["has_goods_receipt"] is True
        assert status["has_receipt_inspection"] is True
        assert status["has_rejected_receipt"] is True
        assert status["has_stock_out_movement"] is True
        assert status["has_trial_passed"] is True
        assert status["has_customer_acceptance"] is True
        assert status["has_structured_logistics_price"] is False
        assert "固定物流路线" in "".join(analysis["gaps"])
        assert "试模通过只代表试模结论" in "".join(analysis["warnings"])


def test_delivery_logistics_returns_effective_route_quote_and_settlement_price(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        p = project(db, "DLV-PRICE")
        wh = warehouse(db)
        plan(db, p, admin)
        order_with_flow(db, p, admin, material(db, "PRICE-MAT"), wh)
        closure_acceptance(db, p, admin, status="DONE")
        logistics_quote(db, p, admin, settlement=True)
    with Session() as db:
        admin = db.query(m.User).filter_by(username="admin").one()
        result = execute(db, admin, "query_delivery_logistics_context", {"identifier": "DLV-PRICE"})
        assert result["resolution"] == "RESOLVED"
        analysis = result["data"][0]["analysis"]
        status = analysis["derived_status"]
        pricing = analysis["logistics_pricing"]
        assert status["has_structured_logistics_price"] is True
        assert status["has_project_logistics_settlement_price"] is True
        assert pricing["derived_status"]["has_effective_quote"] is True
        assert pricing["derived_status"]["has_project_settlement_price"] is True
        assert pricing["effective_quotes"][0]["route"]["carrier_name"] == "顺达物流"
        assert pricing["effective_quotes"][0]["route"]["vehicle_type"] == "4.2米厢车"
        assert pricing["settlement_price_candidates"][0]["quote"]["unit_price"] == "1800.00"
        assert "物流报价" not in "".join(analysis["gaps"])


def test_delivery_logistics_keeps_customer_signature_separate_from_acceptance(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        p = project(db, "DLV-SIGN")
        wh = warehouse(db)
        plan(db, p, admin)
        order_with_flow(db, p, admin, material(db, "SIGN-MAT"), wh)
        customer_signature(db, p, admin)
    with Session() as db:
        admin = db.query(m.User).filter_by(username="admin").one()
        result = execute(db, admin, "query_delivery_logistics_context", {"identifier": "DLV-SIGN"})
        analysis = result["data"][0]["analysis"]
        status = analysis["derived_status"]
        customer = analysis["customer_delivery_acceptance"]
        assert status["has_customer_signature"] is True
        assert status["has_customer_acceptance"] is False
        assert customer["signatures"][0]["move_type"] == "MOLD_TRANSFER"
        assert "已有客户签收记录，但未见客户质量验收" in "".join(analysis["gaps"])


def test_delivery_logistics_reports_failed_customer_acceptance_deduction_and_recheck_gap(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        p = project(db, "DLV-ACCEPT-FAIL")
        wh = warehouse(db)
        plan(db, p, admin)
        order_with_flow(db, p, admin, material(db, "ACCEPT-MAT"), wh)
        signature = customer_signature(db, p, admin, "CUSTOMER-SIGN-FAIL")
        customer_acceptance(db, p, admin, signature, result="FAILED", deduction=True)
    with Session() as db:
        admin = db.query(m.User).filter_by(username="admin").one()
        result = execute(db, admin, "query_delivery_logistics_context", {"identifier": "DLV-ACCEPT-FAIL"})
        analysis = result["data"][0]["analysis"]
        status = analysis["derived_status"]
        customer = analysis["customer_delivery_acceptance"]
        assert status["has_customer_signature"] is True
        assert status["has_failed_customer_acceptance"] is True
        assert status["has_customer_recheck_passed"] is False
        assert status["has_customer_acceptance_deduction"] is True
        assert status["has_customer_acceptance_contract_change"] is True
        assert customer["acceptance_records"][0]["deduction_amount"] == "500.00"
        warnings = "".join(analysis["warnings"])
        assert "客户验收未通过" in warnings
        assert "费用扣款" in warnings
        assert "合同变化" in warnings


def test_delivery_logistics_does_not_leak_order_without_order_tool(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        operator = user(db)
        p = project(db, "DLV-LIMITED")
        wh = warehouse(db)
        order_with_flow(db, p, admin, material(db, "SECRET-MAT", "秘密发货物料"), wh)
        signature = customer_signature(db, p, admin, "CUSTOMER-SIGN-LIMITED")
        customer_acceptance(db, p, admin, signature, result="FAILED", deduction=True)
        grant(db, admin, operator, "project.read", project_id=p.id)
        grant(db, admin, operator, "warehouse.read", warehouse_id=wh.id)
        capability(db, operator, "query_delivery_logistics_context")
    with Session() as db:
        operator = db.query(m.User).filter_by(username="operator").one()
        result = execute(db, operator, "query_delivery_logistics_context", {"identifier": "DLV-LIMITED"})
        analysis = result["data"][0]["analysis"]
        assert analysis["derived_status"]["has_supplier_shipment"] is False
        assert "SHIP-SECRET" not in str(result)
        assert "PO-DLV-LIMITED" not in str(result)
        assert "500.00" not in str(result)
        assert "客户验收尺寸偏差" not in str(result)
        assert result["data"][0]["purchase_orders"] == []
        assert analysis["customer_delivery_acceptance"]["visibility"]["acceptance_records_visible"] is False
        assert analysis["customer_delivery_acceptance"]["signatures"][0]["shipment_reference"] == "CUSTOMER-SIGN-LIMITED"
        assert "正式采购订单" in "".join(result["limitations"])
        assert "客户验收/复验/扣款记录" in "".join(result["limitations"])


def test_delivery_logistics_reports_multiple_candidates_without_deciding(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        project(db, "DLV-A", "共同交付项目A")
        project(db, "DLV-B", "共同交付项目B")
    with Session() as db:
        admin = db.query(m.User).filter_by(username="admin").one()
        result = execute(db, admin, "query_delivery_logistics_context", {"identifier": "共同交付项目"})
        assert result["resolution"] == "MULTIPLE_CANDIDATES"
        assert {row["code"] for row in result["data"]} == {"DLV-A", "DLV-B"}
        assert "请使用项目 ID" in "".join(result["limitations"])


def test_prepare_project_logistics_route_requires_confirmation_then_records(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        p = project(db, "DLV-ROUTE-PREPARE")
        conversation = m.Conversation(user_id=admin.id, title="物流路线确认")
        db.add(conversation)
        db.flush()
        run = m.Run(
            conversation_id=conversation.id,
            user_id=admin.id,
            security_version=admin.security_version,
            prompt="登记模具项目实际物流路线",
            status="SUCCEEDED",
            checkpoint={"authorization_hash": fingerprint(db, admin), "agent_permission_mode": "ask"},
        )
        db.add(run)
        db.flush()
        args = {
            "route_scope": "PROJECT_ACTUAL",
            "project_id": p.id,
            "project_version": p.row_version,
            "route_code": "DLV-ROUTE-001",
            "origin": "昆山模具工厂",
            "destination": "上海客户工厂",
            "carrier_name": "顺达物流",
            "vehicle_type": "9.6米厢车",
            "weight_kg": "12800.000",
            "transport_mode": "TRUCK",
            "price_unit": "车次",
            "tax_mode": "TAX_INCLUDED",
            "valid_from": date.today().isoformat(),
            "valid_to": (date.today() + timedelta(days=90)).isoformat(),
            "evidence": "仓库核对车辆、装载重量和客户收货地点",
            "source_ref": "ROUTE-CONFIRM-001",
        }
    schema = tool_schema("prepare_logistics_route")["function"]["parameters"]
    assert {"route_scope", "weight_kg", "valid_from", "valid_to", "source_ref"} <= set(schema["properties"])
    with Session.begin() as db:
        admin = db.query(m.User).filter_by(username="admin").one()
        run = db.scalar(select(m.Run).where(m.Run.user_id == admin.id))
        evidence = execute(db, admin, "prepare_logistics_route", args, run=run)
        assert evidence["proposal"]["kind"] == "logistics_route"
        assert evidence["proposal"]["display"]["路线重量(kg)"] == "12800.000"
        assert db.scalar(select(m.LogisticsRoute).where(m.LogisticsRoute.route_code == "DLV-ROUTE-001")) is None
        step = m.Step(run_id=run.id, sequence=0, tool="prepare_logistics_route", request_hash="hash", result=evidence)
        db.add(step)
        db.flush()
        intent = business.create_intent(
            db,
            admin,
            "delivery_logistics.execute",
            step.id,
            {"step_id": step.id, "proposal_hash": bpm.content_hash(evidence["proposal"])},
        )
        receipt = business.confirm_intent(db, admin, intent["id"], intent["challenge"])
        row = db.get(m.LogisticsRoute, receipt["logistics_route_id"])
        assert receipt["status"] == "CONFIRMED"
        assert row.project_id == args["project_id"]
        assert row.weight_kg == Decimal("12800.000")
        assert row.valid_to == date.today() + timedelta(days=90)
        assert row.confirmed_by == admin.id
        assert row.source_ref == "ROUTE-CONFIRM-001"
        context = execute(db, admin, "query_delivery_logistics_context", {"identifier": "DLV-ROUTE-PREPARE"})
        pricing = context["data"][0]["analysis"]["logistics_pricing"]
        assert pricing["derived_status"]["has_route"] is True
        assert pricing["derived_status"]["has_effective_quote"] is False
        assert pricing["current_routes"][0]["route_code"] == "DLV-ROUTE-001"
        assert "已登记物流路线，但未见当前有效的物流报价" in "".join(context["data"][0]["analysis"]["gaps"])


def test_prepare_logistics_quote_requires_confirmation_and_preserves_comparison_evidence(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        p = project(db, "DLV-QUOTE-PREPARE")
        sup = supplier(db, "logistics-quote")
        route = m.LogisticsRoute(
            project_id=p.id,
            route_code="DLV-QUOTE-ROUTE",
            origin="昆山模具工厂",
            destination="上海客户工厂",
            carrier_name="顺达物流",
            vehicle_type="9.6米厢车",
            weight_kg=Decimal("12800.000"),
            transport_mode="TRUCK",
            price_unit="车次",
            tax_mode="TAX_INCLUDED",
            valid_from=date.today(),
            valid_to=date.today() + timedelta(days=365),
            evidence="仓库路线确认",
            source_ref="QUOTE-ROUTE-001",
            confirmed_by=admin.id,
        )
        db.add(route)
        db.flush()
        conversation = m.Conversation(user_id=admin.id, title="物流报价确认")
        db.add(conversation)
        db.flush()
        run = m.Run(
            conversation_id=conversation.id,
            user_id=admin.id,
            security_version=admin.security_version,
            prompt="确认项目本次物流结算价格",
            status="SUCCEEDED",
            checkpoint={"authorization_hash": fingerprint(db, admin), "agent_permission_mode": "ask"},
        )
        db.add(run)
        db.flush()
        args = {
            "route_id": route.id,
            "supplier_id": sup.id,
            "unit_price": "5680.00",
            "currency": "CNY",
            "valid_from": date.today().isoformat(),
            "valid_to": (date.today() + timedelta(days=180)).isoformat(),
            "settlement_for_project_id": p.id,
            "project_version": p.row_version,
            "pricing_method": "COMPETITIVE",
            "comparison_count": 3,
            "comparison_summary": "顺达5680、安达5920、捷运6100；综合时效与车型选择顺达",
            "quote_evidence": "三家盖章报价单及采购议价邮件",
            "reconciliation_basis": "按本次路线、实际发运单和顺达对账单核对，含税按车次结算",
            "source_ref": "LOGISTICS-QUOTE-001",
        }
    schema = tool_schema("prepare_logistics_quote")["function"]["parameters"]
    assert {"pricing_method", "comparison_count", "comparison_summary", "reconciliation_basis"} <= set(schema["properties"])
    with Session.begin() as db:
        admin = db.query(m.User).filter_by(username="admin").one()
        run = db.scalar(select(m.Run).where(m.Run.user_id == admin.id))
        evidence = execute(db, admin, "prepare_logistics_quote", args, run=run)
        assert evidence["proposal"]["display"]["比价数量"] == 3
        assert db.scalar(select(m.LogisticsQuote).where(m.LogisticsQuote.source_ref == "LOGISTICS-QUOTE-001")) is None
        step = m.Step(run_id=run.id, sequence=0, tool="prepare_logistics_quote", request_hash="hash", result=evidence)
        db.add(step)
        db.flush()
        intent = business.create_intent(
            db,
            admin,
            "delivery_logistics.execute",
            step.id,
            {"step_id": step.id, "proposal_hash": bpm.content_hash(evidence["proposal"])},
        )
        receipt = business.confirm_intent(db, admin, intent["id"], intent["challenge"])
        row = db.get(m.LogisticsQuote, receipt["logistics_quote_id"])
        assert row.status == "EFFECTIVE"
        assert row.pricing_method == "COMPETITIVE"
        assert row.comparison_count == 3
        assert "安达5920" in row.comparison_summary
        assert "实际发运单" in row.reconciliation_basis
        assert row.approved_by == admin.id


def test_logistics_quote_rejects_invalid_validity_and_incomplete_comparison(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        p = project(db, "DLV-QUOTE-BLOCK")
        route, _ = logistics_quote(db, p, admin, settlement=False)
        conversation = m.Conversation(user_id=admin.id, title="物流报价拦截")
        db.add(conversation)
        db.flush()
        run = m.Run(
            conversation_id=conversation.id,
            user_id=admin.id,
            security_version=admin.security_version,
            prompt="准备物流报价",
            status="SUCCEEDED",
            checkpoint={"authorization_hash": fingerprint(db, admin), "agent_permission_mode": "ask"},
        )
        db.add(run)
        db.flush()
        base = {
            "route_id": route.id,
            "supplier_id": None,
            "unit_price": "1000.00",
            "currency": "CNY",
            "valid_from": date.today().isoformat(),
            "valid_to": (date.today() + timedelta(days=184)).isoformat(),
            "settlement_for_project_id": p.id,
            "project_version": p.row_version,
            "pricing_method": "COMPETITIVE",
            "comparison_count": 1,
            "comparison_summary": None,
            "quote_evidence": "报价材料",
            "reconciliation_basis": "按实际运单核对",
            "source_ref": "LOGISTICS-QUOTE-BLOCK",
        }
        with pytest.raises(DomainError) as validity:
            execute(db, admin, "prepare_logistics_quote", base, run=run)
        assert validity.value.code == "LOGISTICS_QUOTE_VALIDITY_TOO_LONG"
        base["valid_to"] = (date.today() + timedelta(days=180)).isoformat()
        with pytest.raises(DomainError) as comparison:
            execute(db, admin, "prepare_logistics_quote", base, run=run)
        assert comparison.value.code == "LOGISTICS_COMPARISON_REQUIRED"


def test_delivery_logistics_query_exposes_governed_route_and_quote_metadata(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        p = project(db, "DLV-GOVERNED")
        route, quote = logistics_quote(db, p, admin, settlement=True)
        route.source_ref = "ROUTE-GOVERNED"
        route.confirmed_by = admin.id
        route.confirmed_at = now()
        quote.source_ref = "QUOTE-GOVERNED"
        quote.created_by = admin.id
        quote.comparison_summary = "两家报价比较后议价"
    with Session() as db:
        admin = db.query(m.User).filter_by(username="admin").one()
        result = execute(db, admin, "query_delivery_logistics_context", {"identifier": "DLV-GOVERNED"})
        item = result["data"][0]["analysis"]["logistics_pricing"]["effective_quotes"][0]
        assert item["route"]["weight_kg"] == "2500.000"
        assert item["route"]["source_ref"] == "ROUTE-GOVERNED"
        assert item["quote"]["pricing_method"] == "NEGOTIATED"
        assert item["quote"]["reconciliation_basis"].startswith("按审批报价")
        assert item["quote"]["source_ref"] == "QUOTE-GOVERNED"


def test_logistics_price_change_explicitly_supersedes_effective_quote_and_keeps_history(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        p = project(db, "DLV-QUOTE-REPLACE")
        route, old_quote = logistics_quote(db, p, admin, settlement=True)
        old_quote.source_ref = "QUOTE-OLD"
        conversation = m.Conversation(user_id=admin.id, title="物流价格变更")
        db.add(conversation)
        db.flush()
        run = m.Run(
            conversation_id=conversation.id,
            user_id=admin.id,
            security_version=admin.security_version,
            prompt="替换当前物流结算价格",
            status="SUCCEEDED",
            checkpoint={"authorization_hash": fingerprint(db, admin), "agent_permission_mode": "ask"},
        )
        db.add(run)
        db.flush()
        args = {
            "route_id": route.id,
            "supplier_id": old_quote.supplier_id,
            "unit_price": "1880.00",
            "currency": "CNY",
            "valid_from": date.today().isoformat(),
            "valid_to": (date.today() + timedelta(days=120)).isoformat(),
            "settlement_for_project_id": p.id,
            "project_version": p.row_version,
            "pricing_method": "NEGOTIATED",
            "comparison_count": 1,
            "comparison_summary": "与原承运商重新议价",
            "quote_evidence": "价格变化议价邮件",
            "reconciliation_basis": "新价格按实际发运单核对",
            "source_ref": "QUOTE-NEW",
            "supersedes_quote_id": old_quote.id,
        }
        evidence = execute(db, admin, "prepare_logistics_quote", args, run=run)
        step = m.Step(run_id=run.id, sequence=0, tool="prepare_logistics_quote", request_hash="hash", result=evidence)
        db.add(step)
        db.flush()
        intent = business.create_intent(
            db, admin, "delivery_logistics.execute", step.id,
            {"step_id": step.id, "proposal_hash": bpm.content_hash(evidence["proposal"])},
        )
        receipt = business.confirm_intent(db, admin, intent["id"], intent["challenge"])
        new_quote = db.get(m.LogisticsQuote, receipt["logistics_quote_id"])
        assert db.get(m.LogisticsQuote, old_quote.id).status == "CANCELLED"
        assert new_quote.status == "EFFECTIVE"
        assert new_quote.supersedes_quote_id == old_quote.id
        assert new_quote.unit_price == Decimal("1880.00")


def test_logistics_price_change_cannot_hide_an_overlapping_effective_quote(pg_session_factory):
    Session = pg_session_factory
    with Session.begin() as db:
        admin = user(db, "admin", True)
        p = project(db, "DLV-QUOTE-OVERLAP")
        route, _ = logistics_quote(db, p, admin, settlement=True)
        conversation = m.Conversation(user_id=admin.id, title="物流价格重叠")
        db.add(conversation)
        db.flush()
        run = m.Run(
            conversation_id=conversation.id,
            user_id=admin.id,
            security_version=admin.security_version,
            prompt="新增重叠物流结算价格",
            status="SUCCEEDED",
            checkpoint={"authorization_hash": fingerprint(db, admin), "agent_permission_mode": "ask"},
        )
        db.add(run)
        db.flush()
        args = {
            "route_id": route.id,
            "supplier_id": None,
            "unit_price": "1880.00",
            "currency": "CNY",
            "valid_from": date.today().isoformat(),
            "valid_to": (date.today() + timedelta(days=120)).isoformat(),
            "settlement_for_project_id": p.id,
            "project_version": p.row_version,
            "pricing_method": "NEGOTIATED",
            "comparison_count": 1,
            "comparison_summary": "重新议价",
            "quote_evidence": "议价邮件",
            "reconciliation_basis": "按实际发运单核对",
            "source_ref": "QUOTE-OVERLAP",
        }
        with pytest.raises(DomainError) as error:
            execute(db, admin, "prepare_logistics_quote", args, run=run)
        assert error.value.code == "LOGISTICS_QUOTE_EFFECTIVE_OVERLAP"
