from local_scope import current_worktree_scope


def test_scope_contains_worktree_changes_but_not_remote_only_files():
    allowed = current_worktree_scope()
    assert "backend/domain_packs/mold/tool_gateway.py" in allowed
    assert "backend/domain_packs/mold/tools/erp/change/change_intake_tools.py" not in allowed
