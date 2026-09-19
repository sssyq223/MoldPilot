from datetime import date
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


def run_with_files(db, actor, project_code):
    conversation = m.Conversation(user_id=actor.id, title="中标接收资料")
    db.add(conversation)
    db.flush()
    run = m.Run(
        conversation_id=conversation.id,
        user_id=actor.id,
        security_version=actor.security_version,
        prompt="登记中标接收草稿",
        status="SUCCEEDED",
        checkpoint={"authorization_hash": fingerprint(db, actor), "agent_permission_mode": "ask"},
    )
    db.add(run)
    db.flush()
    files = []
    for index, (filename, media_type, digest) in enumerate((
        (project_code + "-bid.pdf", "application/pdf", "b" * 64),
        (project_code + "-mold.png", "image/png", "c" * 64),
        (project_code + "-start.pdf", "application/pdf", "d" * 64),
        (project_code + "-start-copy.pdf", "application/pdf", "d" * 64),
    )):
        blob = m.FileObject(
            owner_id=actor.id,
            conversation_id=conversation.id,
            request_key="bid-intake-" + str(index),
            filename=filename,
            media_type=media_type,
            size=512,
            sha256=digest,
            backend="local",
            storage_namespace="test",
            object_key="test/" + digest + "/" + filename,
            storage_version=None,
        )
        db.add(blob)
        db.flush()
        db.add(m.RunFile(run_id=run.id, file_id=blob.id))
        files.append(blob)
    return run, files


def confirm_bid_intake(db, actor, run, arguments, sequence):
    evidence = execute(db, actor, "prepare_bid_intake_draft", arguments, run=run)
    step = m.Step(
        run_id=run.id,
        sequence=sequence,
        tool="prepare_bid_intake_draft",
        request_hash="bid-intake-" + str(sequence),
        result=evidence,
    )
    db.add(step)
    db.flush()
    payload = {"step_id": step.id, "proposal_hash": bpm.content_hash(evidence["proposal"])}
    intent = business.create_intent(db, actor, "bid_intake.execute", step.id, payload)
    return evidence, business.confirm_intent(db, actor, intent["id"], intent["challenge"])


def bid_intake_args(project_row, files, *, version=1, previous_revision_id=None, source_ref="MAIL-BID-001"):
    return {
        "project_id": project_row.id,
        "project_version": project_row.row_version,
        "previous_revision_id": previous_revision_id,
        "version": version,
        "source_kind": "EMAIL",
        "source_ref": source_ref,
        "received_date": date.today().isoformat(),
        "customer_classification": "HISENSE",
        "classification_evidence": "业务人员已按项目客户档案人工确认海信规则",
        "customer_company": "海信测试客户",
        "customer_contact": "王经理",
        "customer_mold_number": "CUSTOMER-MOLD-001",
        "customer_model_or_material": "MODEL-001",
        "project_name_snapshot": project_row.name,
        "amount": "100000.00",
        "currency": "CNY",
        "our_recipient": "销售张三",
        "external_order_number": None,
        "external_start_date": None,
        "customer_due_date": None,
        "customer_process_confirmed": False,
        "customer_process_confirmation_evidence": None,
        "matched_quotation_subject_id": None,
        "historical_mold_number": None,
        "historical_relation_kind": None,
        "match_result": "UNMATCHED",
        "match_evidence": "未命中历史报价和历史模具，字段按要求保持为空",
        "notes": "中标和外部开工资料可分别到达",
        "attachments": [
            {"file_id": files[0].id, "role": "BID_NOTICE"},
            {"file_id": files[1].id, "role": "MOLD_IMAGE"},
        ],
    }


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


