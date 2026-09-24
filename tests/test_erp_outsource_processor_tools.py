from types import SimpleNamespace

import pytest

from agent_core.confirmation_policy import proposal_run_is_open
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_processor_tools as tools


class _Db:
    def __init__(self, step, run):
        self.step = step
        self.run = run

    def get(self, model, key):
        if key == self.step.id:
            return self.step
        if key == self.run.id:
            return self.run
        return None


def test_proposal_run_is_open_keeps_failed_cards_confirmable():
    assert proposal_run_is_open(SimpleNamespace(status="FAILED"))
    assert proposal_run_is_open(SimpleNamespace(status="SUCCEEDED"))
    assert not proposal_run_is_open(SimpleNamespace(status="CANCELLED"))
    assert not proposal_run_is_open(None)


def test_processor_source_allows_failed_run_with_unused_proposal(monkeypatch):
    user = SimpleNamespace(id="u1", security_version=1)
    run = SimpleNamespace(
        id="r1",
        user_id="u1",
        status="FAILED",
        security_version=1,
        checkpoint={"authorization_hash": "h"},
    )
    step = SimpleNamespace(
        id="s1",
        run_id="r1",
        tool=tools.ACCEPT_TOOL,
        result={"proposal": {"kind": "erp_outsource_processor_accept"}},
    )
    monkeypatch.setattr(tools, "fingerprint", lambda db, current: "h")
    monkeypatch.setattr(
        "domain_packs.mold.tool_gateway.available_tools",
        lambda db, current: {tools.ACCEPT_TOOL},
    )

    proposal = tools.source(_Db(step, run), user, "s1")
    assert proposal["kind"] == "erp_outsource_processor_accept"

    run.status = "CANCELLED"
    with pytest.raises(DomainError) as error:
        tools.source(_Db(step, run), user, "s1")
    assert error.value.code == "PROPOSAL_STOPPED"
