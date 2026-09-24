"""User-visible confirmation and delegated approval policy."""

# A prepared confirmation card stays executable after a protocol/budget close.
# Those runs are stored as FAILED or SUCCEEDED; CANCELLED still means the user
# stopped the work and must prepare again.
CONFIRMABLE_RUN_STATUSES = frozenset({
    "RUNNING", "RUNNING_SCOPED", "SUCCEEDED", "FAILED",
})


def proposal_run_is_open(run):
    return bool(run) and getattr(run, "status", None) in CONFIRMABLE_RUN_STATUSES


def run_agent_permission_mode(run):
    checkpoint = run.checkpoint if run and isinstance(run.checkpoint, dict) else {}
    return "delegated_auto" if checkpoint.get("agent_permission_mode") == "delegated_auto" else "ask"


def proposal_confirmation_policy(run=None, *, requires_approval=False):
    mode = run_agent_permission_mode(run)
    if requires_approval and mode == "delegated_auto":
        return {
            "agent_permission_mode": mode,
            "status": "CONFIRM_THEN_DELEGATED_APPROVAL_ALLOWED",
            "requires_human_confirmation": True,
            "requires_approval": True,
            "title": "本人确认后，授权节点可自动审批",
            "description": "本卡片仍必须由本人核对确认；确认后会提交 Agent BPM，只有流程允许且审批人本人已授权的低风险节点才可能自动同意。",
        }
    if requires_approval:
        return {
            "agent_permission_mode": mode,
            "status": "CONFIRM_THEN_MANUAL_APPROVAL",
            "requires_human_confirmation": True,
            "requires_approval": True,
            "title": "本人确认后提交审批",
            "description": "当前是每次询问模式；本卡片确认后只提交审批，不会触发 Agent 自动同意，后续审批仍按待办办理。",
        }
    return {
        "agent_permission_mode": mode,
        "status": "HUMAN_CONFIRMATION_REQUIRED",
        "requires_human_confirmation": True,
        "requires_approval": False,
        "title": "必须本人确认",
        "description": "当前只是操作建议，不会由 Agent 自动执行；点击核对并确认后才会保存记录或办理操作。",
    }


def agent_permission_mode_from_proposal(proposal):
    policy = proposal.get("confirmation_policy") if isinstance(proposal, dict) else None
    if isinstance(policy, dict) and policy.get("agent_permission_mode") == "delegated_auto":
        return "delegated_auto"
    return "ask"
