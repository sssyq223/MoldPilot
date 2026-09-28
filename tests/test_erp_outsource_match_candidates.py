from domain_packs.mold.tools.erp.procurement.outsource_queries import match_candidates as match


PROCESS_CODES = [
    {"id": 1, "code": "S", "name": "开粗"},
    {"id": 2, "code": "Z", "name": "精铣"},
    {"id": 3, "code": "M", "name": "磨床"},
]


def test_mold_frame_ratio_matches_erp_threshold():
    stats = match.mold_frame_stats(["下托板", "下垫脚", "下垫脚"])
    assert stats["matched"] is True
    assert stats["ratio"] >= 0.8
    assert match.mold_frame_stats(["成型冲头", "上夹板"])["matched"] is False


def test_required_codes_from_route_and_method():
    required = match.required_code_ids(
        ["S-Z"],
        PROCESS_CODES,
        method_ids=[3],
        methods=[{"id": 3, "name": "磨床"}],
    )
    assert required == {1, 2, 3}


def test_full_coverage_keeps_capability_and_category_suppliers():
    required = {1, 2}
    capability = match.capability_code_ids(
        [{"process_code_id": 1}, {"process_name": "Z"}],
        PROCESS_CODES,
    )
    category = match.category_code_ids("全加工零件", {"全加工零件": {1, 2, 3}})
    assert required <= capability
    assert required <= category
    assert not ({1, 2, 3} <= capability)


def test_fetch_match_candidates_uses_process_coverage_for_normal_parts(monkeypatch):
    monkeypatch.setattr(match, "_safe_fetch", lambda sql, params=None: {
        match.PROJECT_SQL: [{"outsource_type": "part"}],
        match.PART_SQL: [{"name": "成型冲头", "accounting_process": "S-Z", "process_method_ids": []}],
        match.PROCESS_CODE_SQL: PROCESS_CODES,
        match.PROCESS_METHOD_SQL: [],
        match.CATEGORY_MAP_SQL: [{"category_name": "全加工零件", "process_code_ids": [1, 2, 3]}],
        match.SUPPLIER_SQL: [
            {"supplier_id": 11, "supplier_code": "A", "supplier_name": "铂锐", "category_name": "精铣"},
            {"supplier_id": 22, "supplier_code": "B", "supplier_name": "全加工厂", "category_name": "全加工零件"},
        ],
        match.CAPABILITY_SQL: [
            {"supplier_id": 11, "process_code_id": 1, "process_name": None},
        ],
    }.get(sql, []))
    rows = match.fetch_match_candidates(9)
    assert [row["supplierName"] for row in rows] == ["全加工厂"]
    assert rows[0]["supplierId"] == 22


def test_fetch_match_candidates_uses_mold_frame_suppliers(monkeypatch):
    monkeypatch.setattr(match, "_safe_fetch", lambda sql, params=None: {
        match.PROJECT_SQL: [{"outsource_type": "part"}],
        match.PART_SQL: [
            {"name": "下托板", "accounting_process": "S-Z", "process_method_ids": []},
            {"name": "下垫脚", "accounting_process": "S-Z", "process_method_ids": []},
        ],
        match.SUPPLIER_SQL: [{
            "supplier_id": 921159,
            "supplier_code": "SUP000114",
            "supplier_name": "青岛和兴金属制品有限公司",
            "category_name": "模架",
        }],
    }.get(sql, []))
    rows = match.fetch_match_candidates(4367)
    assert [row["supplierName"] for row in rows] == ["青岛和兴金属制品有限公司"]
    assert rows[0]["status"] == "matched"
