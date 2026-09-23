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
