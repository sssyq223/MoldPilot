from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.errors import DomainError
from app import models as m
from app.models import Base
from app.tool_gateway import SKILLS, TOOLS, execute, skill_context, tool_schema


def test_new_mold_design_upload_accepts_csv_attachment(monkeypatch, tmp_path):
    from domain_packs.mold import erp_design_mcp

    content = '零件编号,名称\nU2-02,上模座\n'.encode('utf-8')
    source = SimpleNamespace(id='file-1', filename='M250238-P4钢料.csv')
    db = SimpleNamespace(scalars=lambda _statement: ['file-1'])
    user = SimpleNamespace(id='user-1')
    run = SimpleNamespace(id='run-1', user_id='user-1')
    directory = tmp_path / 'design-upload'

    monkeypatch.setattr(erp_design_mcp.files, 'uploaded_file', lambda *_args: source)
    monkeypatch.setattr(erp_design_mcp.object_storage, 'read', lambda _source: content)
    monkeypatch.setattr(erp_design_mcp.tempfile, 'mkdtemp', lambda **_kwargs: str(directory))
    directory.mkdir()

    result_dir, path, selected = erp_design_mcp._temporary_design_file(db, user, run, None)

    assert result_dir == directory
    assert selected is source
    assert path.name == 'M250238-P4钢料.csv'
    assert path.read_bytes() == content


def test_new_mold_design_upload_accepts_legacy_xls_attachment(monkeypatch, tmp_path):
    from domain_packs.mold import erp_design_mcp

    content = b'legacy excel workbook'
    source = SimpleNamespace(id='file-xls', filename='M250238-P4钢料.xls')
    db = SimpleNamespace(scalars=lambda _statement: ['file-xls'])
    user = SimpleNamespace(id='user-1')
    run = SimpleNamespace(id='run-1', user_id='user-1')
    directory = tmp_path / 'design-upload-xls'

    monkeypatch.setattr(erp_design_mcp.files, 'uploaded_file', lambda *_args: source)
    monkeypatch.setattr(erp_design_mcp.object_storage, 'read', lambda _source: content)
    monkeypatch.setattr(erp_design_mcp.files, 'validate_file', lambda filename, _data: (filename, 'application/vnd.ms-excel'))
    monkeypatch.setattr(erp_design_mcp.tempfile, 'mkdtemp', lambda **_kwargs: str(directory))
    directory.mkdir()

    result_dir, path, selected = erp_design_mcp._temporary_design_file(db, user, run, None)

    assert result_dir == directory
    assert selected is source
    assert path.name == 'M250238-P4钢料.xls'
    assert path.read_bytes() == content


def test_new_mold_parse_defaults_to_erp_auto_type_detection(monkeypatch, tmp_path):
    from domain_packs.mold import erp_design_mcp

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    calls = []
    directory = tmp_path / "auto-detect-upload"
    directory.mkdir()
    path = directory / "M250238-P4-五金请购单66.xlsx"
    path.write_bytes(b"synthetic workbook")
    source = SimpleNamespace(id=uuid4(), filename=path.name)

    def fake_control_call(name, arguments):
        calls.append((name, arguments))
        return {"sessionId": 321, "sheetType": "hardware", "previewRows": []}

    monkeypatch.setattr(erp_design_mcp, "_temporary_design_file",
                        lambda *_args: (directory, path, source))
    monkeypatch.setattr(erp_design_mcp, "call_design_control_mcp", fake_control_call)
    try:
        with Session.begin() as db:
            user = m.User(username="erp_auto_detect_admin", display_name="ERP 设计管理员",
                          password_hash="test", super_admin=True)
            db.add(user)
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_auto_detect_admin").one()
            run = SimpleNamespace(id="run-auto-detect", user_id=user.id)
            result = execute(db, user, "erp_design_parse_new_mold_upload", {}, run=run)
            event = db.query(m.AuditEvent).filter_by(
                action="erp_design_mcp.parsed", resource_id="321").one()

        assert calls == [("parse_new_mold_design_file_auto", {
            "filePath": str(path), "sheetType": "auto", "designOrderSubType": None,
        })]
        assert result["data"]["sheetType"] == "hardware"
        assert event.detail["sheet_type"] == "hardware"
    finally:
        engine.dispose()


