from domain_packs.mold.tools.erp.design.erp_design_mcp import TOOL_NAMES
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
    assert item["optional_dependencies"] == [
        "query_project_plan_context", "prepare_project_plan_change", "prepare_design_order_approval",
    ]


def test_contract_signing_record_tool_is_human_confirmed_operation_in_contract_pack():
    tool = capability_descriptor("TOOL", "prepare_contract_signing_record", TOOLS["prepare_contract_signing_record"])
    assert tool["name"] == "准备合同签署记录"
    assert tool["department"] == "finance"
    assert tool["type"] == "operation"
    assert tool["mode"] == "human_confirmed_proposal"
    skill = capability_descriptor("SKILL", "contract_context_review", SKILLS["contract_context_review"])
    assert skill["dependencies"] == ["query_contract_context"]
    assert "prepare_contract_signing_record" in skill["optional_dependencies"]


def test_supplier_material_handoff_tool_is_in_full_outsource_pack():
    tool = capability_descriptor("TOOL", "prepare_supplier_material_handoff", TOOLS["prepare_supplier_material_handoff"])
    assert tool["name"] == "准备供应商资料交接"
    assert tool["department"] == "purchase"
    assert tool["type"] == "operation"
    assert tool["mode"] == "human_confirmed_proposal"
    skill = capability_descriptor("SKILL", "full_outsource_review", SKILLS["full_outsource_review"])
    assert skill["dependencies"] == ["query_full_outsource_context"]
    assert "prepare_supplier_material_handoff" in skill["optional_dependencies"]


def test_supplier_material_verification_tool_is_human_confirmed_full_outsource_operation():
    tool = capability_descriptor("TOOL", "prepare_supplier_material_verification", TOOLS["prepare_supplier_material_verification"])
    assert tool["name"] == "准备供应商资料核验"
    assert tool["department"] == "purchase"
    assert tool["type"] == "operation"
    assert tool["mode"] == "human_confirmed_proposal"
    skill = capability_descriptor("SKILL", "full_outsource_review", SKILLS["full_outsource_review"])
    assert "prepare_supplier_material_verification" in skill["optional_dependencies"]


def test_supplier_progress_report_tool_is_human_confirmed_full_outsource_operation():
    tool = capability_descriptor("TOOL", "prepare_supplier_progress_report", TOOLS["prepare_supplier_progress_report"])
    assert tool["name"] == "准备供应商节点上报"
    assert tool["department"] == "purchase"
    assert tool["type"] == "operation"
    assert tool["mode"] == "human_confirmed_proposal"
    skill = capability_descriptor("SKILL", "full_outsource_review", SKILLS["full_outsource_review"])
    assert "prepare_supplier_progress_report" in skill["optional_dependencies"]


def test_supplier_progress_policy_tool_is_human_confirmed_full_outsource_operation():
    tool = capability_descriptor("TOOL", "prepare_supplier_progress_policy", TOOLS["prepare_supplier_progress_policy"])
    assert tool["name"] == "准备供应商上报规则"
    assert tool["department"] == "purchase"
    assert tool["type"] == "operation"
    assert tool["mode"] == "human_confirmed_proposal"
    skill = capability_descriptor("SKILL", "full_outsource_review", SKILLS["full_outsource_review"])
    assert "prepare_supplier_progress_policy" in skill["optional_dependencies"]


def test_delivery_logistics_skill_exposes_separate_route_and_price_authorities():
    route = capability_descriptor("TOOL", "prepare_logistics_route", TOOLS["prepare_logistics_route"])
    assert route["name"] == "准备物流路线确认"
    assert route["department"] == "warehouse"
    assert route["type"] == "operation"
    assert route["mode"] == "human_confirmed_proposal"
    quote = capability_descriptor("TOOL", "prepare_logistics_quote", TOOLS["prepare_logistics_quote"])
    assert quote["name"] == "准备物流报价/结算价确认"
    assert quote["department"] == "purchase"
    assert quote["type"] == "approval"
    assert quote["mode"] == "human_confirmed_proposal"
    skill = capability_descriptor("SKILL", "delivery_logistics_review", SKILLS["delivery_logistics_review"])
    assert skill["dependencies"] == ["query_delivery_logistics_context"]
    assert skill["optional_dependencies"] == ["prepare_logistics_route", "prepare_logistics_quote"]


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


