from types import SimpleNamespace

from agent_core.context_budget import HISTORICAL_ASSISTANT_PREFIX
from app.api import run_trace


def test_run_trace_omits_historical_assistant_context():
    run = SimpleNamespace(
        status="RUNNING",
        checkpoint={
            "messages": [
                {
                    "role": "assistant",
                    "content": HISTORICAL_ASSISTANT_PREFIX + '{"summary":"模具还有192个零件未委外"}',
                },
                {"role": "assistant", "content": "正在按批次查询未委外零件"},
            ]
        },
        result=None,
    )
    texts = [item.get("text") for item in run_trace(run, []) if item.get("type") == "message"]
    assert texts == ["正在按批次查询未委外零件"]


def test_run_trace_rewrites_tool_forbidden_into_spoken_close():
    run = SimpleNamespace(
        prompt="有需要我处理的待办任务吗",
        status="FAILED",
        checkpoint={
            "messages": [{
                "role": "tool",
                "content": '{"model_context":{"summary":"待接单 1 条","item_count":1,'
                           '"items":[{"orderNo":"EO-260924-IT01","station":"待接单"}]}}',
            }],
            "evidence_ids": ["e1"],
            "attempted_tools": ["query_erp_outsource_processor_board"],
        },
        result={"message": "任务执行未完成，可以核对配置和执行记录后重试", "error_code": "TOOL_FORBIDDEN"},
    )
    finals = [item for item in run_trace(run, []) if item.get("type") == "final"]
    assert len(finals) == 1
    assert finals[0].get("error_code") in {None, ""}
    assert "EO-260924-IT01" in (finals[0].get("summary") or "")
    assert "接单" in (finals[0].get("summary") or "")
    assert finals[0].get("suggestions") == ["接单", "拒单"]


def test_run_trace_rewrites_budget_close_to_card_when_proposal_exists():
    run = SimpleNamespace(
        prompt="EO-260924-3SC0：这个确认接单吧",
        status="FAILED",
        checkpoint={"messages": [], "evidence_ids": ["step-accept"]},
        result={
            "message": "任务执行未完成，可以核对配置和执行记录后重试",
            "error_code": "BUDGET_EXCEEDED",
            "evidence": [{
                "id": "step-accept",
                "tool": "prepare_erp_outsource_processor_accept",
                "proposal": {"kind": "erp_outsource_processor_accept", "title": "接单"},
            }],
        },
    )
    step = SimpleNamespace(
        id="step-accept",
        tool="prepare_erp_outsource_processor_accept",
        result={"proposal": {"kind": "erp_outsource_processor_accept", "title": "接单"}},
    )
    finals = [item for item in run_trace(run, [step]) if item.get("type") == "final"]
    assert len(finals) == 1
    assert finals[0].get("error_code") in {None, ""}
    assert finals[0].get("response_kind") == "AWAITING_APPROVAL"
    assert "确认卡" in (finals[0].get("summary") or "")
