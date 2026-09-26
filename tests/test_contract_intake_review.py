from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import models as m
from app.authorization import PERMISSIONS
from app.errors import DomainError
from domain_packs.mold.erp.commercial import contract_intake


def _value(value):
    return {"value": value}


def extracted_group(data, *, project_code="M260100", customer_name="测试客户", payment_amount="400.00"):
    ids, factory = data
    with factory.begin() as db:
        admin = db.get(m.User, ids["admin"])
        suffix = project_code[-3:]
        customer = m.Customer(code=f"C-{project_code}", name=customer_name)
        project = m.Project(code=project_code, name="OCR合同项目", status="ACTIVE")
        mold = m.Mold(internal_number=f"CM-{suffix}", name="前保险杠模具")
        db.add_all([customer, project, mold])
        db.flush()
        db.add(m.ProjectProfile(
            project_id=project.id,
            customer_id=customer.id,
            owner_user_id=admin.id,
            execution_mode="INTERNAL",
        ))
        db.add(m.ProjectMold(project_id=project.id, mold_id=mold.id))
        conversation = m.Conversation(user_id=admin.id, title="合同OCR复核")
        db.add(conversation)
        db.flush()
        blob = m.FileObject(
            owner_id=admin.id,
            conversation_id=conversation.id,
            request_key=str(uuid4()),
            filename="销售合同.pdf",
            media_type="application/pdf",
            size=100,
            sha256="a" * 64,
            backend="local",
            storage_namespace="local",
            object_key=f"objects/review-contract-{project_code}",
            storage_version=None,
        )
        db.add(blob)
        db.flush()
        intake = m.DocumentIntake(
            conversation_id=conversation.id,
            created_by=admin.id,
            request_key=str(uuid4()),
            status="AWAITING_FIELD_CONFIRMATION",
            row_version=3,
        )
        db.add(intake)
        db.flush()
        group = m.ContractIntakeGroup(
            intake_id=intake.id,
            group_key="contract-1",
            status="AWAITING_FIELD_CONFIRMATION",
            row_version=2,
        )
        db.add(group)
        db.flush()
        intake_file = m.DocumentIntakeFile(
            intake_id=intake.id,
            file_id=blob.id,
            contract_group_id=group.id,
            confirmed_type="SALES_CONTRACT",
            confirmed_role="MAIN",
            confirmed_by=admin.id,
        )
        db.add(intake_file)
        db.flush()
        job = m.DocumentOcrJob(
            intake_file_id=intake_file.id,
            phase="FULL_CONTRACT",
            status="SUCCEEDED",
        )
        db.add(job)
        db.flush()
        definitions = [
            ("HEADER", "header", "project_number", project_code),
            ("HEADER", "header", "customer_name", customer_name),
            ("HEADER", "header", "contract_number", "SC-100"),
            ("HEADER", "header", "amount", "1000.00"),
            ("HEADER", "header", "currency", "CNY"),
            ("MOLD", "p1:mold-1", "customer_mold_number", f"CM-{suffix}"),
            ("MOLD", "p1:mold-1", "amount", "900.00"),
            ("PAYMENT", "p2:payment-1", "name", "预付款"),
            ("PAYMENT", "p2:payment-1", "amount", payment_amount),
        ]
        fields = []
        for scope, row_key, field_key, value in definitions:
            field = m.DocumentExtractedField(
                job_id=job.id,
                scope=scope,
                row_key=row_key,
                field_key=field_key,
                raw_value=_value(value),
                normalized_value=_value(value),
                source_block_ids=["p1-b0001"],
                confidence=Decimal("0.9000"),
                page_number=1,
            )
            db.add(field)
            db.flush()
            fields.append(field)
        result = {
            "group_id": group.id,
            "group_version": group.row_version,
            "project_id": project.id,
            "project_version": project.row_version,
            "customer_id": customer.id,
            "mold_id": mold.id,
            "field_ids": {f"{row.scope}:{row.row_key}:{row.field_key}": row.id for row in fields},
            "raw": {row.id: row.raw_value for row in fields},
        }
    return result


