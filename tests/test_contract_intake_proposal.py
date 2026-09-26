from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from app import bpm, business, models as m
from app import document_worker
from domain_packs.mold.erp.commercial import contract_intake
from domain_packs.mold.erp.commercial.ocr_provider import Classification, ContractExtraction, ExtractedField
from app.authorization import PERMISSIONS, fingerprint
from app.tool_gateway import execute, tool_schema
from conftest import sign_in
from test_contract_intake_review import _review_payload, extracted_group
from test_core import confirm_decision
from test_files import PDF, upload


def _grant(db, admin_id, user_id, permission, project_id):
    db.add(m.Grant(
        user_id=user_id,
        permission=permission,
        effect="ALLOW",
        scope={"project_id": [project_id]},
        fields=PERMISSIONS[permission],
        reason="contract intake proposal test",
        granted_by=admin_id,
    ))


def _workflow(db, ctx, ids, *, reversed_roles=False):
    first = "FINANCE_OWNER" if reversed_roles else "SALES_SUPERVISOR"
    second = "SALES_SUPERVISOR" if reversed_roles else "FINANCE_OWNER"
    config = {
        "business_type": "sales_contract",
        "nodes": [
            {
                "key": "sales_review",
                "name": "业务主管审核",
                "users": [],
                "assignment": {
                    "roles": [], "departments": [], "department_heads_only": False,
                    "domain_roles": [first],
                },
                "mode": "ALL",
                "reject_rules": [],
            },
            {
                "key": "finance_confirm",
                "name": "财务确认",
                "users": [],
                "assignment": {
                    "roles": [], "departments": [], "department_heads_only": False,
                    "domain_roles": [second],
                },
                "mode": "ALL",
                "reject_rules": [],
            },
        ],
    }
    definition = m.WorkflowDefinition(
        process_key="sales_contract_ocr_reversed" if reversed_roles else "sales_contract_ocr",
        version=1,
        name="销售合同业务财务两级审批",
        status="PUBLISHED",
        config=config,
        bpmn_xml=bpm.compile_bpmn(config),
        package_hash="contract-intake-test",
    )
    db.add(definition)
    db.flush()
    if not reversed_roles:
        db.add(m.ProjectRoleConfig(project_id=ctx["project_id"], version=1))
        db.add_all([
            m.ProjectRoleMember(
                project_id=ctx["project_id"], role_key="SALES_SUPERVISOR", user_id=ids["reviewer"],
            ),
            m.ProjectRoleMember(
                project_id=ctx["project_id"], role_key="FINANCE_OWNER", user_id=ids["buyer"],
            ),
        ])
        for user_id in (ids["reviewer"], ids["buyer"]):
            _grant(db, ids["admin"], user_id, "sales_contract.read", ctx["project_id"])
            _grant(db, ids["admin"], user_id, "sales_contract.approve", ctx["project_id"])
    return definition


class _FakeProvider:
    name = "e2e-fake-ocr"


def _field(scope, row_key, key, value, page):
    wrapped = {"value": value}
    block_id = f"p{page}-b0001"
    return ExtractedField(
        scope, row_key, key, wrapped, wrapped, Decimal("0.9500"), page,
        {"source_block_ids": [block_id]}, (block_id,),
    )


def _complete_next_job(factory, result):
    claimed = document_worker.claim(factory)
    assert claimed
    job_id, lease_id = claimed
    with factory() as db:
        job = db.get(m.DocumentOcrJob, job_id)
        intake_file = db.get(m.DocumentIntakeFile, job.intake_file_id)
        phase, intake_file_id, intake_id, group_id = (
            job.phase, intake_file.id, intake_file.intake_id, intake_file.contract_group_id,
        )
    document_worker._succeed(
        factory, job_id, lease_id, phase, intake_file_id, intake_id, group_id,
        _FakeProvider(), result,
    )


