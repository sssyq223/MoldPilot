from types import SimpleNamespace

from agent_core.run_status import composition_retry_checkpoint
from app.internal import _completed_evidence_ids, _is_composition_failure


def test_terminal_model_failure_is_separate_only_after_required_evidence_completed():
    evidence = [SimpleNamespace(id="evidence-1")]
    checkpoint = {
        "finalizing": True,
        "pending": [],
        "evidence_ids": ["evidence-1"],
        "required_evidence_tools": ["query_business_state"],
        "evidence_tools": ["query_business_state"],
    }

    evidence_ids = _completed_evidence_ids(checkpoint, evidence)

    assert evidence_ids == ["evidence-1"]
    assert _is_composition_failure(
        checkpoint, "MODEL_READ_TIMEOUT", evidence_ids
    ) is True
    assert _is_composition_failure(
        {**checkpoint, "finalizing": False}, "MODEL_READ_TIMEOUT", evidence_ids
    ) is False
    assert _is_composition_failure(
        {**checkpoint, "evidence_tools": []}, "MODEL_READ_TIMEOUT", evidence_ids
    ) is False
    assert _is_composition_failure(
        checkpoint, "TOOL_EXECUTION_FAILED", evidence_ids
    ) is False


def test_composition_retry_preserves_tool_state_and_clears_only_terminal_attempt_state():
    checkpoint = {
        "turn": 2,
        "finalizing": True,
        "completed_at": "2026-09-30T13:42:39+08:00",
        "evidence_ids": ["evidence-1", "evidence-2"],
        "executed_tool_signatures": ["query:signature"],
        "messages": [{"role": "tool", "content": "{}"}],
        "model_metrics": {"total_ms": 122617, "retry_count": 1},
        "protocol_repairs": 2,
    }
    result = {"error_code": "MODEL_READ_TIMEOUT", "composition_failed": True}

    retried = composition_retry_checkpoint(checkpoint, result, "desktop:worker")

    assert retried["phase"] == "COMPOSITION_RETRY_QUEUED"
    assert retried["composition_attempt"] == 1
    assert retried["protocol_repairs"] == 0
    assert retried["evidence_ids"] == checkpoint["evidence_ids"]
    assert retried["executed_tool_signatures"] == checkpoint["executed_tool_signatures"]
    assert retried["messages"] == checkpoint["messages"]
    assert "completed_at" not in retried
    assert retried["composition_errors"] == [{
        "error_code": "MODEL_READ_TIMEOUT",
        "model_metrics": checkpoint["model_metrics"],
        "failed_at": checkpoint["completed_at"],
    }]
