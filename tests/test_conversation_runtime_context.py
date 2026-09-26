"""续办的历史上下文与工具发现回归，不把历史记录当成本轮授权。"""
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from app import api, internal, models as m
from app.authorization import fingerprint
from app.agent_worker import Gateway as WorkerGateway
from app.config import settings
from app.db import now
from app.tool_gateway import execute
from agent_core.harness import run_loop
from conftest import sign_in
from test_agent_api import worker_headers
from test_document_intake_tools import _confirm, _run, _legacy_file
from test_files import PDF, upload
from test_model_harness import Gateway, TranscriptModel, context


FOLLOWUP = "查询刚才上传文件的预分类结果，并准备文件类型确认建议。"


@pytest.fixture
def confirmed_history(client, data, monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "file_backend", "local")
    monkeypatch.setattr(settings(), "file_local_root", str(tmp_path / "objects"))
    monkeypatch.setattr(api, "model_settings", lambda: SimpleNamespace(llm_enabled=True))
    monkeypatch.setattr(internal, "model_settings", lambda: SimpleNamespace(llm_enabled=True))
    sign_in(client)
    blob = _legacy_file(data, PDF, "续办合同.pdf")
    ids, factory = data
    prior_id = _run(factory, ids, blob["conversation_id"], [blob["id"]])
    with factory.begin() as db:
        user = db.get(m.User, ids["admin"])
        prior = db.get(m.Run, prior_id)
        prior.created_at = now() - timedelta(minutes=1)
        evidence = execute(db, user, "prepare_document_intake", {"file_ids": [blob["id"]]}, run=prior)
        receipt = _confirm(db, user, prior, "prepare_document_intake", evidence)
        prior.result = {"response_kind": "BUSINESS", "summary": "已创建接收批次，等待预分类。"}
    return {"run_id": prior_id, "blob": blob, "receipt": receipt}


def claim_followup(client, factory, cid, checkpoint=None):
    response = client.post("/api/runs", json={"conversation_id": cid, "prompt": FOLLOWUP})
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]
    if checkpoint is not None:
        with factory.begin() as db:
            run = db.get(m.Run, run_id)
            run.checkpoint = {**run.checkpoint, **checkpoint}
    response = client.post("/internal/runs/claim", headers=worker_headers())
    assert response.status_code == 200, response.text
    claimed = response.json()["run"]
    assert claimed["id"] == run_id
    return claimed


def test_first_chat_after_automatic_upload_has_file_references_without_fake_history(client, data, monkeypatch, tmp_path):
    monkeypatch.setattr(settings(),'file_backend','local')
    monkeypatch.setattr(settings(),'file_local_root',str(tmp_path/'files'))
    monkeypatch.setattr(api,'model_settings',lambda:SimpleNamespace(llm_enabled=True))
    monkeypatch.setattr(internal,'model_settings',lambda:SimpleNamespace(llm_enabled=True))
    sign_in(client)
    blob=upload(client,PDF,'自动识别合同.pdf').json()
    claimed=claim_followup(client,data[1],blob['conversation_id'],{'conversation_files':[{'id':'forged'}]})
    assert claimed['conversation_history']==[]
    assert claimed['files']==[]
    assert [f['id'] for f in claimed['conversation_files']]==[blob['id']]
    with data[1]() as db:
        assert db.scalar(select(func.count()).select_from(m.Run))==1


def test_claim_supplies_history_files_and_confirmed_receipt_references(client, data, confirmed_history):
    claimed = claim_followup(client, data[1], confirmed_history["blob"]["conversation_id"], {
        "conversation_history": [{"run_id": "forged-history", "request": "不可信的旧 checkpoint"}],
    })
    assert claimed["files"] == []  # 历史文件不能冒充本次上传，避免重复接收。
    history = claimed["conversation_history"]
    assert [entry["run_id"] for entry in history] == [confirmed_history["run_id"]]
    entry = history[0]
    assert entry["files"][0]["id"] == confirmed_history["blob"]["id"]
    assert entry["assistant_summary"] == "已创建接收批次，等待预分类。"
    assert entry["confirmed_actions"][0]["references"]["document_intake_id"] == confirmed_history["receipt"]["document_intake_id"]
    serialized = json.dumps(history, ensure_ascii=False)
    assert "challenge" not in serialized
    assert "payload_hash" not in serialized
    assert "forged-history" not in serialized
    assert claimed["run_trigger"] == "USER"
    assert "proposal_resolution" not in claimed