def test_modify_mold_parse_fixes_erp_business_type_to_repair_other(monkeypatch, tmp_path):
    from domain_packs.mold import erp_design_mcp

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    calls = []
    directory = tmp_path / "modify-mold-upload"
    directory.mkdir()
    path = directory / "M250238-P4改模钢料.xlsx"
    path.write_bytes(b"synthetic workbook")
    source = SimpleNamespace(id=uuid4(), filename=path.name)

    monkeypatch.setattr(erp_design_mcp, "_temporary_design_file",
                        lambda *_args: (directory, path, source))
    monkeypatch.setattr(
        erp_design_mcp,
        "call_design_control_mcp",
        lambda name, arguments: calls.append((name, arguments)) or {
            "sessionId": 322, "sheetType": "steel", "previewRows": [],
        },
    )
    try:
        with Session.begin() as db:
            db.add(m.User(username="erp_modify_parse_admin", display_name="ERP 改模上传管理员",
                          password_hash="test", super_admin=True))
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_modify_parse_admin").one()
            run = SimpleNamespace(id="run-modify-parse", user_id=user.id)
            result = execute(db, user, "erp_design_parse_modify_mold_upload", {}, run=run)
            event = db.query(m.AuditEvent).filter_by(
                action="erp_design_mcp.parsed", resource_id="322").one()

        assert calls == [("parse_modify_mold_design_file_auto", {
            "filePath": str(path), "sheetType": "auto",
        })]
        assert result["data"]["sheetType"] == "steel"
        assert event.detail["design_order_type"] == "repair_other"
        assert event.detail["sheet_type"] == "steel"
    finally:
        engine.dispose()


def test_modify_mold_config_and_import_use_dedicated_erp_flow(monkeypatch):
    from app.events import record
    from domain_packs.mold import erp_design_mcp

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    read_calls = []
    control_calls = []

    def fake_read(name, arguments):
        read_calls.append((name, arguments))
        return {"canImport": True, "errors": []}

    def fake_control(name, arguments):
        control_calls.append((name, arguments))
        if name == "get_modify_mold_approval_launch_config":
            return {"processCode": "design_modify_model_approval"}
        return {"requestNo": "PR2026091804012", "processName": "设计修改模审批"}

    monkeypatch.setattr(erp_design_mcp, "call_mcp", fake_read)
    monkeypatch.setattr(erp_design_mcp, "call_design_control_mcp", fake_control)
    rows = [{"rowIndex": 1, "material_name": "CR12MOV", "qty": 1}]
    try:
        with Session.begin() as db:
            db.add(m.User(username="erp_modify_import_admin", display_name="ERP 改模导入管理员",
                          password_hash="test", super_admin=True))
        with Session.begin() as db:
            user = db.query(m.User).filter_by(username="erp_modify_import_admin").one()
            record(db, user, "erp_design_mcp.parsed", "322", {
                "sheet_type": "steel", "design_order_type": "repair_other",
            })
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_modify_import_admin").one()
            config = execute(db, user, "erp_design_get_modify_mold_approval_config", {
                "session_id": 322,
            })
            imported = execute(db, user, "erp_design_import_modify_mold", {
                "session_id": 322,
                "sheet_type": "steel",
                "mold_code": "M250238-P4",
                "preview_rows": rows,
                "confirm_import": True,
                "expected_date": "2026-09-21",
                "purchase_reason": "design_abnormal",
                "remark": "设计异常改模",
            })

        assert config["data"]["processCode"] == "design_modify_model_approval"
        assert imported["data"]["processName"] == "设计修改模审批"
        assert read_calls == [("validate_new_mold_design_rows", {
            "sessionId": 322,
            "sheetType": "steel",
            "moldCode": "M250238-P4",
            "previewRows": rows,
            "pricingAlreadyEnriched": True,
        })]
        assert control_calls == [
            ("get_modify_mold_approval_launch_config", {"sessionId": 322}),
            ("import_modify_mold_design", {
                "sessionId": 322,
                "sheetType": "steel",
                "moldCode": "M250238-P4",
                "previewRows": rows,
                "urgencyLevel": "normal",
                "expectedDate": "2026-09-21",
                "purchaseReason": "design_abnormal",
                "remark": "设计异常改模",
                "allowDuplicate": False,
            }),
        ]
        schema = tool_schema("erp_design_import_modify_mold")["function"]["parameters"]
        assert schema["properties"]["purchase_reason"]["enum"] == [
            "customer_change", "design_abnormal", "machining_abnormal", "assembly_abnormal",
            "trial_mold_abnormal", "outsource_abnormal", "process_improvement", "other_abnormal",
        ]
        assert schema["properties"]["confirm_import"]["const"] is True
    finally:
        engine.dispose()


