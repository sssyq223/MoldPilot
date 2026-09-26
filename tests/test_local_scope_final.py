from pathlib import Path

from domain_packs.mold import tool_gateway
from local_scope import current_worktree_scope


def test_new_local_tools_are_reachable_from_registered_skills():
    local_tools = {
        "query_local_change_context",
        "prepare_local_change_intake",
        "prepare_local_change_association",
        "prepare_local_change_acceptance",
        "query_model_configuration",
        "query_model_provider_directory",
        "prepare_model_provider_save",
        "prepare_model_save",
        "prepare_model_default",
    }
    registered = set(tool_gateway.TOOLS)
    referenced = {
        name
        for skill in tool_gateway.SKILLS.values()
        for name in skill.get("tools", []) + skill.get("optional_tools", [])
    }
    assert local_tools <= registered
    assert local_tools <= referenced


def test_protected_remote_change_modules_remain_outside_worktree_scope():
    scope = current_worktree_scope()
    protected = {
        "backend/domain_packs/mold/tools/erp/change/change_intake_tools.py",
        "backend/domain_packs/mold/tools/erp/change/contact_tools.py",
        "backend/domain_packs/mold/skills/erp/change/change_intake_review/SKILL.md",
        "backend/domain_packs/mold/skills/erp/change/contact_collaboration_review/SKILL.md",
    }
    assert not protected & scope


def test_local_skill_documents_are_present_under_local_boundary():
    root = Path(__file__).resolve().parents[1] / "backend" / "domain_packs" / "mold" / "skills" / "local"
    for relative in (
        "change/engineering_change_intake/SKILL.md",
        "change/engineering_contact_collaboration/SKILL.md",
        "config/model_provider_configuration/SKILL.md",
        "config/document_model_configuration/SKILL.md",
    ):
        assert (root / relative).is_file()
