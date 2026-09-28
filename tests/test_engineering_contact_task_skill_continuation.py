from types import SimpleNamespace

from app.agent_resume import queue_after_proposal_decision
from app.models import Run, Step


def _resume(step_tool):
    run = Run(
        id="contact-form-resume-run", conversation_id="conversation", user_id="user-1", security_version=1,
        prompt="处理工程联络单", status="SUCCEEDED",
        checkpoint={'messages': [], 'turn': 11, 'tool_count': 8, 'evidence_ids': [],
                    'executed_tool_signatures': [], 'completed_at': '2026-09-27T10:00:00+08:00'},
        result={'response_kind': 'AWAITING_APPROVAL', 'summary': '请确认工程联络操作。'},
    )
    step = Step(id='contact-form-step', run_id=run.id, sequence=0,
                tool=step_tool, request_hash='hash', result={})

    class FakeDb:
        def get(self, model, identity):
            if model is Step and identity == step.id:
                return step
            if model is Run and identity == run.id:
                return run
            return None

    return run, step, FakeDb()


def test_create_confirmation_deterministically_requests_attachment_tool(monkeypatch):
    run, step, db = _resume('prepare_contact_create')
    monkeypatch.setattr('app.agent_resume.model_settings', lambda: SimpleNamespace(llm_enabled=True))

    assert queue_after_proposal_decision(
        db, SimpleNamespace(id='user-1'), step.id, 'approved', {'status': 'CONFIRMED'},
    ) is True

    assert run.checkpoint['continuation_tool'] == 'prepare_contact_attach'
    assert run.checkpoint['post_proposal_continuation'] is True


def test_attach_confirmation_continues_with_form_tasks_tool(monkeypatch):
    run, step, db = _resume('prepare_contact_attach')
    monkeypatch.setattr('app.agent_resume.model_settings', lambda: SimpleNamespace(llm_enabled=True))

    assert queue_after_proposal_decision(
        db, SimpleNamespace(id='user-1'), step.id, 'approved', {'status': 'CONFIRMED'},
    ) is True

    instruction = run.checkpoint['messages'][-1]['content']
    assert run.checkpoint['post_proposal_continuation'] is True
    assert run.checkpoint['continuation_tool'] == 'prepare_contact_form_tasks'
    assert run.checkpoint['next_model_instructions'] == []
    assert 'prepare_contact_form_tasks' in instruction
    assert '仅准备责任事项 Proposal' not in instruction


def test_attach_confirmation_history_branch_is_explicit(monkeypatch):
    run, step, db = _resume('prepare_contact_attach')
    monkeypatch.setattr('app.agent_resume.model_settings', lambda: SimpleNamespace(llm_enabled=True))

    queue_after_proposal_decision(
        db, SimpleNamespace(id='user-1'), step.id, 'approved', {'status': 'CONFIRMED'},
    )

    instruction = run.checkpoint['messages'][-1]['content']
    assert 'HISTORY' in instruction
    assert '只允许补录线下事实' in instruction