def test_modify_mold_import_rejects_missing_purchase_reason():
    with pytest.raises(DomainError) as caught:
        from domain_packs.mold import erp_design_mcp
        erp_design_mcp.execute_tool(SimpleNamespace(), SimpleNamespace(), "erp_design_import_modify_mold", {
            "session_id": 1,
            "sheet_type": "hardware",
            "mold_code": "M250238-P4",
            "preview_rows": [{"rowIndex": 1}],
            "confirm_import": True,
            "expected_date": "2026-09-21",
        })
    assert caught.value.code == "INVALID_TOOL_INPUT"


def test_http_bridge_accepts_agent_created_upload_session():
    from app.events import record
    from domain_packs.mold.erp.design import erp_design_upload

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    try:
        with Session.begin() as db:
            user = m.User(username="erp_preview_admin", display_name="ERP 预览管理员",
                          password_hash="test", super_admin=True)
            db.add(user)
        with Session.begin() as db:
            user = db.query(m.User).filter_by(username="erp_preview_admin").one()
            record(db, user, "erp_design_mcp.parsed", "271", {"sheet_type": "hardware"})
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_preview_admin").one()
            event = erp_design_upload._owned_session(db, user, 271)
            assert event.action == "erp_design_mcp.parsed"
    finally:
        engine.dispose()


def test_http_bridge_proxies_only_a_drawing_from_the_owned_upload(monkeypatch):
    from domain_packs.mold.erp.design import erp_design_upload

    calls = []
    monkeypatch.setattr(erp_design_upload, "require", lambda *_args: None)
    monkeypatch.setattr(erp_design_upload, "_owned_session", lambda *_args: None)
    monkeypatch.setattr(erp_design_upload, "call_mcp", lambda name, arguments: {
        "previewRows": [{
            "drawing_resource_id": 1185,
            "drawing_preview_url": "/purchase/drawing/resource/1185/preview",
        }],
    })
    monkeypatch.setattr(erp_design_upload.erp_design_mcp, "call_design_control_mcp",
                        lambda name, arguments: calls.append((name, arguments)) or {
                            "base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
                            "mediaType": "image/png", "fileName": "A01.png",
                        })

    response = erp_design_upload.drawing_preview(
        271, 1185, user=SimpleNamespace(id="user-1"), db=SimpleNamespace())

    assert response.body.startswith(b"\x89PNG")
    assert response.media_type == "image/png"
    assert calls == [("download_erp_design_file", {
        "artifact": "drawing_preview", "drawingId": 1185,
        "previewUrl": "/purchase/drawing/resource/1185/preview",
    })]


def test_http_bridge_rejects_drawing_outside_the_owned_upload(monkeypatch):
    from domain_packs.mold.erp.design import erp_design_upload
    from app.errors import DomainError

    monkeypatch.setattr(erp_design_upload, "require", lambda *_args: None)
    monkeypatch.setattr(erp_design_upload, "_owned_session", lambda *_args: None)
    monkeypatch.setattr(erp_design_upload, "call_mcp", lambda *_args: {
        "previewRows": [{"drawing_resource_id": 1185}],
    })

    try:
        erp_design_upload.drawing_preview(
            271, 9999, user=SimpleNamespace(id="user-1"), db=SimpleNamespace())
        assert False, "expected drawing ownership rejection"
    except DomainError as error:
        assert error.code == "ERP_DRAWING_NOT_IN_SESSION"