def _ready_uploaded_group(client, data):
    ctx = extracted_group(data, project_code="M260199", customer_name="端到端客户", payment_amount="400.00")
    ids, factory = data
    with factory.begin() as db:
        second_mold = m.Mold(internal_number="CM-200", name="后保险杠模具")
        db.add(second_mold); db.flush()
        db.add(m.ProjectMold(project_id=ctx["project_id"], mold_id=second_mold.id))
        ctx["second_mold_id"] = second_mold.id
    sign_in(client)
    response = client.post('/api/files/batch',params={'request_key':str(uuid4())},files=[
        ('files',('销售合同主件.pdf',PDF+b'\n% first','application/pdf')),
        ('files',('销售合同附件.pdf',PDF+b'\n% second','application/pdf'))])
    assert response.status_code==200,response.text
    first, second = response.json()['files']
    with factory() as db:
        intake_id = db.scalar(select(m.DocumentIntake.id).where(m.DocumentIntake.conversation_id==first['conversation_id']))
    _complete_next_job(factory, Classification("SALES_CONTRACT", Decimal("0.9800")))
    _complete_next_job(factory, Classification("SALES_CONTRACT", Decimal("0.9700")))
    with factory.begin() as db:
        admin = db.get(m.User, ids["admin"])
        intake = contract_intake.serialize(db, contract_intake.load(db, admin, intake_id))
        assert intake["status"] == "AWAITING_TYPE_CONFIRMATION"
        confirmed = contract_intake.confirm_types(
            db, admin, intake["id"], expected_version=intake["row_version"],
            confirmations=[{
                "intake_file_id": row["id"], "document_type": "SALES_CONTRACT",
                "contract_group_key": "contract-1",
            } for row in intake["files"]],
        )
        confirmed_payload = contract_intake.serialize(db, confirmed)
        group_id = confirmed_payload["files"][0]["contract_group_id"]
    _complete_next_job(factory, ContractExtraction((
        _field("HEADER", "header", "project_number", "M260199", 1),
        _field("HEADER", "header", "customer_name", "端到端客户", 1),
        _field("HEADER", "header", "contract_number", "SC-E2E-199", 1),
        _field("HEADER", "header", "amount", "1500.00", 1),
        _field("HEADER", "header", "currency", "CNY", 1),
        _field("MOLD", "p1:mold-1", "customer_mold_number", "CM-199", 1),
        _field("MOLD", "p1:mold-1", "amount", "900.00", 1),
    )))
    _complete_next_job(factory, ContractExtraction((
        _field("MOLD", "p1:mold-1", "customer_mold_number", "CM-200", 1),
        _field("MOLD", "p1:mold-1", "amount", "600.00", 1),
        _field("PAYMENT", "p2:payment-1", "name", "预付款", 2),
        _field("PAYMENT", "p2:payment-1", "amount", "500.00", 2),
    )))
    with factory.begin() as db:
        admin = db.get(m.User, ids["admin"])
        group = contract_intake.load_group(db, admin, group_id)
        detail = contract_intake.serialize_group(db, group)
        candidates = contract_intake.project_candidates(db, admin, group_id)
        project = next(row for row in candidates["projects"] if row["id"] == ctx["project_id"])
        mold_rows = {}
        for field in detail["fields"]:
            if field["scope"] == "MOLD" and field["field_key"] == "customer_mold_number":
                mold_rows[field["row_key"]] = ctx["mold_id"] if field["raw_value"]["value"] == "CM-199" else ctx["second_mold_id"]
        reviewed = contract_intake.review_group(
            db, admin, group_id,
            expected_version=detail["row_version"],
            project_id=project["id"], project_version=project["row_version"],
            confirmed_fields=[{"field_id": row["id"], "confirmed_value": row["raw_value"]}
                              for row in detail["fields"]],
            mold_mappings=[{"row_key": key, "mold_id": value} for key, value in mold_rows.items()],
            relationship={"relation_type": "NEW", "target_contract_id": None, "reason": "端到端首次合同"},
        )
        reviewed_payload = contract_intake.serialize_group(db, reviewed)
        assert reviewed_payload["status"] == "READY_FOR_DRAFT"
        assert {row["source"]["filename"] for row in reviewed_payload["fields"]} == {"销售合同主件.pdf", "销售合同附件.pdf"}
        ctx.update({"group_id": group_id, "group_version": reviewed_payload["row_version"], "source_file_ids": [first["id"], second["id"]]})
    return ctx


