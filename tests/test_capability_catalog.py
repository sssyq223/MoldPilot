from app.tool_gateway import SKILLS, TOOLS, capability_descriptor


def test_tool_descriptor_carries_backend_catalog_metadata():
    item = capability_descriptor("TOOL", "query_purchase_requests", TOOLS["query_purchase_requests"])
    assert item["name"] == "查询采购申请"
    assert item["department"] == "purchase"
    assert item["department_name"] == "采购部门"
    assert item["type"] == "query"
    assert item["type_name"] == "查询"
    assert item["mode"] == "read_only"
    assert item["business_key"] == "purchase"
    assert item["dependencies"] == []
    assert item["optional_dependencies"] == []


def test_skill_descriptor_keeps_dependencies_and_review_metadata():
    item = capability_descriptor("SKILL", "operations_readiness_review", SKILLS["operations_readiness_review"])
    assert item["name"] == "运行交付就绪核对"
    assert item["department"] == "system"
    assert item["type"] == "review"
    assert item["mode"] == "read_only"
    assert item["dependencies"] == ["query_operations_readiness_context"]
    assert item["optional_dependencies"] == []


def test_change_intake_skill_exposes_optional_plan_change_bridge_without_hard_dependency():
    item = capability_descriptor("SKILL", "change_intake_review", SKILLS["change_intake_review"])
    assert item["name"] == "设变承接上下文核对"
    assert item["department"] == "engineering"
    assert item["type"] == "review"
    assert item["mode"] == "read_only"
    assert item["dependencies"] == ["query_change_intake_context"]
    assert item["optional_dependencies"] == ["query_project_plan_context", "prepare_project_plan_change"]


def test_design_route_skill_exposes_optional_plan_change_bridge_without_hard_dependency():
    item = capability_descriptor("SKILL", "design_route_context_review", SKILLS["design_route_context_review"])
    assert item["name"] == "设计BOM与路线上下文核对"
    assert item["department"] == "design"
    assert item["type"] == "review"
    assert item["mode"] == "read_only"
    assert item["dependencies"] == ["query_design_route_context"]
    assert item["optional_dependencies"] == ["query_project_plan_context", "prepare_project_plan_change"]


def test_contract_signing_record_tool_is_human_confirmed_operation_in_contract_pack():
    tool = capability_descriptor("TOOL", "prepare_contract_signing_record", TOOLS["prepare_contract_signing_record"])
    assert tool["name"] == "准备合同签署记录"
    assert tool["department"] == "finance"
    assert tool["type"] == "operation"
    assert tool["mode"] == "human_confirmed_proposal"
    skill = capability_descriptor("SKILL", "contract_context_review", SKILLS["contract_context_review"])
    assert skill["dependencies"] == ["query_contract_context"]
    assert "prepare_contract_signing_record" in skill["optional_dependencies"]


def test_contact_collaboration_skill_has_curated_activation_pack():
    item = capability_descriptor("SKILL", "contact_collaboration_review", SKILLS["contact_collaboration_review"])
    assert item["dependencies"] == ["query_contact_cases"]
    assert "query_contact_context" in item["optional_dependencies"]
    assert "prepare_contact_close" in item["optional_dependencies"]
    assert item["activation_dependencies"] == [
        "query_contact_cases",
        "query_contact_context",
        "prepare_contact_resolution",
        "prepare_contact_review",
        "prepare_contact_close",
        "prepare_contact_respond",
    ]