def test_agent_drawing_preview_tool_uses_the_owned_session_row(monkeypatch):
    from app.events import record
    from domain_packs.mold import erp_design_mcp

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    mcp_calls = []
    control_calls = []

    def fake_mcp(name, arguments):
        mcp_calls.append((name, arguments))
        return {"previewRows": [{
            "drawing_resource_id": 1190,
            "drawing_preview_url": "/purchase/drawing/resource/1190/preview",
        }]}

    monkeypatch.setattr(erp_design_mcp, "call_mcp", fake_mcp)
    monkeypatch.setattr(erp_design_mcp, "call_design_control_mcp",
                        lambda name, arguments: control_calls.append((name, arguments)) or {"base64": "aW1hZ2U="})
    monkeypatch.setattr(erp_design_mcp, "_store_erp_download",
                        lambda _db, _user, _run, value: {"file": {"filename": "C04.png"}, "raw": value})
    try:
        with Session.begin() as db:
            user = m.User(username="erp_drawing_tool_admin", display_name="ERP 图纸管理员",
                          password_hash="test", super_admin=True)
            db.add(user)
        with Session.begin() as db:
            user = db.query(m.User).filter_by(username="erp_drawing_tool_admin").one()
            record(db, user, "erp_design_mcp.parsed", "271", {"sheet_type": "hardware"})
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_drawing_tool_admin").one()
            run = SimpleNamespace(user_id=user.id)
            result = execute(db, user, "erp_design_preview_drawing", {
                "session_id": 271, "drawing_id": 1190,
            }, run=run)
        assert result["data"]["file"]["filename"] == "C04.png"
        assert mcp_calls == [("get_new_mold_upload_status", {"sessionId": 271, "includeResult": True})]
        assert control_calls == [("download_erp_design_file", {
            "artifact": "drawing_preview", "drawingId": 1190,
            "previewUrl": "/purchase/drawing/resource/1190/preview",
        })]
    finally:
        engine.dispose()


def test_agent_auto_correction_replaces_shape_dimensions_quantity_and_reprices_steel(monkeypatch):
    from app.events import record
    from domain_packs.mold import erp_design_mcp

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    calls = []

    def fake_mcp(name, arguments):
        calls.append((name, arguments))
        return {"previewRows": arguments["previewRows"], "pricingAlreadyEnriched": True}

    monkeypatch.setattr(erp_design_mcp, "call_mcp", fake_mcp)
    try:
        with Session.begin() as db:
            user = m.User(username="erp_correct_tool_admin", display_name="ERP 修正管理员",
                          password_hash="test", super_admin=True)
            db.add(user)
        with Session.begin() as db:
            user = db.query(m.User).filter_by(username="erp_correct_tool_admin").one()
            record(db, user, "erp_design_mcp.parsed", "272", {"sheet_type": "steel"})
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_correct_tool_admin").one()
            result = execute(db, user, "erp_design_auto_correct_rows", {
                "session_id": 272,
                "sheet_type": "steel",
                "mold_code": "M250238-P4",
                "preview_rows": [{
                    "rowIndex": 2, "material_type": "方料", "material_shape": "方料",
                    "length": 100, "width": 60, "height": 20, "qty": 2, "density": 7.85,
                    "drawing_material_shape": "圆料",
                    "drawing_dimension_value": {"outer_diameter": 25.273, "height": 67.287},
                    "drawing_quantity": 5,
                    "drawing_material_shape_match": False,
                    "drawing_dimension_match": False,
                    "drawing_quantity_match": False,
                }],
            })

        corrected = result["data"]["previewRows"][0]
        assert corrected["material_type"] == "圆料"
        assert corrected["material_shape"] == "圆料"
        assert corrected["outer_diameter"] == 25.273
        assert corrected["height"] == 67.287
        assert corrected["length"] is None and corrected["width"] is None
        assert corrected["qty"] == 5 and corrected["quantity"] == 5
        assert corrected["drawing_material_shape_match"] is True
        assert corrected["drawing_dimension_match"] is True
        assert corrected["drawing_quantity_match"] is True
        assert result["data"]["correctedCount"] == 1
        assert result["data"]["correctedFields"] == {"material_shape": 1, "dimensions": 1, "quantity": 1}
        assert calls[0][0] == "reprice_new_mold_design_rows"
        assert calls[0][1]["sheetType"] == "steel"
    finally:
        engine.dispose()


def test_agent_auto_correction_keeps_hardware_on_existing_erp_price_fields():
    from domain_packs.mold import erp_design_mcp

    rows, corrected_count, fields = erp_design_mcp._auto_correct_rows([{
        "rowIndex": 3,
        "material_type": "标准五金",
        "qty": 2,
        "approved_unit_price": 12.5,
        "drawing_quantity": 4,
        "drawing_quantity_match": False,
    }], "hardware", [])

    assert corrected_count == 1
    assert fields == {"material_shape": 0, "dimensions": 0, "quantity": 1}
    assert rows[0]["qty"] == 4 and rows[0]["quantity"] == 4
    assert rows[0]["purchase_quantity"] == 4
    assert rows[0]["total_price"] == 50
    assert rows[0]["drawing_quantity_match"] is True


