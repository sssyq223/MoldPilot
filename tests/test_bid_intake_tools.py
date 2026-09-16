from datetime import date
from decimal import Decimal


from app import models as m
from app.authorization import PERMISSIONS
from pg_db import factory as pg_factory
from app.tool_gateway import execute, tool_schema


def factory():
    return pg_factory()


def user(db, username="operator", super_admin=False):
    row = m.User(username=username, display_name=username, password_hash="test", super_admin=super_admin)
    db.add(row)
    db.flush()
    return row


def project(db, code, name="中标接收项目", status="DRAFT"):
    row = m.Project(code=code, name=name, status=status)
    db.add(row)
    db.flush()
    return row


def grant(db, admin, target, permission, project_id, fields=None):
    db.add(
        m.Grant(
            user_id=target.id,
            permission=permission,
            effect="ALLOW",
            scope={"project_id": [project_id]},
            fields=fields or PERMISSIONS[permission],
            reason="unit test",
            granted_by=admin.id,
        )
    )


def capability(db, target, key, kind="TOOL"):
    db.add(m.Capability(user_id=target.id, kind=kind, key=key, enabled=True))


def customer_profile(db, project, owner, customer_code="HISENSE", rule_key="hisense"):
    customer = m.Customer(code=customer_code, name="海信客户", rule_key=rule_key)
    db.add(customer)
    db.flush()
    db.add(
        m.ProjectProfile(
            project_id=project.id,
            customer_id=customer.id,
            owner_user_id=owner.id,
            execution_mode="INTERNAL",
            customer_due_date=date.today(),
        )
    )
    return customer


def mold_link(db, project, internal_number="MOLD-HS-001"):
    mold = m.Mold(internal_number=internal_number, name="客户历史模具")
    db.add(mold)
    db.flush()
    db.add(m.ProjectMold(project_id=project.id, mold_id=mold.id))
    return mold


def quote_decision(db, project, created_by, number, decision, status="EFFECTIVE", mode="INTERNAL"):
    subject = m.BusinessSubject(kind="quote_acceptance", number=number, project_id=project.id, created_by=created_by.id, status=status)
    db.add(subject)
    db.flush()
    db.add(
        m.BusinessDecisionDetail(
            subject_id=subject.id,
            decision=decision,
            execution_mode=mode,
            effective_date=date.today(),
            evidence="中标资料人工核对依据",
            amount=Decimal("100000.00"),
            currency="CNY",
        )
    )
    return subject


def sales_contract(db, project, created_by, number="BID-SC-001"):
    subject = m.BusinessSubject(kind="sales_contract", number="SUBJECT-" + number, project_id=project.id, created_by=created_by.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(
        m.ContractDetail(
            subject_id=subject.id,
            customer_id=None,
            supplier_id=None,
            amount=Decimal("100000.00"),
            currency="CNY",
            contract_number=number,
            expected_date=date.today(),
        )
    )
    return subject


def test_bid_intake_schema_and_customer_contract_context():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "BID-M001")
            customer_profile(db, p, admin)
            mold_link(db, p)
            quote_decision(db, p, admin, "BID-ACCEPT", "ACCEPT")
            sales_contract(db, p, admin, "BID-CONTRACT-001")
        schema = tool_schema("query_bid_intake_context")["function"]["parameters"]
        assert {"project_id", "identifier"} <= set(schema["properties"])
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_bid_intake_context", {"identifier": "BID-M001"})
            assert result["resolution"] == "RESOLVED"
            row = result["data"][0]
            assert row["analysis"]["derived_status"]["has_customer_classification"] is True
            assert row["analysis"]["derived_status"]["has_effective_acceptance"] is True
            assert row["analysis"]["derived_status"]["has_sales_contract"] is True
            assert row["analysis"]["derived_status"]["has_mold_relation"] is True
            assert row["analysis"]["derived_status"]["has_source_document_record"] is False
            assert "客户邮件" in "".join(row["analysis"]["gaps"])
            assert row["sales_contracts"][0]["contract_number"] == "BID-CONTRACT-001"
    finally:
        engine.dispose()


def test_bid_intake_reports_multiple_candidates_without_deciding():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p1 = project(db, "BID-A", "共同中标项目A")
            p2 = project(db, "BID-B", "共同中标项目B")
            quote_decision(db, p1, admin, "BID-A-ACCEPT", "ACCEPT")
            quote_decision(db, p2, admin, "BID-B-REJECT", "REJECT", mode=None)
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_bid_intake_context", {"identifier": "共同中标项目"})
            assert result["resolution"] == "MULTIPLE_CANDIDATES"
            assert {row["code"] for row in result["data"]} == {"BID-A", "BID-B"}
            assert "请使用项目 ID" in "".join(result["limitations"])
    finally:
        engine.dispose()


def test_bid_intake_does_not_leak_contract_or_mold_without_tools_and_dossier_permission():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            operator = user(db)
            p = project(db, "BID-LIMITED")
            customer_profile(db, p, admin)
            mold_link(db, p, "SECRET-MOLD")
            quote_decision(db, p, admin, "BID-LIMITED-ACCEPT", "ACCEPT")
            sales_contract(db, p, admin, "SECRET-BID-CONTRACT")
            grant(db, admin, operator, "project.read", p.id)
            grant(db, admin, operator, "quote_acceptance.read", p.id)
            capability(db, operator, "query_bid_intake_context")
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_bid_intake_context", {"identifier": "BID-LIMITED"})
            row = result["data"][0]
            assert row["analysis"]["derived_status"]["has_effective_acceptance"] is True
            assert row["sales_contracts"] == []
            assert row["analysis"]["known_molds"] == []
            assert "SECRET-BID-CONTRACT" not in str(result)
            assert "SECRET-MOLD" not in str(result)
        with Session.begin() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            operator = db.query(m.User).filter_by(username="operator").one()
            p = db.query(m.Project).filter_by(code="BID-LIMITED").one()
            grant(db, admin, operator, "sales_contract.read", p.id)
            grant(db, admin, operator, "project.dossier.read", p.id)
            capability(db, operator, "query_sales_contract")
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_bid_intake_context", {"identifier": "BID-LIMITED"})
            row = result["data"][0]
            assert row["sales_contracts"][0]["contract_number"] == "SECRET-BID-CONTRACT"
            assert row["analysis"]["known_molds"][0]["internal_number"] == "SECRET-MOLD"
    finally:
        engine.dispose()

