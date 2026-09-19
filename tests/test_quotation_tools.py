from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import bpm, business, models as m
from app.authorization import fingerprint
from app.tool_gateway import execute, tool_schema
from pg_db import factory as pg_factory


def factory():
    return pg_factory()


def user(db, username="admin", super_admin=True):
    row = m.User(
        username=username, display_name=username, password_hash="test", super_admin=super_admin,
    )
    db.add(row)
    db.flush()
    return row


def project(db, code="QUOTE-VERSION-001"):
    row = m.Project(code=code, name="客户报价版本项目", status="DRAFT")
    db.add(row)
    db.flush()
    return row


def workflow(db, actor, kind):
    config = {
        "business_type": kind,
        "nodes": [{
            "key": "review", "name": "报价审批", "mode": "ALL",
            "users": [actor.id], "reject_rules": [],
        }],
    }
    row = m.WorkflowDefinition(
        process_key=kind + "_test", version=1, name=kind + "审批",
        status="PUBLISHED", config=config, bpmn_xml=bpm.compile_bpmn(config),
        package_hash="test",
    )
    db.add(row)
    db.flush()
    return row


def run_with_file(db, actor, project_code, *, digest="a" * 64):
    conversation = m.Conversation(user_id=actor.id, title="客户报价版本")
    db.add(conversation)
    db.flush()
    run = m.Run(
        conversation_id=conversation.id, user_id=actor.id,
        security_version=actor.security_version, prompt="准备客户报价版本",
        status="SUCCEEDED", checkpoint={
            "authorization_hash": fingerprint(db, actor), "agent_permission_mode": "ask",
        },
    )
    db.add(run)
    db.flush()
    blob = m.FileObject(
        owner_id=actor.id, conversation_id=conversation.id,
        request_key="quote-" + digest[:24],
        filename=project_code + "-drawing.pdf", media_type="application/pdf",
        size=256, sha256=digest, backend="local", storage_namespace="test",
        object_key="test/" + digest + "/drawing.pdf", storage_version=None,
    )
    db.add(blob)
    db.flush()
    db.add(m.RunFile(run_id=run.id, file_id=blob.id))
    return run, blob


def approve_instance(db, actor, instance_id):
    instance = db.get(m.ApprovalInstance, instance_id)
    seat = db.scalar(select(m.ApprovalSeat).where(
        m.ApprovalSeat.instance_id == instance_id,
        m.ApprovalSeat.user_id == actor.id,
        m.ApprovalSeat.status == "PENDING",
    ))
    payload = {
        "instance_id": instance.id, "seat_id": seat.id,
        "seat_version": seat.version, "version": instance.version,
        "snapshot_hash": instance.snapshot_hash, "decision": "APPROVE",
        "comment": "报价资料、成本、工艺、工期、价格和交期已人工核对",
    }
    intent = business.create_intent(db, actor, "approval.decide", instance.id, payload)
    return business.confirm_intent(db, actor, intent["id"], intent["challenge"])


def quotation_args(project_row, definition, file_id, *, version=1, previous_id=None):
    return {
        "project_id": project_row.id,
        "project_version": project_row.row_version,
        "previous_id": previous_id,
        "quotation_number": "Q-" + project_row.code,
        "version": version,
        "preliminary_execution_mode": "INTERNAL",
        "quoted_amount": "120000.00" if version == 1 else "125000.00",
        "currency": "CNY",
        "promised_delivery_date": (date.today() + timedelta(days=90)).isoformat(),
        "payment_terms": "合同生效30%，T0后40%，终验后30%",
        "cost_amount": "85000.00",
        "cost_evidence": "成本人员核算表 COST-001",
        "process_analysis": "技术人员确认采用常规结构设计、CNC加工和内部装配试模路线。",
        "duration_days": 75,
        "duration_evidence": "项目人员工期评估表 SCHEDULE-001",
        "supplier_quote_amount": None,
        "supplier_delivery_date": None,
        "supplier_requirements": None,
        "supplier_quote_evidence": None,
        "customer_company_snapshot": "测试客户有限公司",
        "customer_contact_snapshot": "王经理",
        "owner_user_id": project_row.id,  # replaced with the actor id by each test
        "source_summary": {"customer_order": "PO-QUOTE-001", "mold_count": 1},
        "source_kind": "EMAIL",
        "source_ref": "MAIL-QUOTE-001",
        "file_ids": [file_id],
        "workflow_definition_id": definition.id,
    }


def prepare_and_confirm(db, actor, run, args, sequence=0):
    evidence = execute(db, actor, "prepare_quotation_version", args, run=run)
    step = m.Step(
        run_id=run.id, sequence=sequence, tool="prepare_quotation_version",
        request_hash="quote-hash-" + str(sequence), result=evidence,
    )
    db.add(step)
    db.flush()
    payload = {"step_id": step.id, "proposal_hash": bpm.content_hash(evidence["proposal"])}
    intent = business.create_intent(db, actor, "quotation.execute", step.id, payload)
    receipt = business.confirm_intent(db, actor, intent["id"], intent["challenge"])
    return evidence, receipt