def test_agent_tolerance_tool_reads_erp_rules_and_evaluates_rows_in_one_call(monkeypatch):
    from app.events import record
    from domain_packs.mold import erp_design_mcp

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    calls = []

    def fake_mcp(name, arguments):
        calls.append((name, arguments))
        return {
            "sessionId": 273,
            "sheetType": "steel",
            "previewRows": [
                {"rowIndex": 1, "material_shape": "方料", "length": 706.526, "width": 705.79, "height": 70.46},
                {"rowIndex": 2, "material_shape": "圆料", "outer_diameter": 80, "height": 30},
            ],
            "techRequirements": {"tolerance_table": [
                {"seq": 1, "spec": "500(含)以下", "length_tol": "+0.3~+0.6", "thick_tol": "+0.3~+0.5", "diag_tol": "0~0.5"},
                {"seq": 2, "spec": "500-800(含)", "length_tol": "+0.3~+0.8", "thick_tol": "+0.6~+0.9", "diag_tol": "0~0.5"},
                {"seq": 3, "spec": "800以上", "length_tol": "+0.3~+1.0", "thick_tol": "+0.7~+1.0", "diag_tol": "0~0.5"},
            ]},
        }

    monkeypatch.setattr(erp_design_mcp, "call_mcp", fake_mcp)
    try:
        with Session.begin() as db:
            db.add(m.User(username="erp_tolerance_admin", display_name="ERP 公差管理员",
                          password_hash="test", super_admin=True))
        with Session.begin() as db:
            user = db.query(m.User).filter_by(username="erp_tolerance_admin").one()
            record(db, user, "erp_design_mcp.parsed", "273", {"sheet_type": "steel"})
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_tolerance_admin").one()
            result = execute(db, user, "erp_design_evaluate_tolerances", {"session_id": 273})

        assert calls == [("get_new_mold_upload_result", {"sessionId": 273})]
        square, round_stock = result["data"]["previewRows"]
        assert square["toleranceTier"] == "500-800(含)"
        assert square["lengthAllowedRange"] == "706.826～707.326"
        assert square["widthAllowedRange"] == "706.09～706.59"
        assert square["thicknessAllowedRange"] == "71.06～71.36"
        assert square["diagonalTolerance"] == "0~0.5"
        assert round_stock["toleranceTier"] == "-"
        assert result["data"]["toleranceEvaluation"] == {
            "evaluatedCount": 1,
            "rowCount": 2,
            "ruleCount": 3,
            "source": "ERP techRequirements.tolerance_table",
        }
    finally:
        engine.dispose()


