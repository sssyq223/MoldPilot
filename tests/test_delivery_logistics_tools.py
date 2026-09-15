from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as m
from app.authorization import PERMISSIONS
from app.models import Base
from app.tool_gateway import execute, tool_schema


def factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    return engine, Session


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


def test_delivery_logistics_schema_and_context_summary():
    engine, Session = factory()
    try:
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
            assert "物流报价" in "".join(analysis["gaps"])
            assert "试模通过只代表试模结论" in "".join(analysis["warnings"])
    finally:
        engine.dispose()


def test_delivery_logistics_does_not_leak_order_without_order_tool():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            operator = user(db)
            p = project(db, "DLV-LIMITED")
            wh = warehouse(db)
            order_with_flow(db, p, admin, material(db, "SECRET-MAT", "秘密发货物料"), wh)
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
            assert result["data"][0]["purchase_orders"] == []
            assert "正式采购订单" in "".join(result["limitations"])
    finally:
        engine.dispose()


def test_delivery_logistics_reports_multiple_candidates_without_deciding():
    engine, Session = factory()
    try:
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
    finally:
        engine.dispose()
