import pytest

from domain_packs.mold.erp.commercial.post_start_binding import (
    validate_target_reference,
)
from domain_packs.mold.erp.commercial import checklist_binding
from domain_packs.mold.ports.errors import DomainError


def test_local_checklist_reference_is_a_bindable_versioned_target():
    reference = {
        "kind": "local_accounting_checklist", "project_id": "p1",
        "revision": 1, "fingerprint": "a" * 64, "source_system": "agent_db",
    }
    assert validate_target_reference(
        reference, target_type="ACCOUNTING_CHECKLIST", project_id="p1", target_version=1,
    ) is None

    with pytest.raises(DomainError) as error:
        validate_target_reference(
            {**reference, "fingerprint": ""},
            target_type="ACCOUNTING_CHECKLIST", project_id="p1", target_version=1,
        )
    assert error.value.code == "VERSION_CONFLICT"


def test_local_checklist_reference_reads_local_identity_without_copying_rows():
    class Db:
        def get(self, model, row_id):
            return type("Row", (), {
                "id": row_id, "kind": "local_cost_sheet", "project_id": "p1",
                "revision": 1, "number": "CS-001",
            })()

    result = checklist_binding.resolve_reference(
        Db(), type("User", (), {"id": "u1"})(), "local-id", 1,
    )
    assert result["kind"] == "local_accounting_checklist"
    assert result["id"] == "local-id"
    assert result["project_id"] == "p1"
    assert result["source_system"] == "agent_db"
    assert result["source_type"] == "local_cost_sheet"
    assert result["fingerprint"]


def test_contract_binding_requires_sales_contract_in_same_project_and_current_revision():
    with pytest.raises(DomainError) as error:
        validate_target_reference(
            {"kind": "quotation", "project_id": "p1", "revision": 2},
            target_type="SALES_CONTRACT", project_id="p1", target_version=2,
        )
    assert error.value.code == "BINDING_TARGET_INVALID"

    with pytest.raises(DomainError) as error:
        validate_target_reference(
            {"kind": "sales_contract", "project_id": "p2", "revision": 2},
            target_type="SALES_CONTRACT", project_id="p1", target_version=2,
        )
    assert error.value.code == "BINDING_PROJECT_CONFLICT"

    with pytest.raises(DomainError) as error:
        validate_target_reference(
            {"kind": "sales_contract", "project_id": "p1", "revision": 2},
            target_type="SALES_CONTRACT", project_id="p1", target_version=1,
        )
    assert error.value.code == "VERSION_CONFLICT"