def _review_payload(ctx, **changes):
    payload = {
        "expected_version": ctx["group_version"],
        "project_id": ctx["project_id"],
        "project_version": ctx["project_version"],
        "confirmed_fields": [
            {"field_id": field_id, "confirmed_value": raw}
            for field_id, raw in ctx["raw"].items()
        ],
        "mold_mappings": [{"row_key": "p1:mold-1", "mold_id": ctx["mold_id"]}],
        "relationship": {"relation_type": "NEW", "target_contract_id": None, "reason": "首次合同"},
    }
    payload.update(changes)
    return payload


def _user(db, data, key="admin"):
    return db.get(m.User, data[0][key])


def _detail(data, group_id, key="admin"):
    with data[1]() as db:
        group = contract_intake.load_group(db, _user(db, data, key), group_id)
        return contract_intake.serialize_group(db, group)


def _candidates(data, group_id, key="admin"):
    with data[1]() as db:
        return contract_intake.project_candidates(db, _user(db, data, key), group_id)


def _review(data, ctx, payload=None, key="admin"):
    values = payload or _review_payload(ctx)
    with data[1].begin() as db:
        user = _user(db, data, key)
        group = contract_intake.review_group(
            db, user, ctx["group_id"],
            expected_version=values["expected_version"],
            project_id=values["project_id"],
            project_version=values["project_version"],
            confirmed_fields=values["confirmed_fields"],
            mold_mappings=values["mold_mappings"],
            relationship=values["relationship"],
        )
        return contract_intake.serialize_group(db, group)


def test_contract_intake_detail_includes_field_source_file(data):
    ctx = extracted_group(data)
    detail = _detail(data, ctx["group_id"])
    contract_number = next(row for row in detail["fields"] if row["field_key"] == "contract_number")
    assert contract_number["source"] == {
        "file_id": contract_number["source"]["file_id"],
        "filename": "销售合同.pdf",
        "page_number": 1,
        "block_ids": ["p1-b0001"],
    }


def test_candidates_match_but_never_auto_confirm_and_allow_manual_existing_project(data):
    ctx = extracted_group(data)
    body = _candidates(data, ctx["group_id"])
    assert body["resolution"] == "RESOLVED"
    assert body["projects"][0]["id"] == ctx["project_id"]
    with data[1]() as db:
        assert db.get(m.ContractIntakeGroup, ctx["group_id"]).project_id is None

    with data[1].begin() as db:
        customer = db.get(m.Customer, ctx["customer_id"])
        shared_mold = db.get(m.Mold, ctx["mold_id"])
        second = m.Project(code="M260999", name="第二合同项目", status="ACTIVE")
        db.add(second)
        db.flush()
        db.add_all([
            m.ProjectProfile(project_id=second.id, customer_id=customer.id,
                             owner_user_id=data[0]["admin"], execution_mode="INTERNAL"),
            m.ProjectMold(project_id=second.id, mold_id=shared_mold.id),
        ])
        db.get(m.DocumentExtractedField, ctx["field_ids"]["HEADER:header:project_number"]).normalized_value = _value("UNKNOWN")
        second_id = second.id
    multiple = _candidates(data, ctx["group_id"])
    assert multiple["resolution"] == "MULTIPLE_CANDIDATES"
    assert {row["id"] for row in multiple["projects"]} >= {ctx["project_id"], second_id}

    with data[1].begin() as db:
        for field in db.scalars(select(m.DocumentExtractedField)):
            if field.field_key in {"project_number", "customer_name", "customer_mold_number"}:
                field.normalized_value = _value("NO-MATCH")
    assert _candidates(data, ctx["group_id"])["resolution"] == "NOT_FOUND"
    assert _review(data, ctx)["project_id"] == ctx["project_id"]


