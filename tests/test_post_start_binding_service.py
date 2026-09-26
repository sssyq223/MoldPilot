import pytest

from domain_packs.mold.erp.commercial.post_start_binding import (
    validate_target_reference,
)
from domain_packs.mold.erp.commercial import checklist_binding
from domain_packs.mold.ports.errors import DomainError


def test_erp_checklist_reference_is_a_bindable_versioned_target():
    reference = {
        "kind": "accounting_checklist", "revision": 1,
        "fingerprint": "a" * 64, "mold_no": "M-001",
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


def test_erp_checklist_reference_reads_external_identity_without_copying_rows(monkeypatch):
    class Db:
        def get(self, model, user_id):
            return type("Identity", (), {"token_ciphertext": "cipher"})()

    class Client:
        def __init__(self, token):
            assert token == "token"
            self.closed = False

        def accounting_checklist_reference(self, file_id):
            assert file_id == 42
            return {
                "source_system": "ERP", "source_type": "production_cost_sheet_file",
                "id": 42, "version": 1, "fingerprint": "b" * 64,
                "bindable": True, "mold_no": "M-001", "line_count": 3,
            }

        def close(self):
            self.closed = True

    monkeypatch.setattr(checklist_binding, "settings", lambda: type("S", (), {"erp_base_url": "https://erp.example"})())
    monkeypatch.setattr(checklist_binding, "decrypt", lambda value: "token")
    result = checklist_binding.resolve_reference(
        Db(), type("User", (), {"id": "u1"})(),
        "erp:production_cost_sheet_file:42", 1, client_factory=Client,
    )
    assert result == {
        "kind": "accounting_checklist", "id": "42", "project_id": None,
        "revision": 1, "fingerprint": "b" * 64, "mold_no": "M-001",
        "line_count": 3, "source_system": "ERP", "source_type": "production_cost_sheet_file",
    }


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
