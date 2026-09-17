from agent_core.domain_pack import active_pack_name, component
from agent_core import tool_gateway as core_gateway
from app import tool_gateway as host_gateway


def test_product_selects_installed_business_pack_and_core_uses_its_contract():
    assert active_pack_name() == "mold"
    policy = component("harness_policy")
    handlers = component("proposal_handlers")
    assert policy.SYSTEM_PROMPT
    assert core_gateway.TOOLS is host_gateway.TOOLS
    assert core_gateway.SKILLS is host_gateway.SKILLS
    prepared = {name for name in core_gateway.TOOLS if name.startswith("prepare_")}
    handled = {name for handler in handlers.HANDLERS for name in handler.tools}
    assert prepared == handled


def test_agent_core_source_does_not_embed_mold_business_policy():
    from pathlib import Path
    import agent_core

    root = Path(agent_core.__file__).parent
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.glob("*.py")
    )
    for business_term in ("工程联络", "模具工作台", "prepare_project_pause"):
        assert business_term not in runtime_source
