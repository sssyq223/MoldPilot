import pytest
from uuid import uuid4
from sqlalchemy import func, select

from app import business, models as m
from app.authorization import fingerprint
from app.bpm import content_hash
from app.config import settings
from app.tool_gateway import execute, tool_schema
from conftest import sign_in
from domain_packs.mold import proposal_handlers
from domain_packs.mold import tool_gateway as mold_tool_gateway
from domain_packs.mold.erp.commercial import contract_intake
from test_contract_intake_review import _review_payload, extracted_group
from test_files import PDF, upload


def _legacy_file(data, body, filename, cid=None):
    """明确构造升级前尚未进入识别的存量文件，验证旧 proposal 兼容边界。"""
    from app.files import persist_upload
    ids, factory = data
    with factory.begin() as db:
        return persist_upload(db,db.get(m.User,ids['admin']),filename,body,uuid4(),cid,finalize=False)


def _run(factory, ids, conversation_id, file_ids):
    with factory.begin() as db:
        admin = db.get(m.User, ids["admin"])
        run = m.Run(
            conversation_id=conversation_id,
            user_id=admin.id,
            security_version=admin.security_version,
            prompt="处理本次上传附件",
            status="SUCCEEDED",
            checkpoint={
                "authorization_hash": fingerprint(db, admin),
                "agent_permission_mode": "ask",
                "run_trigger": "ATTACHMENT_UPLOAD",
            },
        )
        db.add(run)
        db.flush()
        db.add_all([m.RunFile(run_id=run.id, file_id=file_id) for file_id in file_ids])
        return run.id


def _confirm(db, user, run, tool, evidence, sequence=0):
    step = m.Step(
        run_id=run.id,
        sequence=sequence,
        tool=tool,
        request_hash=f"{tool}-{sequence}",
        result=evidence,
    )
    db.add(step)
    db.flush()
    payload = {"step_id": step.id, "proposal_hash": content_hash(evidence["proposal"])}
    intent = business.create_intent(db, user, "document_intake.execute", step.id, payload)
    return business.confirm_intent(db, user, intent["id"], intent["challenge"])


def test_sales_contract_intake_skill_keeps_exact_eight_tool_boundary():
    skill = mold_tool_gateway.SKILLS["sales_contract_intake"]
    names = [*skill["tools"], *skill["optional_tools"]]
    assert names == [
        "query_uploaded_files",
        "query_document_intake",
        "prepare_document_intake",
        "prepare_document_type_confirmation",
        "prepare_document_ocr_retry",
        "query_sales_contract_intake",
        "prepare_sales_contract_intake_review",
        "prepare_sales_contract_from_intake",
    ]
    assert not any(name.startswith(("paddleocr_", "extract_pdf_", "document_model_")) for name in names)


def test_document_intake_tools_are_registered_as_one_proposal_boundary():
    expected = {
        "prepare_document_intake",
        "prepare_document_type_confirmation",
        "prepare_document_ocr_retry",
        "prepare_sales_contract_intake_review",
    }
    handler = proposal_handlers.handler_for_action("document_intake.execute")
    assert handler is not None
    assert handler.tools == expected
    for key in expected | {"query_document_intake"}:
        assert tool_schema(key)["function"]["parameters"]["type"] == "object"


def test_confirmed_document_intake_proposal_uses_only_current_run_files(client, data, monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "file_backend", "local")
    monkeypatch.setattr(settings(), "file_local_root", str(tmp_path / "objects"))
    sign_in(client)
    first = _legacy_file(data, PDF + b"\nfirst", "合同主件.pdf")
    second = _legacy_file(data, PDF + b"\nsecond", "合同附件.pdf", cid=first["conversation_id"])
    unrelated = _legacy_file(data, PDF + b"\nunrelated", "其他材料.pdf", cid=first["conversation_id"])
    ids, factory = data
    run_id = _run(factory, ids, first["conversation_id"], [first["id"], second["id"]])

    with factory.begin() as db:
        admin = db.get(m.User, ids["admin"])
        run = db.get(m.Run, run_id)
        queried = execute(db, admin, "query_uploaded_files", {}, run=run)
        assert {row["id"] for row in queried["data"]} == {first["id"], second["id"]}
        assert unrelated["id"] not in {row["id"] for row in queried["data"]}
        evidence = execute(db, admin, "prepare_document_intake", {
            "file_ids": [first["id"], second["id"]],
        }, run=run)
        assert evidence["proposal"]["requires_approval"] is False
        assert {row["id"] for row in evidence["proposal"]["display"]["文件"]} == {
            first["id"], second["id"],
        }
        assert db.scalar(select(func.count()).select_from(m.DocumentIntake)) == 0
        receipt = _confirm(db, admin, run, "prepare_document_intake", evidence)
        assert receipt["status"] == "PRECLASSIFYING"
        intake_id = receipt["document_intake_id"]

    with factory() as db:
        intake = db.get(m.DocumentIntake, intake_id)
        assert intake.conversation_id == first["conversation_id"]
        intake_files = list(db.scalars(select(m.DocumentIntakeFile).where(
            m.DocumentIntakeFile.intake_id == intake.id,
        )))
        assert {row.file_id for row in intake_files} == {first["id"], second["id"]}
        assert db.scalar(select(func.count()).select_from(m.DocumentOcrJob).where(
            m.DocumentOcrJob.intake_file_id.in_([row.id for row in intake_files]),
            m.DocumentOcrJob.phase == "PRECLASSIFY",
            m.DocumentOcrJob.status == "QUEUED",
        )) == 2