def test_quotation_version_feedback_and_acceptance_chain_is_versioned_and_auditable():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db)
            project_row = project(db)
            quote_definition = workflow(db, admin, "quotation")
            acceptance_definition = workflow(db, admin, "quote_acceptance")
            run, blob = run_with_file(db, admin, project_row.code)
            args = quotation_args(project_row, quote_definition, blob.id)
            args["owner_user_id"] = admin.id
            ids = {
                "project": project_row.id, "quote_definition": quote_definition.id,
                "acceptance_definition": acceptance_definition.id,
                "run": run.id, "blob": blob.id,
            }

        schema = tool_schema("prepare_quotation_version")["function"]["parameters"]
        assert {"project_id", "project_version", "quotation_number", "file_ids",
                "workflow_definition_id"} <= set(schema["properties"])

        with Session.begin() as db:
            admin = db.get(m.User, db.scalar(select(m.User.id)))
            project_row = db.get(m.Project, ids["project"])
            definition = db.get(m.WorkflowDefinition, ids["quote_definition"])
            run = db.get(m.Run, ids["run"])
            args = quotation_args(project_row, definition, ids["blob"])
            args["owner_user_id"] = admin.id
            evidence = execute(db, admin, "prepare_quotation_version", args, run=run)
            assert evidence["proposal"]["requires_approval"] is True
            assert evidence["proposal"]["display"]["报价编号与版本"].endswith("V1")
            assert db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.kind == "quotation")) is None
            _, submitted = prepare_and_confirm(db, admin, run, args)
            first = db.get(m.BusinessSubject, submitted["subject_id"])
            assert first.status == "SUBMITTED"
            assert db.get(m.QuotationDetail, first.id).quoted_amount == Decimal("120000.00")
            inbound = db.scalar(select(m.QuoteInboundRecord))
            assert inbound.source_ref == "MAIL-QUOTE-001"
            assert inbound.content_sha256 == "a" * 64
            assert db.get(m.QuotationSourceLink, (first.id, inbound.id))
            approve_instance(db, admin, submitted["instance_id"])
            assert first.status == "EFFECTIVE"
            ids.update({"first": first.id, "inbound": inbound.id})

        with Session.begin() as db:
            admin = db.scalar(select(m.User).where(m.User.username == "admin"))
            project_row = db.get(m.Project, ids["project"])
            run = db.get(m.Run, ids["run"])
            feedback_args = {
                "project_id": project_row.id, "project_version": project_row.row_version,
                "quotation_subject_id": ids["first"], "feedback_type": "REVISION_REQUESTED",
                "feedback_date": date.today().isoformat(),
                "evidence": "客户邮件要求调整付款条件并重新报价",
                "source_ref": "MAIL-FEEDBACK-001",
            }
            evidence = execute(db, admin, "prepare_quotation_feedback", feedback_args, run=run)
            assert evidence["proposal"]["requires_approval"] is False
            assert db.scalar(select(m.QuotationFeedback)) is None
            step = m.Step(
                run_id=run.id, sequence=1, tool="prepare_quotation_feedback",
                request_hash="feedback-hash", result=evidence,
            )
            db.add(step)
            db.flush()
            payload = {"step_id": step.id, "proposal_hash": bpm.content_hash(evidence["proposal"])}
            intent = business.create_intent(db, admin, "quotation.execute", step.id, payload)
            recorded = business.confirm_intent(db, admin, intent["id"], intent["challenge"])
            assert recorded["status"] == "RECORDED"
            assert db.get(m.BusinessSubject, ids["first"]).status == "EFFECTIVE"
            with pytest.raises(Exception) as duplicate:
                execute(db, admin, "prepare_quotation_feedback", feedback_args, run=run)
            assert getattr(duplicate.value, "code", None) == "QUOTE_FEEDBACK_DUPLICATE"

        with Session.begin() as db:
            admin = db.scalar(select(m.User).where(m.User.username == "admin"))
            project_row = db.get(m.Project, ids["project"])
            definition = db.get(m.WorkflowDefinition, ids["quote_definition"])
            run = db.get(m.Run, ids["run"])
            original_blob = db.get(m.FileObject, ids["blob"])
            reuploaded_blob = m.FileObject(
                owner_id=admin.id,
                conversation_id=run.conversation_id,
                request_key="quote-reupload-" + original_blob.sha256[:18],
                filename=project_row.code + "-drawing-reuploaded.pdf",
                media_type=original_blob.media_type,
                size=original_blob.size,
                sha256=original_blob.sha256,
                backend=original_blob.backend,
                storage_namespace=original_blob.storage_namespace,
                object_key="test/reupload/" + original_blob.sha256 + "/drawing.pdf",
                storage_version=None,
            )
            db.add(reuploaded_blob)
            db.flush()
            db.add(m.RunFile(run_id=run.id, file_id=reuploaded_blob.id))
            args = quotation_args(
                project_row, definition, reuploaded_blob.id,
                version=2, previous_id=ids["first"],
            )
            args["owner_user_id"] = admin.id
            _, submitted = prepare_and_confirm(db, admin, run, args, sequence=2)
            second = db.get(m.BusinessSubject, submitted["subject_id"])
            approve_instance(db, admin, submitted["instance_id"])
            assert db.get(m.BusinessSubject, ids["first"]).status == "CLOSED"
            assert second.status == "EFFECTIVE"
            assert db.scalar(select(m.QuoteInboundRecord.id)) == ids["inbound"]
            assert len(list(db.scalars(select(m.QuoteInboundRecord.id)))) == 1
            assert db.get(m.QuotationSourceLink, (second.id, ids["inbound"]))
            ids["second"] = second.id

        with Session.begin() as db:
            admin = db.scalar(select(m.User).where(m.User.username == "admin"))
            project_row = db.get(m.Project, ids["project"])
            result = execute(db, admin, "query_quote_evaluation_context", {"project_id": project_row.id})
            analysis = result["data"][0]["analysis"]
            assert analysis["status_summary"] == {
                "project_status": "DRAFT",
                "quotation_version_status": "EFFECTIVE",
                "acceptance_decision_status": "NOT_DECIDED",
                "sales_contract_status": "NOT_RECORDED",
            }
            assert analysis["current_effective_quotation"]["quotation_status"] == "EFFECTIVE"
            assert analysis["open_quotation_versions"] == []
            assert analysis["derived_status"]["has_structured_cost_process_duration_breakdown"] is True
            assert len(analysis["quotation_versions"]) == 2
            assert analysis["customer_feedback_signals"]["feedback_records"][0]["feedback_type"] == "REVISION_REQUESTED"
            acceptance_definition = db.get(m.WorkflowDefinition, ids["acceptance_definition"])
            base = {
                "project_id": project_row.id, "project_version": project_row.row_version,
                "decision": "ACCEPT", "execution_mode": "INTERNAL",
                "effective_date": date.today().isoformat(), "evidence": "客户确认V2报价并同意承接",
                "amount": "125000.00", "currency": "CNY",
                "workflow_definition_id": acceptance_definition.id,
            }
            run = db.get(m.Run, ids["run"])
            with pytest.raises(Exception) as missing_source:
                execute(db, admin, "prepare_quote_acceptance_decision", base, run=run)
            assert getattr(missing_source.value, "code", None) == "QUOTE_VERSION_SOURCE_REQUIRED"
            with pytest.raises(Exception) as amount_mismatch:
                execute(db, admin, "prepare_quote_acceptance_decision", {
                    **base, "quotation_subject_id": ids["second"], "amount": "1.00",
                }, run=run)
            assert getattr(amount_mismatch.value, "code", None) == "QUOTE_AMOUNT_MISMATCH"
            prepared = execute(db, admin, "prepare_quote_acceptance_decision", {
                **base, "quotation_subject_id": ids["second"],
            }, run=run)
            assert prepared["proposal"]["display"]["引用报价版本"].endswith("V2")
            assert db.scalar(select(m.BusinessSubject).where(
                m.BusinessSubject.kind == "quote_acceptance"
            )) is None
    finally:
        engine.dispose()


