"""ERP-equivalent supplier match for 待发询价 (read-only).

Mirrors management-system MatchService for the send-inquiry list:
- 零件/模具委外且模架件占比 ≥80%：只匹配 category=模架
- 其他零件/模具委外：只匹配 100% 覆盖所需工序的加工商（能力表 + 分类映射）
Invitations are created only after send; this list is what ERP shows before send.
"""
from __future__ import annotations

import re
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all
from domain_packs.mold.ports.errors import DomainError

MOLD_FRAME_KEYWORDS = ("托板", "垫脚", "模座")
MOLD_FRAME_CATEGORY = "模架"
MOLD_FRAME_RATIO = 0.8
ROUTE_SPLIT = re.compile(r"[-－—]")
CATEGORY_SPLIT = re.compile(r"[/、,，]")
PAREN_SUFFIX = re.compile(r"[（(][^）)]*[）)]")

PROJECT_SQL = """
SELECT id, lower(coalesce(outsource_type, '')) AS outsource_type
FROM entrust_projects
WHERE id = %(project_id)s
"""
PART_SQL = """
SELECT
    coalesce(nullif(btrim(part_name), ''), btrim(part_no)) AS name,
    accounting_process,
    process_method_ids
FROM entrust_parts
WHERE project_id = %(project_id)s
"""
UPLOAD_SQL = """
SELECT
    coalesce(nullif(btrim(line.part_name), ''), btrim(line.part_no)) AS name,
    line.accounting_process
FROM entrust_upload_batches batch
JOIN entrust_upload_lines line ON line.batch_id = batch.id
WHERE batch.project_id = %(project_id)s
"""
PROCESS_CODE_SQL = """
SELECT id, code, name
FROM entrust_process_codes
WHERE is_active = 1
"""
PROCESS_METHOD_SQL = """
SELECT id, name
FROM entrust_process_methods
"""
CATEGORY_MAP_SQL = """
SELECT category_name, process_code_ids
FROM entrust_category_process_mapping
WHERE is_active = 1
"""
SUPPLIER_SQL = """
SELECT
    supplier.id AS supplier_id,
    supplier.partner_code AS supplier_code,
    supplier.partner_name AS supplier_name,
    supplier.category_name
FROM partner supplier
JOIN entrust_suppliers ext ON ext.id = supplier.id
WHERE supplier.partner_type = 3
  AND coalesce(supplier.is_deleted, 0) = 0
  AND supplier.status = 'active'
  AND (
        (%(mold_frame)s AND supplier.category_name = '模架')
        OR (NOT %(mold_frame)s AND supplier.category_name IS DISTINCT FROM '模架')
  )
ORDER BY supplier.partner_name
"""
CAPABILITY_SQL = """
SELECT supplier_id, process_code_id, process_name
FROM entrust_supplier_capabilities
WHERE supplier_id = ANY(%(supplier_ids)s)
"""


