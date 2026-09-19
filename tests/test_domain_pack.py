import json
import os
from pathlib import Path
import subprocess
import sys
import re
import pytest

from agent_core.domain_pack import (
    active_pack_name,
    authorization_contract,
    component,
    manifest,
    resource_contract,
    validate_public_metadata,
)
from agent_core.host_ports import HostPorts, host_ports
from agent_core.migration_runtime import resolve_migration_url
from agent_core import tool_gateway as core_gateway
from agent_core.harness import _route_skill_groups
from app import tool_gateway as host_gateway
from domain_packs.mold import tool_gateway as mold_gateway


def test_migration_url_process_overrides_dotenv(monkeypatch):
    monkeypatch.delenv("AGENT_MIGRATION_URL", raising=False)
    monkeypatch.setenv("MOLD_MIGRATION_URL", "postgresql://process-legacy")
    assert resolve_migration_url({
        "AGENT_MIGRATION_URL": "postgresql://dotenv-agent",
        "MOLD_MIGRATION_URL": "postgresql://dotenv-legacy",
    }) == "postgresql://process-legacy"

    monkeypatch.setenv("AGENT_MIGRATION_URL", "postgresql://process-agent")
    assert resolve_migration_url({
        "AGENT_MIGRATION_URL": "postgresql://dotenv-agent",
    }) == "postgresql://process-agent"


def test_migration_url_requires_explicit_configuration(monkeypatch):
    monkeypatch.delenv("AGENT_MIGRATION_URL", raising=False)
    monkeypatch.delenv("MOLD_MIGRATION_URL", raising=False)
    with pytest.raises(RuntimeError, match="AGENT_MIGRATION_URL is required"):
        resolve_migration_url({})


def test_product_selects_installed_business_pack_and_core_uses_its_contract():
    from app import models as model_catalog

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
    assert model_catalog.Project.__module__ == "domain_packs.mold.models"
    assert model_catalog.BusinessSubject.__module__ == "domain_packs.mold.erp.core.domain_models"
    assert model_catalog.ContactCase.__module__ == "domain_packs.mold.erp.change.contact_models"
    assert model_catalog.ContactAttachment.__module__ == "domain_packs.mold.erp.change.attachment_models"
    assert model_catalog.Base.__module__ == "agent_core.model_base"
    assert "purchase.read" in authorization_contract().PERMISSIONS
    assert resource_contract().APPROVAL_RESOURCE_TYPES == {
        "purchase_request", "business_subject",
    }
    assert callable(resource_contract().initiated_approval_ids)
    assert component("migrations").STAGES == (
        {"name": "core", "config": "alembic-core.ini",
         "version_table": "alembic_core_version"},
        {"name": "mold", "config": "backend/domain_packs/mold/alembic-domain.ini",
         "version_table": "alembic_mold_version"},
    )
    assert component("migrations").LEGACY == {
        "config": "backend/domain_packs/mold/alembic.ini",
        "version_table": "alembic_version",
    }
    assert {
        "erp_design_query_bom",
        "erp_design_query_drawing_versions",
        "erp_design_query_densities",
        "erp_design_query_standard_hardware",
    }.issubset(core_gateway.TOOLS)
    project_root = Path(__file__).resolve().parents[1]
    assert not list((project_root / "alembic" / "versions").glob("*.py"))
    assert not (project_root / "backend" / "app" / "erp_design_mcp.py").exists()
    assert not (project_root / "backend" / "app" / "erp_design_upload.py").exists()
    assert (project_root / "backend" / "domain_packs" / "mold" / "erp" /
            "design" / "erp_design_upload.py").is_file()
    assert (project_root / "backend" / "domain_packs" / "mold" / "tools" /
            "erp" / "design" / "erp_design_mcp.py").is_file()
    assert (project_root / "backend" / "domain_packs" / "mold" / "skills" /
            "erp" / "design" / "erp_design_workspace_review" / "SKILL.md").is_file()
    assert (project_root / "backend" / "domain_packs" / "mold" / "skills" /
            "erp" / "design" / "erp_design_drawing_version_review" / "SKILL.md").is_file()
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


