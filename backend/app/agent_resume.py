"""Resume an Agent run after a trusted human decision on a prepared proposal."""
import json

from .config import model_settings
from .errors import DomainError
from .models import Run, Step


def _final_snapshot(result):
    if not isinstance(result, dict):
        return None
    snapshot = {key: result.get(key) for key in (
        "response_kind", "summary", "message", "suggestions", "error_code"
    ) if result.get(key) is not None}
    return snapshot or None


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
    if run.status == "RUNNING":
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
            "decision": "用户已在可信确认界面批准该操作建议",
            "proposal_step_id": step_id,
            "authoritative_receipt": receipt,
        }
        instruction = (
            "这是已完成的可信人工确认及权威执行回执。请依据回执自然回应用户，"
            "准确区分已执行、已提交审批和最终生效；当前不再等待批准，不得再次调用工具。"
        )
    else:
        fact = {
            "decision": "用户已在可信确认界面选择暂不执行该操作建议",
            "proposal_step_id": step_id,
        }
        instruction = "请根据这一用户决定自然回应；当前不再等待批准，不得调用工具。"

    messages = list(checkpoint.get("messages") or [])
    messages.append({
        "role": "system",
        "content": instruction + "\n可信人工决定：" + json.dumps(fact, ensure_ascii=False),
    })
    checkpoint.update({
        "messages": messages,
        "proposal_decisions": decisions,
        "prior_finals": prior_finals,
        "pending": [],
        "pending_index": 0,
        "finalizing": True,
        "deadline": None,
        "phase": "PREPARING",
        "model_started_at": None,
    })
    run.checkpoint = checkpoint
    run.result = None
    run.lease_until = None
    run.status = "QUEUED" if model_settings().llm_enabled else "WAITING_CONFIGURATION"
    return True
