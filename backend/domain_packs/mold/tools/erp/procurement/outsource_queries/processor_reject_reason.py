"""Active ERP processor reject-reason catalog for 待接单拒单."""
from __future__ import annotations

from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all
from domain_packs.mold.ports.errors import DomainError


def _from_options(payload: Any) -> list[dict[str, str]]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        data = data.get("items") or data.get("rows") or data.get("options") or []
    if not isinstance(data, list):
        return []
    items = []
    for row in data:
        if not isinstance(row, dict):
            continue
        code = str(
            row.get("reasonCode") or row.get("reason_code") or row.get("value") or row.get("dictValue") or ""
        ).strip()
        label = str(
            row.get("reasonLabel") or row.get("reason_label") or row.get("label") or row.get("dictLabel") or code
        ).strip()
        if code:
            items.append({"reason_code": code, "reason_label": label or code})
    return items


def list_from_erp(db, user) -> list[dict[str, str]]:
    from domain_packs.mold.erp.procurement.erp_outsource_http import get_erp

    try:
        return _from_options(get_erp(db, user, "entrust/inquiry/order/options/reject-reasons"))
    except DomainError:
        return []

SQL = """
SELECT
    reason_code,
    reason_label,
    sort_order
FROM entrust_processor_order_reject_reason
WHERE coalesce(status, '0') = '0'
  AND coalesce(btrim(reason_code), '') <> ''
ORDER BY sort_order NULLS LAST, id
"""


def list_active() -> list[dict[str, str]]:
    items = []
    for row in fetch_all(SQL):
        code = str(row.get("reason_code") or "").strip()
        label = str(row.get("reason_label") or "").strip()
        if not code:
            continue
        items.append({"reason_code": code, "reason_label": label or code})
    return items


def resolve(token: str | None = None, db=None, user=None) -> dict[str, str]:
    items = list_from_erp(db, user) if db is not None and user is not None else []
    if not items:
        items = list_active()
    if not items:
        raise DomainError("STATE_BLOCKED", "ERP 当前没有启用的拒单原因，请先在 ERP 维护后再拒单", 409)
    text = str(token or "").strip()
    if not text:
        return items[0]
    folded = text.casefold()
    for item in items:
        if item["reason_code"].casefold() == folded or item["reason_label"] == text:
            return item
    hits = [
        item for item in items
        if text in item["reason_label"] or item["reason_label"] in text
    ]
    if len(hits) == 1:
        return hits[0]
    if len(items) == 1:
        return items[0]
    labels = "、".join(item["reason_label"] for item in items)
    raise DomainError("INVALID_TOOL_INPUT", f"拒单原因不是当前可用项，请改用：{labels}")
