from domain_packs.mold import tool_gateway


def test_engineering_contact_document_skill_is_trusted_attachment_skill():
    spec = tool_gateway.SKILLS["document_engineering_contact_intake"]

    assert "query_document_intake" in spec["tools"]
    assert spec["activation_triggers"] == ["DOCUMENT_CLASSIFICATION_CONFIRMED"]
    assert set(spec["activation_media_types"]) == {
        "application/pdf", "image/png", "image/jpeg",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    assert "prepare_contact_create" in spec["optional_tools"]
    assert tool_gateway.skill_paths()["document_engineering_contact_intake"]["layer"] == "local"


def test_engineering_contact_classification_is_not_an_existing_legacy_type():
    from domain_packs.mold.erp.commercial.contract_intake_models import DOCUMENT_TYPES

    assert "ENGINEERING_CONTACT" not in DOCUMENT_TYPES
