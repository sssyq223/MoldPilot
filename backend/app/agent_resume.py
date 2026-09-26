"""Resume an Agent run after a trusted human decision on a prepared proposal."""
import json

from .config import model_settings
from .errors import DomainError
from .models import Run, Step


# 文档工程联络链在“创建/关联原件”确认后还需生成下一张 Proposal；
# 其他业务 Proposal 仍只收口为确认回执，避免意外重复调用工具。
_CONTACT_DOCUMENT_CONTINUATIONS = {
    "prepare_contact_create": "attach",
    "prepare_contact_attach": "task",
}


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

    continuation = decision == "approved" and step.tool in _CONTACT_DOCUMENT_CONTINUATIONS
    if decision == "approved":
        fact = {
            "decision": "approved",
            "decision_message": "用户已在可信确认界面批准该操作建议",
            "proposal_step_id": step_id,
            "authoritative_receipt": receipt,
        }
        if step.tool == "prepare_contact_create":
            instruction = (
                "这是已完成的可信人工确认及权威执行回执。工程联络单已创建；"
                "现在继续工程联络文档 Skill：先查询新联络单当前版本，再仅准备原始附件关联 Proposal。"
                "不得重复调用 prepare_contact_create，附件关联仍须等待本人另行确认。"
            )
        elif step.tool == "prepare_contact_attach":
            instruction = (
                "这是已完成的可信人工确认及权威执行回执。原始附件已关联；"
                "现在继续工程联络文档 Skill：查询联络单当前版本，若办理模式为 ONLINE，"
                "仅准备责任事项 Proposal。不得重复调用 prepare_contact_attach，责任事项仍须等待本人另行确认。"
            )
        else:
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
        # 每次本人确认后的受控续办都是新的模型阶段；历史证据与已执行签名仍保留，
        # 但不能让上一阶段消耗掉下一张 Proposal 所需的模型轮次预算。工具计数同时
        # 是 Step.sequence 的全局幂等序号，必须继续累计，不能重置。
        "turn": 0,
        "proposal_decisions": decisions,
        "proposal_resolution": fact,
        "prior_finals": prior_finals,
        "pending": [],
        "pending_index": 0,
        "post_proposal_continuation": continuation,
        "finalizing": not continuation,
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
    # 已绑定模型的任务由 claim 校验自身配置，不受全局默认模型变化影响。
    run.status = "QUEUED" if checkpoint.get('model_selection') or model_settings().llm_enabled else "WAITING_CONFIGURATION"
    return True