@pytest.mark.parametrize("invalid_scope", ["other_conversation", "other_user", "revoked_version", "revoked_scope"])
def test_claim_does_not_leak_unreadable_history(client, data, confirmed_history, invalid_scope):
    ids, factory = data
    cid = confirmed_history["blob"]["conversation_id"]
    with factory.begin() as db:
        prior = db.get(m.Run, confirmed_history["run_id"])
        if invalid_scope == "other_conversation":
            conversation = m.Conversation(user_id=ids["admin"], title="另一会话")
            db.add(conversation)
            db.flush()
            cid = conversation.id
        elif invalid_scope == "other_user":
            prior.user_id = ids["buyer"]
        elif invalid_scope == "revoked_version":
            prior.security_version -= 1
        else:
            prior.checkpoint = {**prior.checkpoint, "authorization_hash": "outdated-scope"}
    claimed = claim_followup(client, factory, cid)
    assert claimed["conversation_history"] == []
    assert confirmed_history["blob"]["id"] not in json.dumps(claimed["conversation_history"])


def test_claim_history_is_bounded_and_ordered(client, data, confirmed_history):
    ids, factory = data
    with factory.begin() as db:
        user = db.get(m.User, ids["admin"])
        for index in range(12):
            db.add(m.Run(
                user_id=user.id, conversation_id=confirmed_history["blob"]["conversation_id"],
                security_version=user.security_version, prompt=f"近期请求{index}", status="SUCCEEDED",
                created_at=now() - timedelta(seconds=40-index),
                checkpoint={"authorization_hash": fingerprint(db, user)},
                result={"summary": "合成历史答复" * 1000},
            ))
    claimed = claim_followup(client, factory, confirmed_history["blob"]["conversation_id"])
    history = claimed["conversation_history"]
    assert 0 < len(history) <= 8
    assert len(json.dumps(history, ensure_ascii=False)) <= 12000
    assert history[-1]["request"] == "近期请求11"
    assert [row["created_at"] for row in history] == sorted(row["created_at"] for row in history)


def test_followup_query_and_type_proposal_roundtrip_through_real_mcp(client, data, confirmed_history):
    factory = data[1]
    intake_id = confirmed_history["receipt"]["document_intake_id"]
    with factory.begin() as db:
        intake = db.get(m.DocumentIntake, intake_id)
        intake.status = "AWAITING_TYPE_CONFIRMATION"
        intake.row_version = 2
        intake_file = db.scalar(select(m.DocumentIntakeFile).where(m.DocumentIntakeFile.intake_id == intake_id))
        intake_file.suggested_type = "SALES_CONTRACT"
        intake_file.suggested_confidence = Decimal("0.98")
        intake_file_id = intake_file.id
    claimed = claim_followup(client, factory, confirmed_history["blob"]["conversation_id"])

    class WorkerTransport:
        def post(self, path, **kwargs):
            kwargs["headers"] = {**worker_headers(), **kwargs.get("headers", {})}
            return client.post(path, **kwargs)

    gateway = WorkerGateway(WorkerTransport(), claimed)
    assert any(t["function"]["name"] == "query_document_intake" for t in gateway.discover())
    queried = gateway.execute(0, "query_document_intake", {})
    assert queried["data"][0]["id"] == intake_id
    assert queried["data"][0]["status"] == "AWAITING_TYPE_CONFIRMATION"
    assert isinstance(queried["data"][0]["created_at"], str)
    queried_by_id = gateway.execute(1, "query_document_intake", {"document_intake_id": intake_id})
    assert queried_by_id["data"][0]["id"] == intake_id
    prepared = gateway.execute(2, "prepare_document_type_confirmation", {
        "document_intake_id": intake_id, "expected_version": 2,
        "files": [{"intake_file_id": intake_file_id, "document_type": "SALES_CONTRACT", "contract_group_key": "contract-1"}],
    })
    assert prepared["proposal"]["confirmation_policy"]["requires_human_confirmation"] is True
    review = client.post(f"/api/proposals/{prepared['evidence_id']}/intent")
    assert review.status_code == 200, review.text
    with factory() as db:
        saved = db.get(m.Step, queried["evidence_id"])
        assert isinstance(saved.result["data"][0]["created_at"], str)
        assert db.get(m.DocumentIntake, intake_id).status == "AWAITING_TYPE_CONFIRMATION"
        assert db.scalar(select(func.count()).select_from(m.DocumentOcrJob).where(m.DocumentOcrJob.phase == "FULL_CONTRACT")) == 0


