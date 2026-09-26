from pathlib import Path

import pytest

from domain_packs.mold.erp.commercial.post_start_binding import validate_target_reference
from domain_packs.mold.ports.errors import DomainError


def test_local_post_start_binding_rejects_erp_accounting_checklist_reference():
    with pytest.raises(DomainError):
        validate_target_reference(
            {"kind": "accounting_checklist", "revision": 1, "fingerprint": "x"},
            target_type="ACCOUNTING_CHECKLIST",
            project_id="project-1",
            target_version=1,
        )


def test_local_commercial_workflow_has_no_erp_runtime_imports():
    root = Path(__file__).resolve().parents[1] / "backend" / "domain_packs" / "mold" / "erp" / "commercial"
    files = [
        "checklist_binding.py", "post_start_binding.py", "document_workflow.py",
        "document_workflow_api.py", "bid_start_workflow.py", "admin_start_workflow.py",
        "contract_match_workflow.py",
    ]
    source = "\n".join((root / name).read_text(encoding="utf-8") for name in files)
    assert "ERPClient" not in source
    assert "erp_adapter" not in source