@pytest.fixture
def document_proposal_for_http(client, data, monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "file_backend", "local")
    monkeypatch.setattr(settings(), "file_local_root", str(tmp_path / "objects"))
    sign_in(client)
    uploaded = _legacy_file(data, PDF, "合同确认接口回归.pdf")
    ids, factory = data
    run_id = _run(factory, ids, uploaded["conversation_id"], [uploaded["id"]])
    with factory.begin() as db:
        user = db.get(m.User, ids["admin"])
        run = db.get(m.Run, run_id)
        evidence = execute(db, user, "prepare_document_intake", {
            "file_ids": [uploaded["id"]],
        }, run=run)
        step = m.Step(
            run_id=run.id, sequence=0, tool="prepare_document_intake",
            request_hash="document-intake-http-regression", result=evidence,
        )
        db.add(step)
        db.flush()
        return step.id, uploaded["id"]


def test_document_proposal_http_review_then_confirm_creates_one_ocr_job(
    client, data, document_proposal_for_http,
):
    step_id, file_id = document_proposal_for_http
    factory = data[1]
    response = client.post(f"/api/proposals/{step_id}/intent")
    assert response.status_code == 200, response.text
    intent = response.json()
    assert intent["display"]["文件"][0]["id"] == file_id
    assert intent["confirmation_policy"]["requires_human_confirmation"] is True
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(m.DocumentIntake)) == 0
        assert db.scalar(select(func.count()).select_from(m.DocumentOcrJob)) == 0
    endpoint = f"/api/human-actions/{intent['id']}/confirm"
    response = client.post(endpoint, json={"challenge": intent["challenge"]})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PRECLASSIFYING"
    repeated = client.post(endpoint, json={"challenge": intent["challenge"]})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == response.json()
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(m.DocumentIntake)) == 1
        assert db.scalar(select(func.count()).select_from(m.DocumentOcrJob)) == 1


def test_document_proposal_http_rejects_actual_content_change(
    client, data, document_proposal_for_http,
):
    step_id, _file_id = document_proposal_for_http
    factory = data[1]
    response = client.post(f"/api/proposals/{step_id}/intent")
    assert response.status_code == 200, response.text
    intent = response.json()
    with factory.begin() as db:
        step = db.get(m.Step, step_id)
        proposal = step.result["proposal"]
        step.result = {**step.result, "proposal": {
            **proposal, "display": {**proposal["display"], "操作": "已被修改的操作"},
        }}
    response = client.post(f"/api/human-actions/{intent['id']}/confirm",
                           json={"challenge": intent["challenge"]})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CONFIRMATION_INVALID"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(m.DocumentIntake)) == 0
        assert db.scalar(select(func.count()).select_from(m.DocumentOcrJob)) == 0


