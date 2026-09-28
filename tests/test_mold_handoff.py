from types import SimpleNamespace

from domain_packs.mold.erp.project import mold_handoff


def test_mold_handoff_reports_unconfigured_erp_without_inventing_local_mapping():
    class FakeDB:
        def scalars(self, _query):
            return iter(())

        def get(self, *_args):
            return None

    old_settings = mold_handoff.settings
    mold_handoff.settings = lambda: SimpleNamespace(erp_base_url="")
    try:
        result = mold_handoff.query(
            FakeDB(),
            SimpleNamespace(id="u-1"),
            SimpleNamespace(id="p-1", code="P-001"),
        )
    finally:
        mold_handoff.settings = old_settings

    assert result["status"] == "NOT_CONFIGURED"
    assert result["handoff_state"] == "ERP_NOT_CONFIGURED"
    assert result["local_internal_mold_numbers"] == []
    assert result["records"] == []
    assert "未读取原系统" in "".join(result["limitations"])


def test_mold_handoff_classifies_one_erp_candidate_as_human_handoff():
    class FakeDB:
        def scalars(self, _query):
            return iter(())

        def get(self, *_args):
            return SimpleNamespace(token_ciphertext="cipher")

    class FakeClient:
        def __init__(self, token):
            assert token == "token"

        def business_molds(self, project_no=None):
            assert project_no == "P-001"
            return {
                "records": [
                    {
                        "project_code": "P-001",
                        "mold_code": "M-001",
                        "source_ref": "scheduling/api/business/molds/:P-001:M-001",
                    }
                ],
                "as_of": "2026-09-26T00:00:00+08:00",
                "limitations": ["只读"],
            }

        def close(self):
            pass

    old_settings = mold_handoff.settings
    old_decrypt = mold_handoff.decrypt
    mold_handoff.settings = lambda: SimpleNamespace(erp_base_url="https://erp.example")
    mold_handoff.decrypt = lambda value: "token"
    try:
        result = mold_handoff.query(
            FakeDB(),
            SimpleNamespace(id="u-1"),
            SimpleNamespace(id="p-1", code="P-001"),
            client_factory=FakeClient,
        )
    finally:
        mold_handoff.settings = old_settings
        mold_handoff.decrypt = old_decrypt

    assert result["status"] == "RESOLVED"
    assert result["handoff_state"] == "ERP_CANDIDATE_REQUIRES_HANDOFF"
    assert result["exact_project_records"][0]["mold_code"] == "M-001"
    assert "不会自动建立 project_mold" in "".join(result["limitations"])


def test_mold_handoff_exposes_unmatched_erp_candidates_for_human_project_mapping():
    class FakeDB:
        def scalars(self, _query):
            return iter(())

        def scalar(self, _query):
            return None

        def get(self, *_args):
            return SimpleNamespace(token_ciphertext="cipher")

    class FakeClient:
        def __init__(self, token):
            assert token == "token"

        def business_molds(self, project_no=None):
            if project_no:
                return {"records": [], "as_of": "2026-09-26T00:00:00+08:00", "limitations": []}
            return {
                "records": [{
                    "project_code": "M260133",
                    "mold_code": "M260133-P3",
                    "source_ref": "scheduling/api/business/molds/:M260133:M260133-P3",
                }],
                "as_of": "2026-09-26T00:00:00+08:00",
                "limitations": ["只读"],
            }

        def close(self):
            pass

    old_settings = mold_handoff.settings
    old_decrypt = mold_handoff.decrypt
    mold_handoff.settings = lambda: SimpleNamespace(erp_base_url="https://erp.example")
    mold_handoff.decrypt = lambda value: "token"
    try:
        result = mold_handoff.query(
            FakeDB(),
            SimpleNamespace(id="u-1"),
            SimpleNamespace(id="p-1", code="BROWSER-BID-START-001"),
            client_factory=FakeClient,
        )
    finally:
        mold_handoff.settings = old_settings
        mold_handoff.decrypt = old_decrypt

    assert result["handoff_state"] == "ERP_PROJECT_MAPPING_REQUIRED"
    assert result["project_mapping_state"] == "REQUIRES_HUMAN_MAPPING"
    assert result["records"][0]["project_code"] == "M260133"
    assert result["exact_project_records"] == []