def _safe_fetch(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        return fetch_all(sql, params or {})
    except DomainError:
        return []


def mold_frame_stats(names: list[str]) -> dict[str, Any]:
    cleaned = [str(name or "").strip() for name in names if str(name or "").strip()]
    total = len(cleaned)
    if total <= 0:
        return {"total": 0, "mold_frame": 0, "ratio": 0.0, "matched": False}
    frame = sum(1 for name in cleaned if any(word in name for word in MOLD_FRAME_KEYWORDS))
    ratio = frame / total
    return {
        "total": total,
        "mold_frame": frame,
        "ratio": round(ratio, 4),
        "matched": ratio >= MOLD_FRAME_RATIO,
    }


def route_tokens(route: Any) -> list[str]:
    text = str(route or "").strip()
    if not text:
        return []
    if "→" in text or "->" in text:
        return [part.strip() for part in re.split(r"→|->", text) if part.strip()]
    return [part.strip() for part in ROUTE_SPLIT.split(text) if part.strip()]


def _code_lookup(process_codes: list[dict[str, Any]]) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for row in process_codes:
        code_id = int(row["id"])
        name = str(row.get("name") or "").strip()
        code = str(row.get("code") or "").strip()
        if name:
            lookup[name] = code_id
        if code:
            lookup[code] = code_id
            lookup[code.upper()] = code_id
            lookup[code.lower()] = code_id
    return lookup


def required_code_ids(
    routes: list[Any],
    process_codes: list[dict[str, Any]],
    method_ids: list[Any] | None = None,
    methods: list[dict[str, Any]] | None = None,
) -> set[int]:
    lookup = _code_lookup(process_codes)
    found: set[int] = set()
    for route in routes:
        for token in route_tokens(route):
            code_id = lookup.get(token) or lookup.get(token.upper()) or lookup.get(token.lower())
            if code_id is not None:
                found.add(int(code_id))
    name_to_code = {
        str(row.get("name") or "").strip(): int(row["id"])
        for row in process_codes
        if str(row.get("name") or "").strip()
    }
    method_name = {
        int(row["id"]): str(row.get("name") or "").strip()
        for row in (methods or [])
        if row.get("id") is not None
    }
    for raw in method_ids or []:
        try:
            method_id = int(raw)
        except (TypeError, ValueError):
            continue
        name = method_name.get(method_id)
        if name and name in name_to_code:
            found.add(name_to_code[name])
    return found


def split_category(category: Any) -> list[str]:
    return [part.strip() for part in CATEGORY_SPLIT.split(str(category or "")) if part.strip()]


def category_code_ids(category: Any, category_map: dict[str, set[int]]) -> set[int]:
    ids: set[int] = set()
    for part in split_category(category):
        norm = PAREN_SUFFIX.sub("", part).strip()
        for key in (part, norm):
            if key and key in category_map:
                ids.update(category_map[key])
                break
        else:
            text = part
            if "沙迪克" in text or "慢走丝" in text or norm == "慢丝" or (
                ("慢丝" in text or text.endswith("慢丝")) and "快" not in text
            ):
                ids.update(category_map.get("慢丝") or ())
            elif "中走丝" in text or norm == "中丝" or text.endswith("中丝"):
                ids.update(category_map.get("中丝") or ())
            elif "快走丝" in text or norm == "快丝" or (
                text.endswith("快丝") and "慢" not in text and "中" not in text
            ):
                ids.update(category_map.get("快丝") or ())
            elif norm == "小磨床":
                ids.update(category_map.get("小磨床") or ())
            elif norm == "全加工零件" or text == "全加工零件":
                ids.update(category_map.get("全加工零件") or ())
    return ids


def capability_code_ids(
    cap_rows: list[dict[str, Any]],
    process_codes: list[dict[str, Any]],
) -> set[int]:
    lookup = _code_lookup(process_codes)
    found: set[int] = set()
    for row in cap_rows:
        raw_id = row.get("process_code_id")
        if raw_id not in (None, ""):
            found.add(int(raw_id))
            continue
        name = str(row.get("process_name") or "").strip()
        if not name:
            continue
        code_id = lookup.get(name) or lookup.get(name.upper())
        if code_id is not None:
            found.add(int(code_id))
    return found


def _as_int_list(value: Any) -> list[int]:
    if isinstance(value, list):
        items = value
    elif value in (None, ""):
        items = []
    else:
        items = [value]
    result = []
    for item in items:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _project_parts(project_id: int) -> tuple[list[str], list[str], list[int]]:
    parts = _safe_fetch(PART_SQL, {"project_id": project_id})
    names = [str(row.get("name") or "").strip() for row in parts if str(row.get("name") or "").strip()]
    routes = [row.get("accounting_process") for row in parts if str(row.get("accounting_process") or "").strip()]
    method_ids: list[int] = []
    for row in parts:
        method_ids.extend(_as_int_list(row.get("process_method_ids")))
    if names:
        return names, routes, method_ids
    uploads = _safe_fetch(UPLOAD_SQL, {"project_id": project_id})
    names = [str(row.get("name") or "").strip() for row in uploads if str(row.get("name") or "").strip()]
    routes = [row.get("accounting_process") for row in uploads if str(row.get("accounting_process") or "").strip()]
    return names, routes, method_ids


def _category_map() -> dict[str, set[int]]:
    mapping: dict[str, set[int]] = {}
    for row in _safe_fetch(CATEGORY_MAP_SQL):
        name = str(row.get("category_name") or "").strip()
        ids = {int(item) for item in _as_int_list(row.get("process_code_ids"))}
        if name and ids:
            mapping[name] = ids
    return mapping


def _as_invitation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "supplierId": int(row["supplier_id"]),
        "supplierCode": str(row.get("supplier_code") or "").strip(),
        "supplierName": str(row.get("supplier_name") or "").strip(),
        "status": "matched",
    }


def fetch_match_candidates(project_id: Any) -> list[dict[str, Any]]:
    if not project_id:
        return []
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        return []
    project = (_safe_fetch(PROJECT_SQL, {"project_id": project_id}) or [{}])[0]
    outsource_type = str(project.get("outsource_type") or "")
    if outsource_type == "operation":
        return []
    names, routes, method_ids = _project_parts(project_id)
    frame = mold_frame_stats(names)
    use_frame = outsource_type in {"part", "mold"} and frame["matched"]
    suppliers = _safe_fetch(SUPPLIER_SQL, {"mold_frame": use_frame})
    if use_frame:
        return [_as_invitation(row) for row in suppliers if row.get("supplier_id") is not None]
    process_codes = _safe_fetch(PROCESS_CODE_SQL)
    methods = _safe_fetch(PROCESS_METHOD_SQL)
    required = required_code_ids(routes, process_codes, method_ids, methods)
    if not required:
        return []
    supplier_ids = [int(row["supplier_id"]) for row in suppliers if row.get("supplier_id") is not None]
    caps_by_supplier: dict[int, list[dict[str, Any]]] = {sid: [] for sid in supplier_ids}
    if supplier_ids:
        for row in _safe_fetch(CAPABILITY_SQL, {"supplier_ids": supplier_ids}):
            try:
                sid = int(row.get("supplier_id"))
            except (TypeError, ValueError):
                continue
            caps_by_supplier.setdefault(sid, []).append(row)
    category_map = _category_map()
    matched = []
    for row in suppliers:
        if row.get("supplier_id") is None:
            continue
        sid = int(row["supplier_id"])
        covered = capability_code_ids(caps_by_supplier.get(sid) or [], process_codes)
        covered.update(category_code_ids(row.get("category_name"), category_map))
        if required <= covered:
            matched.append(_as_invitation(row))
    return matched