def test_erp_design_mcp_read_tool_is_registered_and_forwarded(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    calls = []

    def fake_call(name, arguments):
        calls.append((name, arguments))
        return {"rows": [{"moldNo": "M250238-P4"}], "total": 1}

    monkeypatch.setattr("domain_packs.mold.tools.erp.design.erp_design_mcp.call_mcp", fake_call)
    try:
        with Session.begin() as db:
            user = m.User(username="erp_design_admin", display_name="ERP 设计管理员", password_hash="test", super_admin=True)
            db.add(user)
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_design_admin").one()
            order = execute(db, user, "erp_design_query_orders", {"query": " M250238-P4 "})
            result = execute(db, user, "erp_design_query_bom", {"query": {"moldNo": "M250238-P4"}})
            repair = execute(db, user, "erp_design_get_mold_repair_approval", {"batch_id": 18})
            skills = {item["key"] for item in skill_context(db, user)}
        assert calls == [
            ("query_erp_design_orders", {"query": {"moldNo": "M250238-P4"}}),
            ("query_erp_bom", {"query": {"moldNo": "M250238-P4"}}),
            ("get_erp_mold_repair_approval", {"batchId": 18}),
        ]
        assert order["data"]["rows"][0]["moldNo"] == "M250238-P4"
        assert result["data"]["rows"][0]["moldNo"] == "M250238-P4"
        assert repair["data"]["total"] == 1
        assert result["source"] == "management-system ERP via erp-design-upload MCP"
        assert "erp_design_parse_new_mold_upload" in TOOLS
        assert "erp_design_analyze_change" in TOOLS
        assert "erp_design_get_mold_repair_processor_response" in TOOLS
        assert "erp_new_mold_design_upload" in SKILLS
        assert "erp_design_modify_mold_upload" in SKILLS
        assert "erp_design_workspace_review" in SKILLS
        assert {"erp_new_mold_design_upload", "erp_design_modify_mold_upload", "erp_design_workspace_review",
                "erp_design_price_calculation", "erp_design_drawing_preview",
                "erp_design_drawing_auto_correction", "erp_design_tolerance_evaluation"} <= skills
        assert tool_schema("erp_design_query_bom")["function"]["name"] == "erp_design_query_bom"
    finally:
        engine.dispose()


def test_erp_design_master_data_query_aggregates_the_related_read_catalogues(monkeypatch):
    from domain_packs.mold import erp_design_mcp

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    calls = []

    def fake_call(name, arguments):
        calls.append((name, arguments))
        return {"rows": [{"source": name}]}

    monkeypatch.setattr(erp_design_mcp, "call_mcp", fake_call)
    try:
        with Session.begin() as db:
            db.add(m.User(username="erp_master_data_admin", display_name="ERP 基础资料管理员",
                          password_hash="test", super_admin=True))
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_master_data_admin").one()
            result = execute(db, user, "erp_design_query_master_data", {
                "material_mark": "CR12MOV",
            })

        assert calls == [
            ("query_erp_design_densities", {"query": {"materialMark": "CR12MOV"}}),
            ("query_erp_design_group_rules", {"query": {"materialMark": "CR12MOV"}}),
            ("query_erp_design_group_keywords", {"query": {"materialMark": "CR12MOV"}}),
        ]
        assert set(result["data"]) == {"densities", "group_rules", "group_keywords"}
        assert result["data"]["densities"]["rows"][0]["source"] == "query_erp_design_densities"

        calls.clear()
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_master_data_admin").one()
            narrow = execute(db, user, "erp_design_query_master_data", {
                "material_mark": "CR12MOV",
                "include": ["densities"],
            })

        assert calls == [("query_erp_design_densities", {"query": {"materialMark": "CR12MOV"}})]
        assert set(narrow["data"]) == {"densities"}
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

    monkeypatch.setattr("domain_packs.mold.tools.erp.design.erp_design_mcp.call_design_control_mcp", fake_call)
    try:
        with Session.begin() as db:
            user = m.User(username="erp_design_control_admin", display_name="ERP 设计管理员", password_hash="test", super_admin=True)
            db.add(user)
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_design_control_admin").one()
            density = execute(db, user, "erp_design_manage_density", {
                "operation": "create", "material_mark": "P20", "density": "7.85", "confirm": True,
            })
            item = execute(db, user, "erp_design_update_order_item", {
                "detail_id": 21, "material_mark": "S136", "specification": "100×80×50",
                "modify_reason": "按已确认图纸修订", "confirm": True,
            })
            rule = execute(db, user, "erp_design_manage_group_rule", {
                "operation": "set_status", "id": 9, "status": "inactive", "confirm": True,
            })
            keyword = execute(db, user, "erp_design_manage_group_keyword", {
                "operation": "update", "id": 12, "keyword_text": "导柱", "remark": "统一关键词", "confirm": True,
            })
            hardware = execute(db, user, "erp_design_rename_standard_hardware", {
                "relative_path": "DME/导柱/GP-01.dxf", "new_file_name": "GP-01-修订.dxf", "confirm": True,
            })
            skills = {item["key"] for item in skill_context(db, user)}
        assert calls == [
            ("manage_design_density", {"operation": "create", "id": None, "payload": {"materialMark": "P20", "density": "7.85"}}),
            ("update_design_order_item", {"detailId": 21, "materialMark": "S136", "specification": "100×80×50", "modifyReason": "按已确认图纸修订"}),
            ("manage_design_group_rule", {"operation": "set_status", "id": 9, "payload": {"status": "inactive"}}),
            ("manage_design_group_keyword", {"operation": "update", "id": 12, "payload": {"keywordText": "导柱", "remark": "统一关键词"}}),
            ("rename_standard_hardware_drawing", {"relativePath": "DME/导柱/GP-01.dxf", "newFileName": "GP-01-修订.dxf"}),
        ]
        assert all(result["data"]["accepted"] is True for result in [density, item, rule, keyword, hardware])
        assert {"erp_design_order_adjustment", "erp_design_master_data_maintenance", "erp_design_standard_hardware_maintenance"} <= skills
        schema = tool_schema("erp_design_manage_group_rule")["function"]["parameters"]
        assert schema["properties"]["operation"]["enum"] == ["create", "update", "delete", "set_status"]
        assert schema["properties"]["confirm"]["const"] is True
        assert "erp_design_create_density" not in TOOLS
    finally:
        engine.dispose()


def test_erp_design_master_data_crud_is_grouped_by_resource():
    grouped = {
        "erp_design_manage_density",
        "erp_design_manage_group_rule",
        "erp_design_manage_group_keyword",
    }
    legacy = {
        "erp_design_create_density", "erp_design_update_density", "erp_design_delete_density",
        "erp_design_create_group_rule", "erp_design_update_group_rule", "erp_design_toggle_group_rule",
        "erp_design_delete_group_rule", "erp_design_create_group_keyword",
        "erp_design_update_group_keyword", "erp_design_delete_group_keyword",
    }
    assert grouped <= set(TOOLS)
    assert not legacy.intersection(TOOLS)
    assert SKILLS["erp_design_master_data_maintenance"]["tools"] == ["erp_design_query_master_data"]
    assert SKILLS["erp_design_master_data_maintenance"]["activation_tools"] == [
        "erp_design_query_master_data", "erp_design_manage_density",
        "erp_design_manage_group_rule", "erp_design_manage_group_keyword",
    ]
    assert set(SKILLS["erp_design_master_data_maintenance"]["optional_tools"]) == {
        "erp_design_get_record", *grouped,
    }


def test_erp_design_missing_operations_are_registered_and_forwarded(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    calls = []

    def fake_call(name, arguments):
        calls.append((name, arguments))
        return {"accepted": True}

    monkeypatch.setattr("domain_packs.mold.tools.erp.design.erp_design_mcp.call_design_control_mcp", fake_call)
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


def test_design_mold_repair_dedicated_tools_are_typed_and_forwarded(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    calls = []

    def fake_call(name, arguments):
        calls.append((name, arguments))
        return {"accepted": True}

    monkeypatch.setattr(
        "domain_packs.mold.tools.erp.design.erp_design_mcp.call_design_control_mcp",
        fake_call,
    )
    try:
        with Session.begin() as db:
            user = m.User(
                username="erp_mold_repair_admin",
                display_name="ERP 设计修模管理员",
                password_hash="test",
                super_admin=True,
            )
            db.add(user)
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_mold_repair_admin").one()
            quantity = execute(db, user, "erp_design_confirm_mold_repair_quantity", {
                "exception_id": 33,
                "new_qty": 2,
                "confirm": True,
            })
            approval = execute(db, user, "erp_design_submit_mold_repair_approval_batches", {
                "batch_ids": [41, 42],
                "approver_overrides": [{
                    "node_code": "DESIGN_MANAGER_APPROVAL",
                    "user_ids": [7],
                }],
                "category_overrides": [{
                    "exception_id": 33,
                    "business_category": "hardware",
                }],
                "confirm": True,
            })
            order_link = execute(db, user, "erp_design_confirm_mold_repair_order_link", {
                "order_type": "purchase_order",
                "order_id": 51,
                "order_line_key": "purchase-order-detail:81",
                "exception_id": 33,
                "confirm": True,
            })
            response = execute(db, user, "erp_design_respond_mold_repair_processor", {
                "group_token": "repair_group_1",
                "order_id": 61,
                "response_type": "processed_feedback",
                "source_message_id": "notice-2026-001",
                "items": [{
                    "source_exception_id": 33,
                    "processed_qty": 1,
                    "exception_type": "尺寸已加工",
                    "level": "important",
                    "description": "旧版尺寸已完成加工，请确认处置。",
                    "estimated_hours": 2.5,
                }],
                "confirm": True,
            })

        assert calls == [
            ("manage_mold_repair", {
                "operation": "confirm_quantity",
                "exceptionId": 33,
                "batchId": None,
                "groupToken": None,
                "orderId": None,
                "payload": {"newQty": 2},
            }),
            ("manage_mold_repair", {
                "operation": "submit_approval_batches",
                "exceptionId": None,
                "batchId": None,
                "groupToken": None,
                "orderId": None,
                "payload": {
                    "batchIds": [41, 42],
                    "approverOverrides": [{
                        "nodeCode": "DESIGN_MANAGER_APPROVAL",
                        "roleIds": [],
                        "userIds": [7],
                    }],
                    "categoryOverrides": [{
                        "exceptionId": 33,
                        "businessCategory": "hardware",
                    }],
                },
            }),
            ("manage_mold_repair", {
                "operation": "confirm_order_link",
                "exceptionId": None,
                "batchId": None,
                "groupToken": None,
                "orderId": None,
                "payload": {
                    "orderType": "purchase_order",
                    "orderId": 51,
                    "orderLineKey": "purchase-order-detail:81",
                    "exceptionId": 33,
                },
            }),
            ("manage_mold_repair", {
                "operation": "respond",
                "exceptionId": None,
                "batchId": None,
                "groupToken": "repair_group_1",
                "orderId": 61,
                "payload": {
                    "responseType": "processed_feedback",
                    "sourceMessageId": "notice-2026-001",
                    "items": [{
                        "sourceExceptionId": 33,
                        "processedQty": 1.0,
                        "exceptionType": "尺寸已加工",
                        "level": "important",
                        "description": "旧版尺寸已完成加工，请确认处置。",
                        "handling": None,
                        "estimatedHours": 2.5,
                        "attachments": None,
                    }],
                },
            }),
        ]
        assert all(item["data"]["accepted"] for item in [quantity, approval, order_link, response])

        response_schema = tool_schema("erp_design_respond_mold_repair_processor")["function"]["parameters"]
        assert response_schema["properties"]["response_type"]["enum"] == ["agree", "processed_feedback"]
        assert response_schema["properties"]["confirm"]["const"] is True
    finally:
        engine.dispose()


def test_design_mold_repair_response_rejects_missing_processed_feedback(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(
        "domain_packs.mold.tools.erp.design.erp_design_mcp.call_design_control_mcp",
        lambda *_args: {"accepted": True},
    )
    try:
        with Session.begin() as db:
            user = m.User(
                username="erp_mold_repair_validation_admin",
                display_name="ERP 设计修模校验管理员",
                password_hash="test",
                super_admin=True,
            )
            db.add(user)
        with Session() as db:
            user = db.query(m.User).filter_by(username="erp_mold_repair_validation_admin").one()
            with pytest.raises(DomainError, match="至少需要一条已加工异常明细"):
                execute(db, user, "erp_design_respond_mold_repair_processor", {
                    "group_token": "repair_group_1",
                    "order_id": 61,
                    "response_type": "processed_feedback",
                    "source_message_id": "notice-2026-001",
                    "confirm": True,
                })
    finally:
        engine.dispose()


def test_design_mold_repair_temporary_upload_preserves_original_erp_filename(monkeypatch, tmp_path):
    from domain_packs.mold import erp_design_mcp

    file_id = uuid4()
    source = SimpleNamespace(id=file_id, filename="M250238-P5修模.dxf")
    content = b"synthetic dxf"
    directory = tmp_path / "repair-upload"
    directory.mkdir()
    db = SimpleNamespace(scalars=lambda _statement: [file_id])
    user = SimpleNamespace(id="user-1")
    run = SimpleNamespace(id="run-1", user_id="user-1")

    monkeypatch.setattr(erp_design_mcp.files, "uploaded_file", lambda *_args: source)
    monkeypatch.setattr(erp_design_mcp.files, "validate_file", lambda name, _content: (name, None))
    monkeypatch.setattr(erp_design_mcp.object_storage, "read", lambda _source: content)
    monkeypatch.setattr(erp_design_mcp.tempfile, "mkdtemp", lambda **_kwargs: str(directory))

    result_dir, paths = erp_design_mcp._temporary_files(
        db,
        user,
        run,
        [file_id],
        preserve_filename=True,
    )

    assert result_dir == directory
    assert [path.name for path in paths] == ["M250238-P5修模.dxf"]
    assert paths[0].read_bytes() == content