def _ready_group(data):
    from domain_packs.mold.erp.commercial import contract_intake

    ctx = extracted_group(data)
    with data[1].begin() as db:
        admin = db.get(m.User, data[0]["admin"])
        payload = _review_payload(ctx)
        contract_intake.review_group(
            db,
            admin,
            ctx["group_id"],
            expected_version=payload["expected_version"],
            project_id=payload["project_id"],
            project_version=payload["project_version"],
            confirmed_fields=payload["confirmed_fields"],
            mold_mappings=payload["mold_mappings"],
            relationship=payload["relationship"],
        )
    ctx["group_version"] += 1
    return ctx


def test_confirmed_intake_proposal_creates_contract_and_two_stage_approval(client, data):
    ctx = _ready_uploaded_group(client, data)
    ids, factory = data
    with factory.begin() as db:
        definition = _workflow(db, ctx, ids)
        admin = db.get(m.User, ids["admin"])
        conversation = m.Conversation(user_id=admin.id, title="从OCR登记销售合同")
        db.add(conversation)
        db.flush()
        run = m.Run(
            conversation_id=conversation.id,
            user_id=admin.id,
            security_version=admin.security_version,
            prompt="确认识别结果并登记销售合同",
            status="SUCCEEDED",
            checkpoint={"authorization_hash": fingerprint(db, admin), "agent_permission_mode": "ask"},
        )
        db.add(run)
        db.flush()
        run_id = run.id
        definition_id = definition.id

    schema = tool_schema("prepare_sales_contract_from_intake")["function"]["parameters"]
    assert {"contract_intake_group_id", "expected_version", "workflow_definition_id"} <= set(schema["properties"])
    with factory.begin() as db:
        admin = db.get(m.User, ids["admin"])
        run = db.get(m.Run, run_id)
        queried = execute(db, admin, "query_sales_contract_intake", {
            "contract_intake_group_id": ctx["group_id"],
        }, run=run)
        assert queried["data"][0]["status"] == "READY_FOR_DRAFT"
        evidence = execute(db, admin, "prepare_sales_contract_from_intake", {
            "contract_intake_group_id": ctx["group_id"],
            "expected_version": ctx["group_version"],
            "workflow_definition_id": definition_id,
        }, run=run)
        proposal = evidence["proposal"]
        assert proposal["requires_approval"] is True
        # 接收记录按时间及 UUID 排序，此处核对原件完整性，不假设主件在前。
        assert sorted(proposal["display"]["原始PDF"]) == ["销售合同主件.pdf", "销售合同附件.pdf"]
        assert proposal["display"]["审批顺序"] == ["业务主管审核", "财务确认"]
        assert db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.kind == "sales_contract")) is None
        step = m.Step(
            run_id=run.id,
            sequence=0,
            tool="prepare_sales_contract_from_intake",
            request_hash="intake-proposal",
            result=evidence,
        )
        db.add(step)
        db.flush()
        payload = {"step_id": step.id, "proposal_hash": bpm.content_hash(proposal)}
        intent = business.create_intent(db, admin, "contract_intake.execute", step.id, payload)
        receipt = business.confirm_intent(db, admin, intent["id"], intent["challenge"])
        assert receipt["status"] == "SUBMITTED"
        subject_id = receipt["subject_id"]
        instance_id = receipt["instance_id"]

    with factory() as db:
        subject = db.get(m.BusinessSubject, subject_id)
        group = db.get(m.ContractIntakeGroup, ctx["group_id"])
        assert subject.status == "SUBMITTED"
        assert group.status == "CONTRACT_DRAFT_CREATED"
        assert group.contract_subject_id == subject.id
        attachments = list(db.scalars(select(m.ContractAttachment).where(
            m.ContractAttachment.contract_subject_id == subject.id)))
        assert len(attachments) == 2
        assert {row.role for row in attachments} == {"MAIN", "ATTACHMENT"}
        assert all(row.intake_file_id for row in attachments)
        assert db.scalar(select(func.count()).select_from(m.ContractMoldLine).where(
            m.ContractMoldLine.contract_subject_id == subject.id)) == 2
        assert db.scalar(select(func.count()).select_from(m.PaymentStage).where(
            m.PaymentStage.contract_id == subject.id)) == 1
        first_seat = db.scalar(select(m.ApprovalSeat.user_id).where(
            m.ApprovalSeat.instance_id == instance_id,
            m.ApprovalSeat.stage_index == 0,
        ))
        instance = db.get(m.ApprovalInstance, instance_id)
        assert first_seat == ids["reviewer"], (instance.incident, instance.assignment_snapshots)

    sign_in(client, "test_reviewer")
    _, first = confirm_decision(client, instance_id)
    assert first.json()["status"] == "RUNNING"
    with factory() as db:
        assert db.get(m.BusinessSubject, subject_id).status != "EFFECTIVE"
        second_seat = db.scalar(select(m.ApprovalSeat.user_id).where(
            m.ApprovalSeat.instance_id == instance_id,
            m.ApprovalSeat.stage_index == 1,
        ))
        assert second_seat == ids["buyer"]

    sign_in(client, "test_buyer")
    _, second = confirm_decision(client, instance_id)
    assert second.json()["business_status"] == "EFFECTIVE"
    with factory() as db:
        assert db.get(m.BusinessSubject, subject_id).status == "EFFECTIVE"
        actions = set(db.scalars(select(m.AuditEvent.action).where(m.AuditEvent.resource_id.in_([
            ctx["group_id"], subject_id,
        ]))))
        assert {"contract.intake.reviewed", "contract.document.linked"} <= actions


