"""Resume an Agent run after a trusted human decision on a prepared proposal."""
import json

from .config import model_settings, settings
from .errors import DomainError
from .models import Run, Step
from agent_core.run_status import RUNNING_STATUSES, SCOPED_QUEUED


def _final_snapshot(result):
    if not isinstance(result, dict):
        return None
    snapshot = {key: result.get(key) for key in (
        "response_kind", "summary", "message", "suggestions", "error_code"
    ) if result.get(key) is not None}
    return snapshot or None


def _assistant_history_message(snapshot):
    """Project the saved terminal result back into the resumed model transcript."""
    if not snapshot:
        return None
    summary = snapshot.get("summary") or snapshot.get("message")
    if not isinstance(summary, str) or not summary.strip():
        return None
    suggestions = snapshot.get("suggestions") or []
    suffix = "" if not suggestions else "\n" + "\n".join(f"- {item}" for item in suggestions)
    return {"role": "assistant", "content": summary.strip() + suffix}


def queue_after_proposal_decision(db, user, step_id, decision, receipt=None):
    """Persist the decision and queue the same run for a natural Agent follow-up."""
    if decision not in {"approved", "dismissed"}:
        raise ValueError("unsupported proposal decision")
    step = db.get(Step, step_id)
    run = db.get(Run, step.run_id) if step else None
    if not step or not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    # A proposal can become visible in the persisted trace while the harness is
    # still completing the same run. Do not invalidate that worker lease.
    if run.status in RUNNING_STATUSES:
        return False

    checkpoint = dict(run.checkpoint or {})
    decisions = dict(checkpoint.get("proposal_decisions") or {})
    if step_id in decisions:
        return False
    decisions[step_id] = decision

    prior_finals = list(checkpoint.get("prior_finals") or [])
    prior = _final_snapshot(run.result)
    if prior:
        prior_finals.append(prior)

    if decision == "approved":
        fact = {
            "decision": "approved",
            "decision_message": "用户已在可信确认界面批准该操作建议",
            "proposal_step_id": step_id,
            "authoritative_receipt": receipt,
        }
        instruction = (
            "这是已完成的可信人工确认及权威执行回执。请依据回执自然回应用户，"
            "准确区分已执行、已提交审批和最终生效；当前不再等待批准，不得再次调用工具。"
        )
    else:
        fact = {
            "decision": "dismissed",
            "decision_message": "用户已在可信确认界面选择暂不执行该操作建议",
            "proposal_step_id": step_id,
        }
        instruction = "请根据这一用户决定自然回应；当前不再等待批准，不得调用工具。"

    messages = list(checkpoint.get("messages") or [])
    prior_message = _assistant_history_message(prior)
    if prior_message:
        messages.append(prior_message)
    messages.append({
        "role": "user",
        "content": (
            "我已在可信确认界面完成本人确认，请根据执行回执继续回复。"
            if decision == "approved"
            else "我已在可信确认界面选择暂不执行，请继续回复。"
        ),
    })
    receipt_call_id = "proposal_resolution_" + step_id
    messages.append({
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": receipt_call_id,
            "type": "function",
            "function": {
                "name": "ProposalResolution",
                "arguments": json.dumps({
                    "proposal_step_id": step_id,
                    "decision": decision,
                }, ensure_ascii=False),
            },
        }],
    })
    messages.append({
        "role": "tool",
        "tool_call_id": receipt_call_id,
        "content": json.dumps({
            "source": "trusted_host",
            "event": "proposal_resolved",
            **fact,
        }, ensure_ascii=False),
    })
    messages.append({
        "role": "system",
        "content": (
            instruction
            + f" 最终 JSON 必须包含 proposal_decision={json.dumps(decision)}，"
              "用于证明已消费前面的 ProposalResolution 权威回执。"
        ),
    })
    checkpoint.pop("completed_at", None)
    checkpoint.update({
        "messages": messages,
        "proposal_decisions": decisions,
        "proposal_resolution": fact,
        "prior_finals": prior_finals,
        "pending": [],
        "pending_index": 0,
        "finalizing": True,
        "deadline": None,
        "phase": "PREPARING",
        "model_started_at": None,
        "protocol_repairs": 0,
        "next_model_instructions": [],
        "streaming_model_message": None,
        "last_model_message": None,
    })
    run.checkpoint = checkpoint
    run.result = None
    run.lease_until = None
    if model_settings().llm_enabled:
        checkpoint["worker_scope"] = settings().worker_scope
        run.checkpoint = checkpoint
        run.status = SCOPED_QUEUED
    else:
        run.status = "WAITING_CONFIGURATION"
    return True
