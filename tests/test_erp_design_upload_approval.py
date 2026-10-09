from datetime import datetime, timedelta, timezone
import hashlib
import secrets

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.errors import DomainError
from app.events import record
from app.models import Base, User
from domain_packs.mold.erp.design import erp_design_upload as bridge


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def _approval_data(token: str):
    config = {
        "resolvedProcessCode": "design_new_model_approval",
        "processName": "新模设计审批",
        "currentNodeName": "设计负责人审批",
    }
    expected_date = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
    data = bridge.ApprovalConfigInput(
        session_id=700,
        sheet_type="steel",
        preview_rows=[{"rowIndex": 1, "qty": 1}],
        mold_code="M250238-P4",
        design_order_type="new_model",
        expected_date=expected_date,
    )
    binding = bridge._approval_binding(data, "new_model", config)
    return config, data, {
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "payload_hash": bridge._approval_hash(binding),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        "consumed": False,
    }


def test_design_import_receipt_exposes_erp_workflow_fields():
    receipt = bridge._import_receipt_detail({
        "requestNo": "PR-20260929-01",
        "requestId": "request-1",
        "processCode": "design_new_model_approval",
        "processName": "新模设计审批",
        "currentNodeName": "设计负责人审批",
        "currentApproverNames": ["张设计"],
        "approvalSteps": [
            {"nodeName": "设计负责人审批", "approverNames": ["张设计"]},
            {"nodeName": "采购审批", "approverNames": ["李采购"]},
        ],
        "approvalStatus": "PENDING",
        "workflowInstanceId": "workflow-1",
    }, 1)

    assert receipt["request_no"] == "PR-20260929-01"
    assert receipt["process_code"] == "design_new_model_approval"
    assert receipt["current_node_name"] == "设计负责人审批"
    assert receipt["current_approver_names"] == ["张设计"]
    assert receipt["approval_steps"][1] == {"nodeName": "采购审批", "approverNames": ["李采购"]}
    assert receipt["approval_status"] == "PENDING"
    assert receipt["workflow_instance_id"] == "workflow-1"


def test_approval_summary_reads_erp_launch_config_process_and_first_approval_node():
    summary = bridge._approval_process_summary({
        "resolvedProcessCode": "design_stock_prepare_approval",
        "process": {"name": "备料审批"},
        "nodes": [
            {"nodeType": "start", "nodeName": "开始"},
            {
                "nodeType": "approval",
                "nodeName": "设计负责人审批",
                "resolvedAssigneeUsers": [{"userName": "designer", "nickName": "张设计"}],
            },
            {
                "nodeType": "approval",
                "nodeName": "采购审批",
                "resolvedAssigneeUsers": [{"userName": "buyer", "nickName": "李采购"}],
            },
        ],
    })

    assert summary == {
        "processCode": "design_stock_prepare_approval",
        "processName": "备料审批",
        "currentNodeName": "设计负责人审批",
        "currentApproverNames": ["张设计"],
        "approvalSteps": [
            {"nodeName": "设计负责人审批", "approverNames": ["张设计"]},
            {"nodeName": "采购审批", "approverNames": ["李采购"]},
        ],
    }


@pytest.mark.parametrize("process_code,process_name", [
    ("design_new_model_approval", "设计新模审批"),
    ("design_modify_model_approval", "设计修改模审批"),
    ("design_stock_prepare_approval", "设计备料审批"),
    ("design_new_model_attached_square_approval", "新模五金附图方料采购审批"),
])
def test_all_erp_design_flow_branches_are_passed_through(process_code, process_name):
    summary = bridge._approval_process_summary({
        "resolvedProcessCode": process_code,
        "process": {"name": process_name},
        "nodes": [{"nodeType": "approval", "nodeName": "设计负责人审批"}],
    })

    assert summary["processCode"] == process_code
    assert summary["processName"] == process_name
    assert summary["currentNodeName"] == "设计负责人审批"
    assert summary["currentApproverNames"] == []
    assert summary["approvalSteps"] == [
        {"nodeName": "设计负责人审批", "approverNames": []},
    ]