def test_prepare_intake_contract_rejects_wrong_workflow_and_stale_group(data):
    ctx = _ready_group(data)
    ids, factory = data
    with factory.begin() as db:
        definition = _workflow(db, ctx, ids, reversed_roles=True)
        admin = db.get(m.User, ids["admin"])
        conversation = m.Conversation(user_id=admin.id, title="错误合同流程")
        db.add(conversation)
        db.flush()
        run = m.Run(
            conversation_id=conversation.id,
            user_id=admin.id,
            security_version=admin.security_version,
            prompt="准备合同",
            status="SUCCEEDED",
            checkpoint={"authorization_hash": fingerprint(db, admin)},
        )
        db.add(run)
        db.flush()
        run_id, definition_id = run.id, definition.id
    with factory() as db:
        admin = db.get(m.User, ids["admin"])
        run = db.get(m.Run, run_id)
        try:
            execute(db, admin, "prepare_sales_contract_from_intake", {
                "contract_intake_group_id": ctx["group_id"],
                "expected_version": ctx["group_version"],
                "workflow_definition_id": definition_id,
            }, run=run)
            assert False, "错误审批顺序必须被拒绝"
        except Exception as error:
            assert getattr(error, "code", None) == "WORKFLOW_MISMATCH"
        try:
            execute(db, admin, "prepare_sales_contract_from_intake", {
                "contract_intake_group_id": ctx["group_id"],
                "expected_version": ctx["group_version"] - 1,
                "workflow_definition_id": definition_id,
            }, run=run)
            assert False, "旧分组版本必须被拒绝"
        except Exception as error:
            assert getattr(error, "code", None) == "STALE_VERSION"