def test_skill_directory_hierarchy_is_a_retrieval_boundary():
    paths = mold_gateway.skill_paths()
    design = paths["erp_design_workspace_review"]
    procurement = paths["purchase_request_review"]

    assert (design["layer"], design["domain"]) == ("erp", "design")
    assert (procurement["layer"], procurement["domain"]) == ("erp", "procurement")

    groups = [
        {"key": "design", "skill_layer": "erp", "skill_domain": "design",
         "route_terms": design["route_terms"]},
        {"key": "procurement", "skill_layer": "erp", "skill_domain": "procurement",
         "route_terms": procurement["route_terms"]},
    ]
    assert [group["key"] for group in _route_skill_groups("帮我检查设计图纸", groups)] == ["design"]
    assert [group["key"] for group in _route_skill_groups("查询采购订单", groups)] == ["procurement"]
    assert _route_skill_groups("继续处理", groups) == groups


def test_formal_start_conversation_title_wins_over_generic_notification_word():
    from domain_packs.mold.manifest import conversation_title

    title = conversation_title(
        "查询 BROWSER-START-HANDOFF-001 的正式开工和五部门通知投递结果"
    )

    assert title == "BROWSER-START-HANDOFF-001 开工条件核对"


def test_tool_implementations_are_categorized_beside_skills():
    project_root = Path(__file__).resolve().parents[1]
    tool_root = project_root / "backend" / "domain_packs" / "mold" / "tools"

    assert (tool_root / "erp" / "design" / "design_tools.py").is_file()
    assert (tool_root / "erp" / "project" / "plan_tools.py").is_file()
    assert (tool_root / "erp" / "change" / "contact_tools.py").is_file()
    assert (tool_root / "agent" / "operations" / "operations_readiness_tools.py").is_file()


def test_pack_boundary_has_no_hidden_host_imports_or_root_mcp_runtime():
    project_root = Path(__file__).resolve().parents[1]
    pack_root = project_root / "backend" / "domain_packs" / "mold"
    direct_host_imports = []
    for path in pack_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if re.search(r"(?m)^\s*(?:from\s+app(?:\.|\s)|import\s+app(?:\.|\s|$))", source):
            direct_host_imports.append(path.relative_to(pack_root).as_posix())
    assert direct_host_imports == []

    mcp_root = pack_root / "mcp" / "erp-design-upload"
    assert not (project_root / "mcp" / "erp-design-upload").exists()
    assert (mcp_root / "package.json").is_file()
    assert (mcp_root / "scripts" / "install-erp-design-package.mjs").is_file()
    assert not (mcp_root / "package-lock.json").exists()
    mcp_source = "\n".join(path.read_text(encoding="utf-8") for path in mcp_root.rglob("*") if path.is_file())
    assert "D:/work2" not in mcp_source
    assert "D:\\work2" not in mcp_source


def test_generic_host_config_and_frontend_shell_are_product_neutral():
    project_root = Path(__file__).resolve().parents[1]
    config_source = (project_root / "backend" / "app" / "config.py").read_text(encoding="utf-8")
    for field in (
        "logistics_quote_max_valid_days", "erp_base_url",
        "erp_allow_insecure_local", "credential_encryption_key",
    ):
        assert field not in config_source

    generic_source = "\n".join(
        (project_root / relative).read_text(encoding="utf-8")
        for relative in ("web/index.html", "web/src/theme.ts", "web/src/api.ts")
    ).lower()
    for legacy in ("moldpilot", "mold_session", "mold_csrf"):
        assert legacy not in generic_source

    package = json.loads((project_root / "web" / "package.json").read_text(encoding="utf-8"))
    assert "tsconfig.mold.json" in package["scripts"]["typecheck"]
    assert "tsconfig.template.json" in package["scripts"]["typecheck"]
    assert "tsconfig.template.json" in package["scripts"]["build:template"]
    app_source = (project_root / "web" / "src" / "App.vue").read_text(encoding="utf-8")
    assert "__DOMAIN_PACK_ID__" in app_source


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
        "prepare_project_pause", "domain_packs.mold",
    ):
        assert business_term not in runtime_source