def test_project_kickoff_skill_starts_with_one_coordinator_and_keeps_stage_tools_optional():
    tool = capability_descriptor("TOOL", "query_project_kickoff_context", TOOLS["query_project_kickoff_context"])
    assert tool["name"] == "读取项目启动链路"
    assert tool["department"] == "project"
    assert tool["type"] == "query"
    assert tool["mode"] == "read_only"

    skill = capability_descriptor("SKILL", "project_kickoff_orchestration", SKILLS["project_kickoff_orchestration"])
    assert skill["name"] == "项目启动链路协调"
    assert skill["department"] == "project"
    assert skill["type"] == "review"
    assert skill["dependencies"] == ["query_project_kickoff_context"]
    assert skill["activation_dependencies"] == ["query_project_kickoff_context"]
    assert {
        "query_quote_acceptance_context",
        "prepare_quote_acceptance_decision",
        "query_contract_context",
        "prepare_contract_record",
        "query_internal_start_readiness",
        "prepare_internal_start",
        "query_project_plan_context",
        "prepare_project_plan_baseline",
    } == set(skill["optional_dependencies"])


def test_project_execution_skill_starts_with_one_coordinator_and_keeps_stage_reads_optional():
    tool = capability_descriptor("TOOL", "query_project_execution_context", TOOLS["query_project_execution_context"])
    assert tool["name"] == "读取项目执行链路"
    assert tool["department"] == "project"
    assert tool["type"] == "query"
    assert tool["mode"] == "read_only"

    skill = capability_descriptor("SKILL", "project_execution_orchestration", SKILLS["project_execution_orchestration"])
    assert skill["name"] == "项目执行链路协调"
    assert skill["department"] == "project"
    assert skill["type"] == "review"
    assert skill["dependencies"] == ["query_project_execution_context"]
    assert skill["activation_dependencies"] == ["query_project_execution_context"]
    assert {
        "query_project_plan_context",
        "query_design_route_context",
        "query_procurement_price_context",
        "query_full_outsource_context",
        "query_manufacturing_quality_context",
        "query_assembly_trial_context",
        "query_delivery_logistics_context",
    } == set(skill["optional_dependencies"])


def test_project_completion_skill_starts_with_one_coordinator_and_keeps_stage_actions_optional():
    tool = capability_descriptor("TOOL", "query_project_completion_context", TOOLS["query_project_completion_context"])
    assert tool["name"] == "读取项目收尾链路"
    assert tool["department"] == "project"
    assert tool["type"] == "query"
    assert tool["mode"] == "read_only"

    skill = capability_descriptor("SKILL", "project_completion_orchestration", SKILLS["project_completion_orchestration"])
    assert skill["name"] == "项目收尾链路协调"
    assert skill["department"] == "project"
    assert skill["type"] == "review"
    assert skill["dependencies"] == ["query_project_completion_context"]
    assert skill["activation_dependencies"] == ["query_project_completion_context"]
    assert {
        "query_delivery_logistics_context",
        "query_finance_context",
            "query_project_closure_context",
            "prepare_customer_receivable_schedule",
            "prepare_customer_receipt_confirmation",
        "prepare_supplier_payment_confirmation",
        "prepare_supplier_deduction_settlement",
        "prepare_project_closure_checklist",
        "prepare_project_closure_item",
        "prepare_project_normal_close",
        "prepare_project_settlement_close",
    } == set(skill["optional_dependencies"])


def test_erp_design_tools_expose_curated_chinese_titles():
    assert set(TOOL_NAMES) == {key for key in TOOLS if key.startswith("erp_design_")}
    for key, title in TOOL_NAMES.items():
        assert capability_descriptor("TOOL", key, TOOLS[key])["name"] == title


def test_erp_design_skills_are_grouped_with_design_tools():
    keys = [
        "erp_new_mold_design_upload",
        "erp_design_modify_mold_upload",
        "erp_design_price_calculation",
        "erp_design_tolerance_evaluation",
        "erp_design_drawing_preview",
        "erp_design_drawing_auto_correction",
        "erp_design_workspace_review",
        "erp_design_order_adjustment",
        "erp_design_master_data_maintenance",
        "erp_design_standard_hardware_maintenance",
        "erp_design_change_management",
        "erp_design_order_lifecycle",
        "erp_design_mold_repair",
        "erp_design_bom_maintenance",
    ]
    for key in keys:
        item = capability_descriptor("SKILL", key, SKILLS[key])
        assert item["department"] == "design"
        assert item["department_name"] == "设计部门"
        assert item["type"] == "review"


def test_new_mold_attachment_upload_activates_only_the_parser_initially():
    item = capability_descriptor("SKILL", "erp_new_mold_design_upload",
                                 SKILLS["erp_new_mold_design_upload"])
    assert item["activation_dependencies"] == ["erp_design_parse_new_mold_upload"]