def test_followup_can_discover_tools_and_receives_historical_references_without_forced_calls():
    history = [{
        "run_id": "prior-run", "request": "处理本次上传附件",
        "files": [{"id": "prior-file", "filename": "续办合同.pdf", "media_type": "application/pdf"}],
        "assistant_summary": "已接收文件，等待预分类。",
        "confirmed_actions": [{"tool": "prepare_document_intake", "references": {
            "document_intake_id": "prior-intake", "status": "PRECLASSIFYING",
        }}],
    }]
    tool = {"type": "function", "function": {"name": "query_document_intake", "description": "查询文档接收状态"}}
    model = TranscriptModel([{"role": "assistant", "content": json.dumps({
        "response_kind": "CLARIFICATION", "summary": "请核对本次办理目标。", "evidence_ids": [], "suggestions": [],
    }, ensure_ascii=False)}])
    gateway = Gateway()
    run_loop(context(prompt=FOLLOWUP, core_tool_names=[], tools=[tool], skills=[],
                     run_trigger="USER", files=[], conversation_history=history), model, gateway)
    assert model.tool_names[0] == ["ToolSearch"]
    user_text = "\n".join(msg["content"] for msg in model.transcripts[0] if msg["role"] == "user")
    assert "prior-file" in user_text and "prior-intake" in user_text
    assert user_text.index("prior-file") < user_text.index(FOLLOWUP)
    assert gateway.physical_calls == 0
    assert gateway.saved["evidence_ids"] == []
    assert gateway.saved["activated_skill_keys"] == []


def test_followup_can_discover_preparation_tool_without_keyword_authorization():
    tool = {"type": "function", "function": {"name": "prepare_document_type_confirmation", "description": "准备文件类型确认建议"}}
    model = TranscriptModel([
        {"role": "assistant", "tool_calls": [{"id": "search-prepare", "type": "function", "function": {
            "name": "ToolSearch", "arguments": json.dumps({"query": "prepare_document_type_confirmation"}),
        }}]},
        {"role": "assistant", "tool_calls": [{"id": "prepare-type", "type": "function", "function": {
            "name": "prepare_document_type_confirmation", "arguments": "{}",
        }}]},
        {"role": "assistant", "content": json.dumps({"response_kind": "AWAITING_APPROVAL", "summary": "请核对建议。", "evidence_ids": ["type-proposal"], "suggestions": []})},
    ])
    class ProposalGateway(Gateway):
        def execute(self, sequence, key, arguments):
            self.physical_calls += 1
            return {"evidence_id": "type-proposal", "proposal": {"action": "document_type_confirmation", "display": {"类型": "销售合同"}}}
    gateway = ProposalGateway()
    result = run_loop(context(prompt=FOLLOWUP, core_tool_names=[], tools=[tool], skills=[], run_trigger="USER"), model, gateway)
    assert "prepare_document_type_confirmation" in model.tool_names[1]
    assert gateway.physical_calls == 1
    assert result["response_kind"] == "AWAITING_APPROVAL"


def test_recovery_keeps_model_selected_tools_for_keywordless_followup():
    tool = {"type": "function", "function": {"name": "query_document_intake", "description": "查询接收状态"}}
    model = TranscriptModel([
        {"role": "assistant", "tool_calls": [{"id": "resume-query", "type": "function", "function": {
            "name": "query_document_intake", "arguments": "{}",
        }}]},
        {"role": "assistant", "content": json.dumps({"response_kind": "BUSINESS", "summary": "已查询。", "evidence_ids": ["e1"], "suggestions": []})},
    ])
    gateway = Gateway()
    run_loop(context(prompt=FOLLOWUP, core_tool_names=[], tools=[tool], skills=[], run_trigger="USER",
                     active_tool_names=["query_document_intake"]), model, gateway)
    assert "query_document_intake" in model.tool_names[0]
    assert gateway.physical_calls == 1


def test_model_can_search_and_query_for_followup_without_business_keyword_gate():
    tool = {"type": "function", "function": {"name": "query_document_intake", "description": "查询当前会话预分类状态"}}
    def call(name, arguments, call_id):
        return {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments),
        }}]}
    model = TranscriptModel([
        call("ToolSearch", {"query": "query_document_intake"}, "search-1"),
        call("query_document_intake", {}, "query-1"),
        {"role": "assistant", "content": json.dumps({"response_kind": "BUSINESS", "summary": "已查询当前状态。", "evidence_ids": ["e1"], "suggestions": []})},
    ])
    gateway = Gateway()
    run_loop(context(prompt=FOLLOWUP, core_tool_names=[], tools=[tool], skills=[], run_trigger="USER"), model, gateway)
    assert model.tool_names[0] == ["ToolSearch"]
    assert "query_document_intake" in model.tool_names[1]
    assert gateway.physical_calls == 1
    assert gateway.final["evidence_ids"] == ["e1"]