def test_review_requires_every_mapping_and_preserves_machine_values(data):
    ctx = extracted_group(data)
    with pytest.raises(DomainError) as error:
        _review(data, ctx, _review_payload(ctx, mold_mappings=[]))
    assert error.value.code == "MOLD_MAPPING_REQUIRED"

    body = _review(data, ctx)
    assert body["status"] == "READY_FOR_DRAFT"
    assert "MOLD_TOTAL_MISMATCH" in [row["code"] for row in body["review_warnings"]]
    with data[1]() as db:
        group = db.get(m.ContractIntakeGroup, ctx["group_id"])
        field = db.get(m.DocumentExtractedField, next(iter(ctx["raw"])))
        match = db.scalar(select(m.ContractIntakeMoldMatch).where(
            m.ContractIntakeMoldMatch.group_id == group.id))
        assert group.project_version == ctx["project_version"]
        assert field.raw_value == ctx["raw"][field.id]
        assert field.confirmed_value == ctx["raw"][field.id]
        assert match.mold_id == ctx["mold_id"]


def test_review_blocks_customer_errors_and_excess_payment(data):
    ctx = extracted_group(data)
    with data[1].begin() as db:
        db.get(m.ProjectProfile, ctx["project_id"]).customer_id = None
    with pytest.raises(DomainError) as error:
        _review(data, ctx)
    assert error.value.code == "CUSTOMER_MAPPING_REQUIRED"

    with data[1].begin() as db:
        db.get(m.ProjectProfile, ctx["project_id"]).customer_id = ctx["customer_id"]
    payload = _review_payload(ctx)
    customer_field = ctx["field_ids"]["HEADER:header:customer_name"]
    for row in payload["confirmed_fields"]:
        if row["field_id"] == customer_field:
            row["confirmed_value"] = _value("另一客户")
    with pytest.raises(DomainError) as error:
        _review(data, ctx, payload)
    assert error.value.code == "CUSTOMER_CONFLICT"

    payment_ctx = extracted_group(data, project_code="M260101", payment_amount="1200.00")
    with pytest.raises(DomainError) as error:
        _review(data, payment_ctx)
    assert error.value.code == "PAYMENT_TOTAL_EXCEEDS_CONTRACT"


def test_review_rejects_stale_project_and_mold_outside_project(data):
    ctx = extracted_group(data)
    with pytest.raises(DomainError) as error:
        _review(data, ctx, _review_payload(ctx, project_version=ctx["project_version"] + 1))
    assert error.value.code == "PROJECT_VERSION_CHANGED"

    with data[1].begin() as db:
        outside = m.Mold(internal_number="OUTSIDE-1", name="其他项目模具")
        db.add(outside)
        db.flush()
        outside_id = outside.id
    payload = _review_payload(ctx)
    payload["mold_mappings"][0]["mold_id"] = outside_id
    with pytest.raises(DomainError) as error:
        _review(data, ctx, payload)
    assert error.value.code == "MOLD_NOT_IN_PROJECT"


def test_candidates_do_not_disclose_forbidden_projects(data):
    ctx = extracted_group(data)
    ids, factory = data
    with factory.begin() as db:
        buyer = db.get(m.User, ids["buyer"])
        admin = db.get(m.User, ids["admin"])
        db.add_all([
            m.Grant(user_id=buyer.id, permission="project.read", effect="ALLOW",
                    scope={"project_id": [ctx["project_id"]]}, fields=PERMISSIONS["project.read"],
                    reason="review test", granted_by=admin.id),
            m.Grant(user_id=buyer.id, permission="project.dossier.read", effect="ALLOW",
                    scope={"project_id": [ctx["project_id"]]}, fields=["*"],
                    reason="review test", granted_by=admin.id),
        ])
        group = db.get(m.ContractIntakeGroup, ctx["group_id"])
        intake = db.get(m.DocumentIntake, group.intake_id)
        intake.created_by = buyer.id
        db.get(m.Conversation, intake.conversation_id).user_id = buyer.id
    assert _candidates(data, ctx["group_id"], "buyer")["projects"][0]["id"] == ctx["project_id"]

    with factory.begin() as db:
        db.query(m.Grant).filter(m.Grant.user_id == ids["buyer"]).delete()
    hidden = _candidates(data, ctx["group_id"], "buyer")
    assert hidden["resolution"] == "NOT_FOUND_OR_FORBIDDEN"
    assert hidden["projects"] == []
