from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import bpm, business, models as m
from app.db import now
from app.errors import DomainError
from app.security import hasher
from domain_packs.mold.erp.core.domain_api import LineInput, PurchaseInput
from pg_db import factory as pg_factory


CONTRACT = {
    "fields": [{"key": "urgent", "label": "是否紧急", "type": "boolean"}],
    "tables": [{"key": "design", "label": "设计清单", "fields": [
        {"key": "item", "label": "料号", "type": "text"},
        {"key": "quantity", "label": "数量", "type": "decimal"},
    ]}],
}


@pytest.fixture()
def pg_session():
    engine, session_factory = pg_factory()
    with session_factory() as db:
        yield db
    engine.dispose()


def fixture(db, *, review_status="CONFIRMED"):
    user = m.User(username="owner", display_name="项目负责人", password_hash=hasher.hash("SyntheticPassword-2026!"), super_admin=True)
    project = m.Project(code="MB-M001", name="资料绑定项目", status="ACTIVE")
    material = m.Material(code="MB-H001", name="核对物料", category="hardware", unit="件")
    db.add_all([user, project, material]); db.flush()
    request = business.create_request(db, user, PurchaseInput(project_id=project.id, remark="资料绑定提交",
        lines=[LineInput(material_id=material.id, quantity=Decimal("2"), due_date=date(2026, 9, 20))]))
    template = m.MaterialTemplate(template_key="binding_sheet", version=1, name="绑定资料模板", status="PUBLISHED",
        contract=CONTRACT, package_hash=bpm.content_hash({"template_key":"binding_sheet","version":1,"contract":CONTRACT}))
    db.add(template); db.flush()
    conversation = m.Conversation(user_id=user.id, title="资料绑定附件")
    db.add(conversation); db.flush()
    file = m.FileObject(
        owner_id=user.id,
        conversation_id=conversation.id,
        request_key="file-1",
        filename="binding.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size=32,
        sha256="2" * 64,
        backend="local",
        storage_namespace="test",
        object_key="material-binding/" + "2" * 64,
        storage_version=None,
    )
    db.add(file); db.flush()
    material_data = {"fields": {"urgent": True}, "tables": {"design": [{"id": "MAT-BIND", "values": {"item": "MAT-BIND", "quantity": "2"}}]}}
    review = m.MaterialReview(template_id=template.id, mapping_id=None, file_id=file.id, owner_id=user.id,
        status=review_status, material_data=material_data, issues=[] if review_status == "CONFIRMED" else [{"code": "PENDING"}],
        template_hash=template.package_hash, mapping_hash="1"*64, file_sha256=file.sha256,
        review_hash=bpm.content_hash({"material_data": material_data}), confirmed_by=user.id if review_status == "CONFIRMED" else None,
        confirmed_at=now() if review_status == "CONFIRMED" else None)
    config = {"business_type": "generic", "material_contract": CONTRACT,
        "nodes": [{"key": "review", "name": "资料核对", "users": [user.id], "mode": "ALL", "reject_rules": []}]}
    definition = m.WorkflowDefinition(process_key="material_binding", version=1, name="资料绑定审批",
        material_template_id=template.id, config=config, bpmn_xml=bpm.compile_bpmn(config),
        status="PUBLISHED", package_hash=bpm.content_hash({"config": config}))
    db.add_all([review, definition]); db.flush()
    return user, request, template, review, definition


def test_submit_request_freezes_confirmed_material_review(pg_session):
    db = pg_session
    user, request, template, review, definition = fixture(db)
    result = business.submit_request(db, user, request.id, request.revision, definition.id, material_review_id=review.id)
    instance = db.get(m.ApprovalInstance, result["instance_id"])
    binding = db.scalar(select(m.MaterialBinding).where(m.MaterialBinding.review_id == review.id))
    assert binding.resource_type == "purchase_request"
    assert binding.resource_id == request.id
    assert binding.template_id == template.id
    assert instance.snapshot["material_data"]["fields"]["urgent"] is True
    assert instance.snapshot["material_binding"]["binding_id"] == binding.id
    assert instance.snapshot["material_binding"]["review_hash"] == review.review_hash


def test_submit_request_requires_confirmed_material_review(pg_session):
    db = pg_session
    user, request, _template, review, definition = fixture(db, review_status="READY_FOR_CONFIRMATION")
    with pytest.raises(DomainError) as error:
        business.submit_request(db, user, request.id, request.revision, definition.id, material_review_id=review.id)
    assert error.value.code == "MATERIAL_REVIEW_NOT_CONFIRMED"
    with pytest.raises(DomainError) as missing:
        business.submit_request(db, user, request.id, request.revision, definition.id)
    assert missing.value.code == "MATERIALS_NOT_BOUND"
