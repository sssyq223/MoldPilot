import pytest

from agent_core.errors import DomainError
from domain_packs.mold.tools.local import change_intake_tools


class User:
    id = "user-1"
    security_version = 1
    super_admin = True


def test_local_change_intake_prepare_is_an_uncommitted_proposal():
    result = change_intake_tools.execute_tool(None, User(), "prepare_local_change_intake", {
        "project_id": "project-1",
        "mold_mode": "EXISTING",
        "classification": "CUSTOMER",
        "execution_mode": "INTERNAL",
        "charge_status": "FREE",
        "contract_status": "NONE",
        "execution_scope": "局部修模并复验",
        "customer_basis": "客户邮件确认-1",
        "customer_mold_number": "C-MOLD-001",
        "original_mold_id": "mold-1",
    })

    assert result["source"] == "agent_proposal"
    assert result["proposal"]["input"]["mold_mode"] == "EXISTING"
    assert result["proposal"]["input"]["classification"] == "CUSTOMER"
    assert result["proposal"]["input"]["contract_status"] == "NONE"
    assert result["proposal"]["input"]["original_mold_id"] == "mold-1"


def test_local_change_prepare_rejects_unknown_external_identity_fields():
    with pytest.raises(DomainError, match="参数"):
        change_intake_tools.execute_tool(None, User(), "prepare_local_change_intake", {
            "project_id": "project-1",
            "classification": "CUSTOMER",
            "execution_mode": "INTERNAL",
            "charge_status": "FREE",
            "contract_status": "NONE",
            "execution_scope": "局部修模",
            "customer_basis": "客户邮件确认-1",
            "erp_change_id": "erp-123",
        })


def test_new_external_mold_change_requires_manual_intake_branch():
    result = change_intake_tools.execute_tool(None, User(), "prepare_local_change_intake", {
        "project_id": "project-1",
        "mold_mode": "NEW_EXTERNAL",
        "classification": "CUSTOMER",
        "execution_mode": "OUTSOURCE",
        "charge_status": "PENDING",
        "contract_status": "REQUIRED",
        "execution_scope": "首次外部模具设变，待报价承接",
        "customer_basis": "客户新模号资料-1",
        "customer_mold_number": "NEW-CUSTOMER-MOLD-001",
    })
    assert result["proposal"]["input"]["mold_mode"] == "NEW_EXTERNAL"
    assert result["proposal"]["input"]["contract_status"] == "REQUIRED"


def test_local_change_association_rejects_external_identity_or_url():
    with pytest.raises(DomainError):
        change_intake_tools.execute_tool(None, User(), "prepare_local_change_association", {
            "change_id": "change-1",
            "expected_revision": 1,
            "association_type": "DOCUMENT",
            "target_id": "erp:document:123",
            "evidence": "本地文件依据",
        })


def test_local_change_acceptance_requires_independent_basis():
    with pytest.raises(DomainError):
        change_intake_tools.execute_tool(None, User(), "prepare_local_change_acceptance", {
            "change_id": "change-1",
            "expected_revision": 1,
            "acceptance_status": "ACCEPTED",
            "acceptance_basis": "",
        })