def test_type_confirmation_and_failed_ocr_retry_require_separate_confirmations(client, data, monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "file_backend", "local")
    monkeypatch.setattr(settings(), "file_local_root", str(tmp_path / "objects"))
    sign_in(client)
    blob = upload(client, PDF, "待分类合同.pdf").json()
    ids, factory = data
    run_id = _run(factory, ids, blob["conversation_id"], [blob["id"]])
    with factory.begin() as db:
        admin = db.get(m.User, ids["admin"])
        intake = db.scalar(select(m.DocumentIntake).where(m.DocumentIntake.conversation_id==blob['conversation_id']))
        intake_file = db.scalar(select(m.DocumentIntakeFile).where(
            m.DocumentIntakeFile.intake_id == intake.id,
        ))
        job = db.scalar(select(m.DocumentOcrJob).where(
            m.DocumentOcrJob.intake_file_id == intake_file.id,
        ))
        job.status = "SUCCEEDED"
        intake_file.suggested_type = "SALES_CONTRACT"
        intake.status = "AWAITING_TYPE_CONFIRMATION"
        intake.row_version += 1
        intake_id, intake_file_id, version = intake.id, intake_file.id, intake.row_version

    with factory.begin() as db:
        admin = db.get(m.User, ids["admin"])
        run = db.get(m.Run, run_id)
        evidence = execute(db, admin, "prepare_document_type_confirmation", {
            "document_intake_id": intake_id,
            "expected_version": version,
            "files": [{
                "intake_file_id": intake_file_id,
                "document_type": "SALES_CONTRACT",
                "contract_group_key": "contract-1",
            }],
        }, run=run)
        assert db.get(m.DocumentIntake, intake_id).status == "AWAITING_TYPE_CONFIRMATION"
        receipt = _confirm(db, admin, run, "prepare_document_type_confirmation", evidence)
        assert receipt["status"] == "FULL_OCR_QUEUED"

    with factory.begin() as db:
        intake = db.get(m.DocumentIntake, intake_id)
        full_job = db.scalar(select(m.DocumentOcrJob).where(
            m.DocumentOcrJob.phase == "FULL_CONTRACT",
        ))
        full_job.status = "FAILED"
        full_job.attempts = 3
        full_job.last_error = "OCR_PROVIDER_UNAVAILABLE"
        intake.status = "OCR_FAILED"
        intake.row_version += 1
        group = db.scalar(select(m.ContractIntakeGroup).where(
            m.ContractIntakeGroup.intake_id == intake.id,
        ))
        group.status = "OCR_FAILED"
        group.row_version += 1
        failed_version = intake.row_version

    with factory.begin() as db:
        admin = db.get(m.User, ids["admin"])
        run = db.get(m.Run, run_id)
        evidence = execute(db, admin, "prepare_document_ocr_retry", {
            "document_intake_id": intake_id,
            "expected_version": failed_version,
        }, run=run)
        assert db.scalar(select(m.DocumentOcrJob.status).where(
            m.DocumentOcrJob.phase == "FULL_CONTRACT",
        )) == "FAILED"
        receipt = _confirm(db, admin, run, "prepare_document_ocr_retry", evidence, sequence=1)
        assert receipt["status"] == "FULL_OCR_QUEUED"

    with factory() as db:
        retried = db.scalar(select(m.DocumentOcrJob).where(
            m.DocumentOcrJob.phase == "FULL_CONTRACT",
        ))
        assert retried.status == "QUEUED"
        assert retried.attempts == 0
        assert db.scalar(select(m.ContractIntakeGroup.status)) == "FULL_OCR_QUEUED"


def test_sales_contract_review_tool_writes_only_after_confirmation(data):
    ctx = extracted_group(data, project_code="M260288")
    ids, factory = data
    with factory() as db:
        group = db.get(m.ContractIntakeGroup, ctx["group_id"])
        intake = db.get(m.DocumentIntake, group.intake_id)
        conversation_id = intake.conversation_id
    run_id = _run(factory, ids, conversation_id, [])
    payload = _review_payload(ctx)
    payload["contract_intake_group_id"] = ctx["group_id"]

    with factory.begin() as db:
        admin = db.get(m.User, ids["admin"])
        run = db.get(m.Run, run_id)
        evidence = execute(db, admin, "prepare_sales_contract_intake_review", payload, run=run)
        assert db.get(m.ContractIntakeGroup, ctx["group_id"]).status == "AWAITING_FIELD_CONFIRMATION"
        receipt = _confirm(db, admin, run, "prepare_sales_contract_intake_review", evidence)
        assert receipt["status"] == "READY_FOR_DRAFT"

    with factory() as db:
        group = db.get(m.ContractIntakeGroup, ctx["group_id"])
        assert group.status == "READY_FOR_DRAFT"
        assert group.project_id == ctx["project_id"]
        assert db.scalar(select(func.count()).select_from(m.ContractIntakeMoldMatch).where(
            m.ContractIntakeMoldMatch.group_id == group.id,
        )) == 1