def test_bid_intake_draft_versions_continue_one_case_and_backfill_acceptance():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "BID-DRAFT-001")
            customer_profile(db, p, admin)
            acceptance = quote_decision(db, p, admin, "BID-DRAFT-ACCEPT", "ACCEPT")
            run, files = run_with_files(db, admin, p.code)
            ids = {"admin": admin.id, "project": p.id, "run": run.id,
                   "files": [item.id for item in files], "acceptance": acceptance.id}

        schema = tool_schema("prepare_bid_intake_draft")["function"]["parameters"]
        assert {"project_id", "project_version", "version", "attachments",
                "customer_classification", "match_result", "customer_process_confirmed"} <= set(schema["properties"])

        with Session.begin() as db:
            admin = db.get(m.User, ids["admin"])
            p = db.get(m.Project, ids["project"])
            run = db.get(m.Run, ids["run"])
            files = [db.get(m.FileObject, file_id) for file_id in ids["files"]]
            first_args = bid_intake_args(p, files)
            evidence = execute(db, admin, "prepare_bid_intake_draft", first_args, run=run)
            assert evidence["proposal"]["requires_approval"] is False
            assert db.scalar(select(m.BidIntakeCase)) is None
            _, first = confirm_bid_intake(db, admin, run, first_args, 0)
            assert first["status"] == "RECORDED"
            case = db.get(m.BidIntakeCase, first["case_id"])
            revision = db.get(m.BidIntakeRevision, first["revision_id"])
            assert case.project_id == p.id
            assert revision.version == 1
            assert len(list(db.scalars(select(m.BidIntakeAttachment).where(
                m.BidIntakeAttachment.revision_id == revision.id
            )))) == 2
            link = db.scalar(select(m.BidIntakeLifecycleLink).where(
                m.BidIntakeLifecycleLink.subject_id == ids["acceptance"]
            ))
            assert link.case_id == case.id
            assert link.link_kind == "ACCEPTANCE"
            ids.update({"case": case.id, "first_revision": revision.id})

        with Session.begin() as db:
            admin = db.get(m.User, ids["admin"])
            p = db.get(m.Project, ids["project"])
            run = db.get(m.Run, ids["run"])
            files = [db.get(m.FileObject, file_id) for file_id in ids["files"]]
            second_args = bid_intake_args(
                p, files, version=2, previous_revision_id=ids["first_revision"],
                source_ref="MAIL-START-001",
            )
            second_args.update({
                "external_order_number": "EXT-ORDER-001",
                "external_start_date": date.today().isoformat(),
                "customer_due_date": date.today().isoformat(),
                "customer_process_confirmed": True,
                "customer_process_confirmation_evidence": "客户工艺方案已由项目负责人和客户人工确认",
                "attachments": [{"file_id": files[2].id, "role": "EXTERNAL_START_NOTICE"}],
            })
            _, second = confirm_bid_intake(db, admin, run, second_args, 1)
            assert second["case_id"] == ids["case"]
            assert second["version"] == 2
            assert db.scalar(select(m.BidIntakeCase).where(
                m.BidIntakeCase.project_id == p.id
            )).id == ids["case"]

            result = execute(db, admin, "query_bid_intake_context", {"project_id": p.id})
            row = result["data"][0]
            assert row["bid_intake"]["id"] == ids["case"]
            assert row["bid_intake"]["current_revision"]["external_order_number"] == "EXT-ORDER-001"
            assert row["bid_intake"]["current_revision"]["customer_process_confirmed"] is True
            assert [item["version"] for item in row["bid_intake"]["revisions"]] == [2, 1]
            assert row["analysis"]["derived_status"]["bid_intake_lifecycle_state"] == "ACCEPTED_AWAITING_FORMAL_START"
            assert row["analysis"]["derived_status"]["has_source_document_record"] is True
            assert row["analysis"]["derived_status"]["has_mold_image_evidence"] is True

            duplicate_args = {**second_args,
                              "version": 3,
                              "previous_revision_id": second["revision_id"],
                              "attachments": [{"file_id": files[3].id, "role": "EXTERNAL_START_NOTICE"}]}
            with pytest.raises(Exception) as duplicate:
                execute(db, admin, "prepare_bid_intake_draft", duplicate_args, run=run)
            assert getattr(duplicate.value, "code", None) == "BID_INTAKE_DUPLICATE"
    finally:
        engine.dispose()