def test_design_import_confirmation_token_is_bound_and_one_time():
    engine, Session = _session()
    token = secrets.token_urlsafe(32)
    expected_date = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
    try:
        with Session.begin() as db:
            user = User(
                username="design_approval_token",
                display_name="设计审批凭证测试",
                password_hash="test",
                super_admin=True,
            )
            db.add(user)
            db.flush()
            config, _approval_data_input, detail = _approval_data(token)
            record(db, user, bridge._APPROVAL_CONFIG_ACTION, "700", detail)

        with Session() as db:
            user = db.query(User).filter_by(username="design_approval_token").one()
            data = bridge.ImportInput(
                session_id=700,
                sheet_type="steel",
                preview_rows=[{"rowIndex": 1, "qty": 1}],
                mold_code="M250238-P4",
                confirm_import=True,
                approval_token=token,
                expected_date=expected_date,
            )
            event = bridge._approval_token_detail(db, user, data, "new_model", config)
            bridge._mark_approval_consumed(event, {"request_no": "PR-1"})
            db.commit()

        with Session() as db:
            user = db.query(User).filter_by(username="design_approval_token").one()
            with pytest.raises(DomainError) as error:
                bridge._approval_token_detail(db, user, data, "new_model", config)
            assert error.value.code == "ERP_DESIGN_CONFIRMATION_REQUIRED"
    finally:
        engine.dispose()


def test_approval_config_persists_exact_browser_form_binding(monkeypatch):
    engine, Session = _session()
    expected_date = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
    try:
        with Session.begin() as db:
            user = User(
                username="design_approval_binding",
                display_name="设计审批绑定测试",
                password_hash="test",
                super_admin=True,
            )
            db.add(user)
            db.flush()
            record(db, user, bridge._PARSE_ACTION, "702", {
                "file_id": "file-1", "sheet_type": "hardware",
            })

        monkeypatch.setattr(bridge, "require", lambda *_args: None)
        monkeypatch.setattr(bridge, "call_mcp", lambda *_args: {
            "resolvedProcessCode": "design_new_model_approval",
            "processName": "设计新模审批",
            "currentNodeName": "设计主管审批",
        })

        with Session() as db:
            user = db.query(User).filter_by(username="design_approval_binding").one()
            response = bridge.approval_config(bridge.ApprovalConfigInput(
                session_id=702,
                sheet_type="hardware",
                preview_rows=[{"rowIndex": 1, "qty": 3}],
                mold_code="M250238-P4",
                design_order_type="new_model",
                expected_date=expected_date,
                remark="已核对",
            ), user=user, db=db)
            event = db.scalar(bridge.select(bridge.m.AuditEvent).where(
                bridge.m.AuditEvent.user_id == user.id,
                bridge.m.AuditEvent.action == bridge._APPROVAL_CONFIG_ACTION,
                bridge.m.AuditEvent.resource_id == "702",
            ).order_by(bridge.m.AuditEvent.created_at.desc()))

        assert response["approval"]["approvalToken"]
        assert event.detail["binding"] == {
            "session_id": 702,
            "sheet_type": "hardware",
            "preview_rows": [{"rowIndex": 1, "qty": 3}],
            "mold_code": "M250238-P4",
            "design_order_type": "new_model",
            "urgency_level": "normal",
            "expected_date": expected_date,
            "purchase_reason": None,
            "remark": "已核对",
            "allow_duplicate": False,
            "approval_config_hash": bridge._approval_hash({
                "processCode": "design_new_model_approval",
                "processName": "设计新模审批",
                "currentNodeName": "设计主管审批",
                "currentApproverNames": [],
                "approvalSteps": [],
            }),
        }
    finally:
        engine.dispose()


def test_import_timeout_queries_session_and_does_not_retry(monkeypatch):
    calls = []

    def importer():
        calls.append("import")
        raise DomainError("ERP_DESIGN_MCP_FAILED", "timeout", 504)

    def query(name, arguments):
        calls.append(name)
        return {"status": "imported", "sessionId": arguments["sessionId"]}

    monkeypatch.setattr(bridge, "call_mcp", query)
    with pytest.raises(DomainError) as error:
        bridge._import_once_or_reconcile(701, importer)

    assert error.value.code == "ERP_IMPORT_RECONCILIATION_REQUIRED"
    assert calls == ["import", "get_new_mold_upload_status"]


def test_import_receipt_is_enriched_from_existing_erp_order_detail(monkeypatch):
    monkeypatch.setattr(bridge, "call_mcp", lambda name, arguments: {
        "requestId": arguments["id"],
        "orderNo": "PR-20260930-01",
        "currentApprovalNodeName": "设计负责人审批",
        "approvalStatus": "pending",
        "workflowInstanceId": 9021,
    })

    result = bridge._enrich_import_from_order_detail({"requestId": 88})

    assert result["requestNo"] == "PR-20260930-01"
    assert result["currentNodeName"] == "设计负责人审批"
    assert result["approvalStatus"] == "pending"
    assert result["workflowInstanceId"] == 9021
