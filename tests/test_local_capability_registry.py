from domain_packs.mold import tool_gateway


def test_local_business_skills_and_change_tools_are_registered():
    assert "sales_contract_intake" in tool_gateway.SKILLS
    assert "bid_to_start_notice" in tool_gateway.SKILLS
    assert "engineering_change_intake" in tool_gateway.SKILLS
    assert "engineering_contact_collaboration" in tool_gateway.SKILLS
    assert "model_provider_configuration" in tool_gateway.SKILLS
    assert "document_model_configuration" in tool_gateway.SKILLS
    assert "query_local_change_context" in tool_gateway.TOOLS
    assert "prepare_local_change_intake" in tool_gateway.TOOLS
    assert "prepare_local_change_association" in tool_gateway.TOOLS
    assert "prepare_local_change_acceptance" in tool_gateway.TOOLS
    assert "query_model_provider_directory" in tool_gateway.TOOLS


def test_local_change_tool_has_strict_function_schema():
    schema = tool_gateway.tool_schema("prepare_local_change_intake")
    assert schema["function"]["name"] == "prepare_local_change_intake"
    assert schema["function"]["parameters"]["additionalProperties"] is False


def test_local_skill_documents_are_indexed_in_local_route():
    paths = tool_gateway.skill_paths()
    assert paths["engineering_change_intake"]["layer"] == "local"
    assert paths["engineering_contact_collaboration"]["layer"] == "local"
