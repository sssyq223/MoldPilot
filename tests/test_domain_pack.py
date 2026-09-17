import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from agent_core.domain_pack import active_pack_name, component, manifest, validate_public_metadata
from agent_core.host_ports import HostPorts, host_ports
from agent_core import tool_gateway as core_gateway
from app import tool_gateway as host_gateway


def test_product_selects_installed_business_pack_and_core_uses_its_contract():
    assert active_pack_name() == "mold"
    policy = component("harness_policy")
    handlers = component("proposal_handlers")
    product = manifest()
    assert policy.SYSTEM_PROMPT
    assert product.PUBLIC_METADATA["id"] == "mold"
    assert product.PUBLIC_METADATA["product_name"] == "MoldPilot"
    assert product.PUBLIC_METADATA["proposal_presentation"]["value_names"]["supplier_design"] == "供应商设计"
    assert product.PUBLIC_METADATA["proposal_presentation"]["detail_links"]["contact"] == {
        "target": "contacts",
        "receipt_field": "case_id",
        "label": "查看材料",
    }
    assert callable(product.install)
    assert core_gateway.TOOLS is host_gateway.TOOLS
    assert core_gateway.SKILLS is host_gateway.SKILLS
    prepared = {name for name in core_gateway.TOOLS if name.startswith("prepare_")}
    handled = {name for handler in handlers.HANDLERS for name in handler.tools}
    assert prepared == handled
    for key in ('project_plan_change', 'project_pause_resume', 'project_termination_closure'):
        skill = core_gateway.SKILLS[key]
        assert all(name.startswith('query_') for name in skill['tools'])
        assert any(name.startswith('prepare_') for name in skill['optional_tools'])


def test_agent_core_source_does_not_embed_mold_business_policy():
    from pathlib import Path
    import agent_core

    root = Path(agent_core.__file__).parent
    runtime_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.glob("*.py")
    )
    for business_term in (
        "工程联络", "模具工作台", "项目计划", "项目号", "合同号", "当前有权项目",
        "prepare_project_pause",
    ):
        assert business_term not in runtime_source


def test_delivery_logistics_implementation_lives_in_mold_pack_not_host():
    project_root = Path(__file__).resolve().parents[1]
    handlers = component("proposal_handlers")
    logistics = handlers.handler_for_tool("prepare_logistics_route")

    assert logistics is not None
    assert logistics.module == "domain_packs.mold.delivery_logistics"
    assert logistics.implementation().__name__ == logistics.module

    host_facade = (project_root / "backend" / "app" / "delivery_logistics_tools.py").read_text(encoding="utf-8")
    pack_source = (project_root / "backend" / "domain_packs" / "mold" / "delivery_logistics.py").read_text(encoding="utf-8")
    legacy_ports_source = (project_root / "backend" / "domain_packs" / "mold" / "legacy_read_ports.py").read_text(encoding="utf-8")
    gateway_source = (project_root / "backend" / "domain_packs" / "mold" / "tool_gateway.py").read_text(encoding="utf-8")
    plan_source = (project_root / "backend" / "app" / "plan_tools.py").read_text(encoding="utf-8")

    assert "class LogisticsRouteProposalInput" not in host_facade
    assert "domain_packs.mold.delivery_logistics" in host_facade
    assert "class LogisticsRouteProposalInput" in pack_source
    assert "from app" not in pack_source
    assert "from app.domains" in legacy_ports_source
    assert "app.delivery_logistics_tools" not in gateway_source
    assert "from .delivery_logistics" in gateway_source
    assert "from app.plan_tools import ProjectPlanContextInput" not in gateway_source
    assert "domain_packs.mold" not in plan_source


def test_domain_pack_uses_validated_host_port_contract():
    ports = host_ports()

    assert isinstance(ports, HostPorts)
    assert ports.models.__name__ == "app.models"
    for name in (
        "access", "fingerprint", "predicate", "require", "select_fields",
        "content_hash", "proposal_confirmation_policy", "settings", "now",
    ):
        assert callable(getattr(ports, name))
    assert component("contracts").ProjectPlanContextInput.__module__ == "domain_packs.mold.contracts"


def test_product_metadata_rejects_incomplete_proposal_detail_link():
    with pytest.raises(RuntimeError, match="target, receipt_field and label"):
        validate_public_metadata({
            "id": "broken",
            "product_name": "Broken",
            "proposal_presentation": {
                "detail_links": {"case": {"receipt_field": "case_id", "label": "Open"}},
            },
        }, "broken")

    with pytest.raises(RuntimeError, match="installed workspace tab"):
        validate_public_metadata({
            "id": "broken",
            "product_name": "Broken",
            "workspace_tabs": [],
            "proposal_presentation": {
                "detail_links": {
                    "case": {"target": "cases", "receipt_field": "case_id", "label": "Open"},
                },
            },
        }, "broken")


def test_generic_proposal_card_has_no_mold_dictionary_or_contact_routing():
    project_root = Path(__file__).resolve().parents[1]
    source = (project_root / "web" / "src" / "components" / "ProposalCard.vue").read_text(encoding="utf-8")

    assert "../uiText" not in source
    assert "ContactProposal" not in source
    assert "contacts" not in source
    assert "detailLink.target" in source


def test_template_pack_boots_host_without_registering_mold_http_surface():
    project_root = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "PYTHONPATH": str(project_root / "backend"),
        "AGENT_BUSINESS_PACK": "template",
    }
    script = """
import json
from app.api import app
from app.erp_adapter import ERPClient
from agent_core.harness import _tool_search_schema, permission_mode_instruction
from agent_core.ollama_adapter import REACT_GUIDANCE
from agent_core.domain_pack import manifest
paths = {route.path for route in app.routes if hasattr(route, 'path')}
print(json.dumps({
    'title': app.title,
    'paths': sorted(paths),
    'erp_module': ERPClient.__module__,
    'proposal_presentation': manifest().PUBLIC_METADATA['proposal_presentation'],
    'policy_text': ' '.join([
        _tool_search_schema()['function']['description'],
        _tool_search_schema()['function']['parameters']['properties']['query']['description'],
        permission_mode_instruction('ask'),
        REACT_GUIDANCE,
    ]),
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    paths = set(payload["paths"])

    assert payload["title"] == "Agent Workbench"
    assert payload["erp_module"] == "domain_packs.template.erp_adapter"
    assert payload["proposal_presentation"] == {
        "action_prefixes": [],
        "action_suffixes": [],
        "detail_links": {},
        "value_names": {},
    }
    assert not any(term in payload["policy_text"] for term in (
        "项目", "模具", "工程联络", "合同", "采购", "审批席位",
    ))
    assert {"/api/product", "/api/auth/login", "/api/runs"} <= paths
    assert "/api/projects" not in paths
    assert "/api/purchases" not in paths
    assert "/api/contacts" not in paths
    assert "/api/business/subjects" not in paths
    assert "/api/erp-design-uploads/parse" not in paths
    assert "/api/contact-proposals/{step_id}" not in paths