def test_delivery_logistics_implementation_lives_in_mold_pack_not_host():
    project_root = Path(__file__).resolve().parents[1]
    handlers = component("proposal_handlers")
    logistics = handlers.handler_for_tool("prepare_logistics_route")

    assert logistics is not None
    assert logistics.module == "domain_packs.mold.erp.procurement.delivery_logistics"
    assert logistics.implementation().__name__ == logistics.module

    pack_source = (project_root / "backend" / "domain_packs" / "mold" / "erp" / "procurement" / "delivery_logistics.py").read_text(encoding="utf-8")
    legacy_ports_source = (project_root / "backend" / "domain_packs" / "mold" / "erp" / "core" / "legacy_read_ports.py").read_text(encoding="utf-8")
    gateway_source = (project_root / "backend" / "domain_packs" / "mold" / "tool_gateway.py").read_text(encoding="utf-8")
    plan_source = (project_root / "backend" / "domain_packs" / "mold" / "tools" / "erp" / "project" / "plan_tools.py").read_text(encoding="utf-8")

    assert not (project_root / "backend" / "app" / "delivery_logistics_tools.py").exists()
    assert "class LogisticsRouteProposalInput" in pack_source
    assert "from app" not in pack_source
    assert "domain_packs.mold.erp.core.domains" in legacy_ports_source
    assert "app.delivery_logistics_tools" not in gateway_source
    assert "domain_packs.mold.erp.procurement.delivery_logistics" in gateway_source
    assert "from app.plan_tools import ProjectPlanContextInput" not in gateway_source
    assert "from app" not in plan_source


def test_domain_pack_uses_validated_host_port_contract():
    ports = host_ports()

    assert isinstance(ports, HostPorts)
    assert ports.models.__name__ == "app.models"
    for name in (
        "access", "grants_for", "fingerprint", "predicate", "require", "select_fields",
        "content_hash", "proposal_confirmation_policy", "settings", "model_settings",
        "get_db", "now", "record", "current_user", "conversation_files",
        "uploaded_file", "reference_run_file", "reference_run_files", "file_metadata",
    ):
        assert callable(getattr(ports, name))
    from domain_packs.mold.erp.core.contracts import ProjectPlanContextInput
    assert ProjectPlanContextInput.__module__ == "domain_packs.mold.erp.core.contracts"


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


def test_generic_frontend_shell_has_no_mold_business_implementation():
    project_root = Path(__file__).resolve().parents[1]
    common_files = [project_root / "web" / "src" / "App.vue"]
    common_files.extend((project_root / "web" / "src" / "components").glob("*.vue"))
    common_source = "\n".join(path.read_text(encoding="utf-8") for path in common_files)

    forbidden = (
        "query_projects", "query_purchase_requests", "query_contact_cases",
        "purchase_request", "business_subject", "contact_case",
        "/api/projects", "/api/purchases", "/api/contacts",
        "mold.agentPermissionMode", "mold.layout",
        "mold.workflow.canvas",
        "工程联络单", "采购申请明细", "测试采购类别", "测试项目标识",
        "项目角色", "project_id", "PROJECT_OWNER",
    )
    assert not [term for term in forbidden if term in common_source]
    assert not (project_root / "web" / "src" / "components" / "WorkflowSubmit.vue").exists()
    assert not (project_root / "web" / "src" / "components" / "ErpDesignWorkbench.vue").exists()
    assert "ERP 设计管理" not in common_source

    from app.api import app
    assert not any(
        getattr(route, "path", "").startswith("/api/erp-design-workspace")
        for route in app.routes
    )

    for pack in ("mold", "template"):
        pack_root = project_root / "web" / "src" / "domain-packs" / pack
        for relative in (
            "evidence.ts", "uiPolicy.ts", "components/WelcomePanel.vue",
            "components/DomainWorkspacePanel.vue",
            "components/ApprovalBusinessDetails.vue",
        ):
            assert (pack_root / relative).is_file(), f"{pack} is missing {relative}"

    assert (project_root / "web" / "src" / "domain-packs" / "mold" /
            "components" / "WorkflowSubmit.vue").is_file()