def test_modify_mold_upload_skill_is_separate_from_dxf_mold_repair():
    skill = SKILLS["erp_design_modify_mold_upload"]
    item = capability_descriptor("SKILL", "erp_design_modify_mold_upload", skill)

    assert skill["name"] == "ERP 修模改模清单上传流程（类型：改模）"
    assert item["activation_dependencies"] == ["erp_design_parse_modify_mold_upload"]
    assert {
        "erp_design_parse_modify_mold_upload",
        "erp_design_get_modify_mold_approval_config",
        "erp_design_import_modify_mold",
    } <= set(skill["tools"] + skill["optional_tools"])
    assert "erp_design_upload_mold_repair_drawing" not in skill["tools"] + skill["optional_tools"]
    assert {"上传改模钢料清单", "上传时选择改模", "修模改模清单上传"} <= set(
        skill["activation_queries"]
    )


def test_new_mold_upload_exposes_existing_pricing_and_drawing_preview_tools():
    skill = SKILLS["erp_new_mold_design_upload"]
    assert {"erp_design_reprice_rows", "erp_design_download_file", "erp_design_preview_drawing",
            "erp_design_auto_correct_rows", "erp_design_evaluate_tolerances"} <= set(skill["optional_tools"])
    assert {"核算价格", "价格核算", "图纸预览", "预览图纸", "查看订单明细"} <= set(skill["activation_queries"])
    assert {"自动修正参数", "按图纸修正", "修正数量", "修正长宽厚"} <= set(skill["activation_queries"])


def test_price_preview_and_auto_correction_are_explicit_design_capabilities():
    expected_tools = {
        "erp_design_reprice_rows": ("核算 ERP 设计钢料价格", "operation"),
        "erp_design_preview_drawing": ("预览 ERP 设计上传图纸", "query"),
        "erp_design_auto_correct_rows": ("按图纸自动修正清单参数", "operation"),
        "erp_design_evaluate_tolerances": ("判断 ERP 设计钢料公差", "query"),
    }
    for key, (name, capability_type) in expected_tools.items():
        item = capability_descriptor("TOOL", key, TOOLS[key])
        assert item["name"] == name
        assert item["department"] == "design"
        assert item["type"] == capability_type

    price = SKILLS["erp_design_price_calculation"]
    preview = SKILLS["erp_design_drawing_preview"]
    correction = SKILLS["erp_design_drawing_auto_correction"]
    tolerance = SKILLS["erp_design_tolerance_evaluation"]
    assert "erp_design_reprice_rows" in price["tools"]
    assert "算价格" in price["activation_queries"]
    assert "erp_design_preview_drawing" in preview["tools"]
    assert "看图纸" in preview["activation_queries"]
    assert "erp_design_auto_correct_rows" in correction["tools"]
    assert {"自动修正", "修正数量", "修正长宽厚"} <= set(correction["activation_queries"])
    assert tolerance["tools"] == ["erp_design_evaluate_tolerances"]
    assert {"判断公差", "公差档位", "对角公差"} <= set(tolerance["activation_queries"])


def test_erp_design_workspace_has_material_list_aliases_and_mold_priority():
    skill = SKILLS["erp_design_workspace_review"]
    assert {"设计与物料清单", "钢料清单", "五金清单", "模具物料"} <= set(skill["activation_queries"])
    assert {"查看设计订单", "打开设计订单", "设计订单表格", "设计订单可视化"} <= set(skill["activation_queries"])
    assert skill["priority_patterns"] == [r"(?i)(?<![A-Z0-9])M\d{5,}-P\d+(?![A-Z0-9])"]
    assert skill["optional_tools"] == ["erp_design_query_bom", "erp_design_query_bom_report"]


def test_erp_drawing_versions_require_an_explicit_drawing_version_intent():
    skill = SKILLS["erp_design_drawing_version_review"]
    assert skill["tools"] == ["erp_design_query_drawing_versions"]
    assert {"查询图纸版本", "查看图纸版本", "图纸版本对比"} <= set(skill["activation_queries"])


def test_design_mold_repair_skill_exposes_dedicated_erp_operations():
    dedicated = {
        "erp_design_upload_mold_repair_drawing",
        "erp_design_confirm_mold_repair_quantity",
        "erp_design_submit_mold_repair_approval_batches",
        "erp_design_confirm_mold_repair_order_link",
        "erp_design_respond_mold_repair_processor",
    }
    skill = SKILLS["erp_design_mold_repair"]

    assert skill["name"] == "ERP 设计修模改模图纸办理"
    assert dedicated <= set(skill["optional_tools"])
    assert dedicated <= set(skill["activation_tools"])
    assert "erp_design_manage_mold_repair" not in skill["activation_tools"]
    assert {"设计修模", "设计改模", "确认新图数量", "同意改图", "已加工反馈"} <= set(
        skill["activation_queries"]
    )

    for tool in dedicated:
        item = capability_descriptor("TOOL", tool, TOOLS[tool])
        assert item["department"] == "design"
        assert item["type"] == "operation"
