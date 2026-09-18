from base64 import b64encode
from decimal import Decimal
from types import SimpleNamespace


def test_design_source_file_validation_accepts_xls_prt_and_dwg():
    from app.files import validate_file

    legacy = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
    assert validate_file("设计清单.xls", legacy)[1] == "application/vnd.ms-excel"
    assert validate_file("R-BZ-001.dwg", b"AC1032" + b"\x00" * 128)[1] == "application/acad"
    assert validate_file("R-BZ-001.prt", b"NX-PRT" + b"\x00" * 128)[1] == "application/octet-stream"


def test_visual_workspace_reads_orders_and_drawing_versions(monkeypatch):
    from domain_packs.mold.erp.design import erp_design_workspace

    calls = []

    def fake_call(name, arguments):
        calls.append((name, arguments))
        if name == "query_erp_design_orders":
            return {"rows": [{"requestId": 7, "requestNo": "PR-007"}], "total": 1}
        return {"rows": [{"drawingId": 11, "partCode": "P01"}], "total": 1}

    monkeypatch.setattr(erp_design_workspace.erp_design_mcp, "call_mcp", fake_call)
    monkeypatch.setattr(erp_design_workspace, "require", lambda *_args: None)
    db, user = SimpleNamespace(), SimpleNamespace(id="user-1")

    orders = erp_design_workspace.query_orders(
        erp_design_workspace.QueryInput(query={"keyword": "PR"}), user=user, db=db,
    )
    drawings = erp_design_workspace.query_drawing_versions(
        erp_design_workspace.QueryInput(query={"moldCode": "M1"}), user=user, db=db,
    )

    assert orders["data"]["rows"][0]["requestNo"] == "PR-007"
    assert drawings["data"]["rows"][0]["partCode"] == "P01"
    assert calls == [
        ("query_erp_design_orders", {"query": {"keyword": "PR"}}),
        ("query_erp_drawing_versions", {"query": {"moldCode": "M1"}}),
    ]


def test_visual_workspace_density_write_uses_existing_tool_boundary(monkeypatch):
    from domain_packs.mold.erp.design import erp_design_workspace

    calls = []
    monkeypatch.setattr(erp_design_workspace, "require", lambda *_args: None)
    monkeypatch.setattr(
        erp_design_workspace.erp_design_mcp,
        "execute_tool",
        lambda db, user, key, arguments: calls.append((key, arguments)) or {"data": {"success": True}},
    )
    db, user = SimpleNamespace(), SimpleNamespace(id="user-1")

    response = erp_design_workspace.manage_density(
        erp_design_workspace.DensityInput(
            operation="update", id=9, material_mark="CR12MOV", density=Decimal("7.85"), confirm=True,
        ),
        user=user,
        db=db,
    )

    assert response == {"data": {"success": True}}
    assert calls == [("erp_design_manage_density", {
        "operation": "update", "id": 9, "material_mark": "CR12MOV", "density": Decimal("7.85"), "confirm": True,
    })]


def test_visual_workspace_streams_erp_drawing_preview(monkeypatch):
    from domain_packs.mold.erp.design import erp_design_workspace

    content = b"SECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    monkeypatch.setattr(erp_design_workspace, "require", lambda *_args: None)
    monkeypatch.setattr(
        erp_design_workspace.erp_design_mcp,
        "call_design_control_mcp",
        lambda name, arguments: {
            "base64": b64encode(content).decode(), "fileName": "P01.dxf", "mediaType": "application/dxf",
        },
    )

    response = erp_design_workspace.preview_drawing(
        11, user=SimpleNamespace(id="user-1"), db=SimpleNamespace(),
    )

    assert response.body == content
    assert response.headers["content-disposition"].startswith("inline;")


def test_visual_workspace_routes_are_installed():
    from app.api import app

    routes = []
    for route in app.routes:
        routes.extend(getattr(getattr(route, "original_router", None), "routes", []) or [route])
    paths = {getattr(route, "path", "") for route in routes}
    assert "/api/erp-design-workspace/orders/query" in paths
    assert "/api/erp-design-workspace/drawing-versions/{drawing_id}/preview" in paths
    assert "/api/erp-design-workspace/densities/manage" in paths
    assert "/api/erp-design-workspace/standard-hardware/query" in paths
