import pytest

from domain_packs.mold.erp.commercial.admin_start_schemas import material_values
from domain_packs.mold.ports.errors import DomainError


def test_internal_start_material_rejects_contract_only_fields():
    with pytest.raises(DomainError) as error:
        material_values({"project_name": "项目A", "contract_number": "SC-001"})
    assert error.value.code == "ADMIN_MATERIAL_INVALID"


def test_internal_start_material_keeps_internal_start_date():
    values = material_values({"effective_date": "2026-10-01"})
    assert values == {"effective_date": "2026-10-01"}


def test_internal_start_material_rejects_contract_schedule():
    with pytest.raises(DomainError) as error:
        material_values({"effective_date": "2026-10-01", "expected_contract_date": "2026-10-15"})
    assert error.value.code == "ADMIN_MATERIAL_INVALID"