def test_outsource_quotation_requires_supplier_evaluation_fields():
    schema = tool_schema("prepare_quotation_version")["function"]["parameters"]
    assert "supplier_quote_amount" in schema["properties"]
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db)
            project_row = project(db, "QUOTE-OUTSOURCE-001")
            definition = workflow(db, admin, "quotation")
            run, blob = run_with_file(db, admin, project_row.code, digest="b" * 64)
            args = quotation_args(project_row, definition, blob.id)
            args.update({
                "owner_user_id": admin.id,
                "preliminary_execution_mode": "FULL_OUTSOURCE",
                "cost_amount": None,
            })
            with pytest.raises(Exception) as incomplete:
                execute(db, admin, "prepare_quotation_version", args, run=run)
            assert getattr(incomplete.value, "code", None) == "INVALID_TOOL_INPUT"
            args.update({
                "supplier_quote_amount": "90000.00",
                "supplier_delivery_date": (date.today() + timedelta(days=70)).isoformat(),
                "supplier_requirements": "供应商负责设计、生产、装配和首轮试模",
                "supplier_quote_evidence": "供应商盖章报价单 SUP-QUOTE-001",
            })
            evidence = execute(db, admin, "prepare_quotation_version", args, run=run)
            assert "90000.00 CNY" in evidence["proposal"]["display"]["供应商评估"]
    finally:
        engine.dispose()