def test_generic_api_delegates_initiated_resource_ownership_to_pack():
    project_root = Path(__file__).resolve().parents[1]
    source = (project_root / "backend" / "app" / "api.py").read_text(encoding="utf-8")

    assert "resource_contract().initiated_approval_ids" in source
    assert "m.PurchaseRequest" not in source
    assert "m.BusinessSubject" not in source
    assert 'resource_type == "purchase_request"' not in source
    assert 'resource_type == "business_subject"' not in source


def test_template_pack_boots_host_without_registering_mold_http_surface():
    project_root = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "PYTHONPATH": str(project_root / "backend"),
        "AGENT_BUSINESS_PACK": "template",
    }
    script = """
import json
import sys
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from app.api import app
from app.models import Base
from app.authorization import PERMISSIONS, DIMENSIONS
from app.erp_adapter import ERPClient
from agent_core.harness import _tool_search_schema, permission_mode_instruction
from agent_core.ollama_adapter import REACT_GUIDANCE
from agent_core.domain_pack import component, manifest, resource_contract
from agent_core.domain_pack import migration_contract
paths = {route.path for route in app.routes if hasattr(route, 'path')}
sorted_tables = list(Base.metadata.sorted_tables)
ddl = [str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in sorted_tables]
print(json.dumps({
    'title': app.title,
    'paths': sorted(paths),
    'erp_module': ERPClient.__module__,
    'proposal_presentation': manifest().PUBLIC_METADATA['proposal_presentation'],
    'tables': sorted(Base.metadata.tables),
    'sorted_tables': [table.name for table in sorted_tables],
    'ddl_count': len(ddl),
    'permissions': sorted(PERMISSIONS),
    'dimensions': sorted(DIMENSIONS),
    'approval_resource_types': sorted(resource_contract().APPROVAL_RESOURCE_TYPES),
    'migration_stages': migration_contract().STAGES,
    'workflow_assignment': component('workflow_assignment').catalog(None, None),
    'mold_modules': sorted(name for name in sys.modules if name.startswith('domain_packs.mold')),
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
    assert payload["mold_modules"] == []
    assert payload["sorted_tables"] == payload["tables"] or set(payload["sorted_tables"]) == set(payload["tables"])
    assert payload["ddl_count"] == len(payload["tables"])
    assert payload["permissions"] == [
        "audit.read", "file.upload", "grant.manage", "user.manage",
        "workflow.design", "workflow.publish",
    ]
    assert payload["dimensions"] == []
    assert payload["approval_resource_types"] == []
    assert payload["migration_stages"] == [{
        "name": "core",
        "config": "alembic-core.ini",
        "version_table": "alembic_core_version",
    }]
    assert payload["workflow_assignment"] == {
        "kind": "", "label": "", "scope_label": "", "context_key": "",
        "roles": [], "scopes": [], "capabilities": [],
        "responsibility_dimensions": [],
    }
    assert not ({
        "project", "material", "purchase_request", "purchase_request_line",
        "business_subject", "supplier", "customer", "mold", "project_mold",
        "project_profile", "warehouse", "purchase_order", "contact_case",
        "contact_task", "contact_record", "contact_resolution", "contact_attachment",
        "logistics_route", "logistics_quote", "project_role_config",
        "project_role_member",
    } & set(payload["tables"]))
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
