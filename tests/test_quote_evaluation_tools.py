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


def project(db, code, name="报价评估项目", status="DRAFT"):
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


def quote_decision(db, project, created_by, number, decision, status="EFFECTIVE", mode="INTERNAL", amount="120000.00"):
    subject = m.BusinessSubject(kind="quote_acceptance", number=number, project_id=project.id, created_by=created_by.id, status=status)
    db.add(subject)
    db.flush()
    db.add(
        m.BusinessDecisionDetail(
            subject_id=subject.id,
            decision=decision,
            execution_mode=mode,
            effective_date=date.today(),
            evidence="成本核算、粗略工艺和项目工期综合评估依据",
            amount=Decimal(amount) if amount is not None else None,
            currency="CNY" if amount is not None else None,
        )
    )
    return subject


def sales_contract(db, project, created_by, number="SC-EVAL-001"):
    subject = m.BusinessSubject(kind="sales_contract", number="SUBJECT-" + number, project_id=project.id, created_by=created_by.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(
        m.ContractDetail(
            subject_id=subject.id,
            customer_id=None,
            supplier_id=None,
            amount=Decimal("130000.00"),
            currency="CNY",
            contract_number=number,
            expected_date=date.today(),
        )
    )
    return subject


def test_quote_evaluation_schema_and_gap_analysis():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "QUOTE-EVAL-001")
            quote_decision(db, p, admin, "QE-ACCEPT", "ACCEPT", mode="INTERNAL")
            sales_contract(db, p, admin, "VISIBLE-CONTRACT")
        schema = tool_schema("query_quote_evaluation_context")["function"]["parameters"]
        assert {"project_id", "identifier"} <= set(schema["properties"])
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_quote_evaluation_context", {"identifier": "QUOTE-EVAL-001"})
            assert result["resolution"] == "RESOLVED"
            row = result["data"][0]
            assert row["analysis"]["derived_status"]["has_price_basis"] is True
            assert row["analysis"]["derived_status"]["has_processing_mode"] is True
            assert row["analysis"]["derived_status"]["has_customer_feedback_or_downstream_fact"] is True
            assert row["analysis"]["derived_status"]["has_structured_cost_process_duration_breakdown"] is False
            assert "结构化拆分" in "".join(row["analysis"]["gaps"])
            assert row["sales_contracts"][0]["contract_number"] == "VISIBLE-CONTRACT"
    finally:
        engine.dispose()


def test_quote_evaluation_reports_multiple_candidates_without_deciding():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p1 = project(db, "QE-A", "共同报价评估项目A")
            p2 = project(db, "QE-B", "共同报价评估项目B")
            quote_decision(db, p1, admin, "QE-A-ACCEPT", "ACCEPT")
            quote_decision(db, p2, admin, "QE-B-REJECT", "REJECT", mode=None, amount=None)
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_quote_evaluation_context", {"identifier": "共同报价评估项目"})
            assert result["resolution"] == "MULTIPLE_CANDIDATES"
            assert {row["code"] for row in result["data"]} == {"QE-A", "QE-B"}
            assert "请使用项目 ID" in "".join(result["limitations"])
    finally:
        engine.dispose()


def test_quote_evaluation_does_not_leak_contract_without_contract_tool():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            operator = user(db)
            p = project(db, "QUOTE-EVAL-LIMITED")
            quote_decision(db, p, admin, "QE-LIMITED", "ACCEPT")
            sales_contract(db, p, admin, "HIDDEN-EVAL-CONTRACT")
            grant(db, admin, operator, "project.read", p.id)
            grant(db, admin, operator, "quote_acceptance.read", p.id)
            capability(db, operator, "query_quote_evaluation_context")
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_quote_evaluation_context", {"identifier": "QUOTE-EVAL-LIMITED"})
            row = result["data"][0]
            assert row["analysis"]["derived_status"]["has_price_basis"] is True
            assert row["sales_contracts"] == []
            assert "HIDDEN-EVAL-CONTRACT" not in str(result)
        with Session.begin() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            operator = db.query(m.User).filter_by(username="operator").one()
            p = db.query(m.Project).filter_by(code="QUOTE-EVAL-LIMITED").one()
            grant(db, admin, operator, "sales_contract.read", p.id)
            capability(db, operator, "query_sales_contract")
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_quote_evaluation_context", {"identifier": "QUOTE-EVAL-LIMITED"})
            assert result["data"][0]["sales_contracts"][0]["contract_number"] == "HIDDEN-EVAL-CONTRACT"
    finally:
        engine.dispose()

