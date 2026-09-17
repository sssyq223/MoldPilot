from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as m
from app.models import Base
from app.tool_gateway import SKILLS, TOOLS, execute, skill_context, tool_schema


def test_erp_design_mcp_read_tool_is_registered_and_forwarded(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    calls = []

    def fake_call(name, arguments):
        calls.append((name, arguments))
        return {"rows": [{"moldNo": "M250238-P4"}], "total": 1}

    monkeypatch.setattr("domain_packs.mold.erp_design_mcp.call_mcp", fake_call)
    try:
        with Session.begin() as db:
            user = m.User(username="erp_design_admin", display_name="ERP 设计管理员", password_hash="test", super_admin=True)
            db.add(user)
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_design_admin").one()
            result = execute(db, user, "erp_design_query_bom", {"query": {"moldNo": "M250238-P4"}})
            repair = execute(db, user, "erp_design_get_mold_repair_approval", {"batch_id": 18})
            skills = {item["key"] for item in skill_context(db, user)}
        assert calls == [
            ("query_erp_bom", {"query": {"moldNo": "M250238-P4"}}),
            ("get_erp_mold_repair_approval", {"batchId": 18}),
        ]
        assert result["data"]["rows"][0]["moldNo"] == "M250238-P4"
        assert repair["data"]["total"] == 1
        assert result["source"] == "management-system ERP via erp-design-upload MCP"
        assert "erp_design_parse_new_mold_upload" in TOOLS
        assert "erp_design_analyze_change" in TOOLS
        assert "erp_design_get_mold_repair_processor_response" in TOOLS
        assert "erp_new_mold_design_upload" in SKILLS
        assert "erp_design_workspace_review" in SKILLS
        assert {"erp_new_mold_design_upload", "erp_design_workspace_review"} <= skills
        assert tool_schema("erp_design_query_bom")["function"]["name"] == "erp_design_query_bom"
    finally:
        engine.dispose()


def test_erp_design_control_tools_are_registered_and_forwarded(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    calls = []

    def fake_call(name, arguments):
        calls.append((name, arguments))
        return {"accepted": True}

    monkeypatch.setattr("domain_packs.mold.erp_design_mcp.call_design_control_mcp", fake_call)
    try:
        with Session.begin() as db:
            user = m.User(username="erp_design_control_admin", display_name="ERP 设计管理员", password_hash="test", super_admin=True)
            db.add(user)
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_design_control_admin").one()
            density = execute(db, user, "erp_design_create_density", {
                "material_mark": "P20", "density": "7.85", "confirm": True,
            })
            item = execute(db, user, "erp_design_update_order_item", {
                "detail_id": 21, "material_mark": "S136", "specification": "100×80×50",
                "modify_reason": "按已确认图纸修订", "confirm": True,
            })
            rule = execute(db, user, "erp_design_toggle_group_rule", {
                "id": 9, "status": "inactive", "confirm": True,
            })
            hardware = execute(db, user, "erp_design_rename_standard_hardware", {
                "relative_path": "DME/导柱/GP-01.dxf", "new_file_name": "GP-01-修订.dxf", "confirm": True,
            })
            skills = {item["key"] for item in skill_context(db, user)}
        assert calls == [
            ("create_design_density", {"density": {"materialMark": "P20", "density": "7.85"}}),
            ("update_design_order_item", {"detailId": 21, "materialMark": "S136", "specification": "100×80×50", "modifyReason": "按已确认图纸修订"}),
            ("toggle_design_group_rule", {"ruleId": 9, "status": "inactive"}),
            ("rename_standard_hardware_drawing", {"relativePath": "DME/导柱/GP-01.dxf", "newFileName": "GP-01-修订.dxf"}),
        ]
        assert density["data"]["accepted"] is True
        assert item["data"]["accepted"] is True
        assert rule["data"]["accepted"] is True
        assert hardware["data"]["accepted"] is True
        assert {"erp_design_order_adjustment", "erp_design_master_data_maintenance", "erp_design_standard_hardware_maintenance"} <= skills
        assert tool_schema("erp_design_delete_density")["function"]["parameters"]["properties"]["confirm"]["const"] is True
    finally:
        engine.dispose()


def test_erp_design_missing_operations_are_registered_and_forwarded(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    calls = []

    def fake_call(name, arguments):
        calls.append((name, arguments))
        return {"accepted": True}

    monkeypatch.setattr("domain_packs.mold.erp_design_mcp.call_design_control_mcp", fake_call)
    try:
        with Session.begin() as db:
            user = m.User(username="erp_design_full_admin", display_name="ERP 设计管理员", password_hash="test", super_admin=True)
            db.add(user)
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_design_full_admin").one()
            change = execute(db, user, "erp_design_manage_change", {
                "operation": "submit", "change_id": 9, "confirm": True,
            })
            items = execute(db, user, "erp_design_manage_change_items", {
                "operation": "batch_create", "payload": {"changeId": 9, "items": [{"itemType": 1}]}, "confirm": True,
            })
            repair = execute(db, user, "erp_design_manage_mold_repair", {
                "operation": "confirm_quantity", "exception_id": 33, "payload": {"newQty": 2}, "confirm": True,
            })
            shortage = execute(db, user, "erp_design_query_bom_shortage", {"mold_id": 7, "part_id": 3})
            bom = execute(db, user, "erp_design_manage_bom", {
                "operation": "delete", "bom_ids": [4, 5], "confirm": True,
            })
        assert calls == [
            ("manage_design_change", {"operation": "submit", "changeId": 9, "changeIds": None, "payload": None}),
            ("manage_design_change_items", {"operation": "batch_create", "changeId": None, "itemIds": None, "payload": {"changeId": 9, "items": [{"itemType": 1}]}}),
            ("manage_mold_repair", {"operation": "confirm_quantity", "exceptionId": 33, "batchId": None, "groupToken": None, "orderId": None, "payload": {"newQty": 2}}),
            ("get_erp_bom_shortage", {"moldId": 7, "partId": 3}),
            ("manage_erp_bom", {"operation": "delete", "bomIds": [4, 5], "payload": None}),
        ]
        assert all(result["data"]["accepted"] is True for result in [change, items, repair, shortage, bom])
        assert tool_schema("erp_design_download_file")["function"]["parameters"]["properties"]["artifact"]["enum"] == [
            "drawing_preview", "drawing_download", "standard_hardware_preview", "standard_hardware_folder",
            "mold_repair_outsource_approval_drawing", "mold_repair_approval_drawing",
            "mold_repair_entrust_order_drawing", "mold_repair_exception_drawing", "mold_repair_group_drawing",
            "mold_repair_authorized_package", "bom_export",
        ]
    finally:
        engine.dispose()
