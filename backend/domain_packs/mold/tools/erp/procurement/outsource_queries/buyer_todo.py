"""Read-only buyer todo station query."""
from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all
from domain_packs.mold.tools.erp.procurement.outsource_queries.match_candidates import (
    fetch_match_candidates as load_match_candidates,
)
from domain_packs.mold.ports.errors import DomainError

MOLD_FAMILY = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,})(?!-P\d+)(?![A-Z0-9])")
MOLD_BATCH = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,}-P\d+)(?![A-Z0-9])")
MOLD_BATCH_CODE = re.compile(r"(?i)^M\d{5,}-P\d+$")
MOLD_FAMILY_CODE = re.compile(r"(?i)^M\d{5,}$")
PROJECT_NO = re.compile(r"(?i)(?<![A-Z0-9])(E\d+-\d+|ENT-BATCH-[A-Z0-9]+)(?![A-Z0-9])")
ORDER_NO = re.compile(r"(?i)(?<![A-Z0-9])(EO-\d{6}-[A-Z0-9]+)(?![A-Z0-9])")
PART_NO = re.compile(r"(?i)(?:零件\s*(?:是|为|:|：)?)([A-Z]{1,8}-?\d{1,4}[A-Z]?)")
QUOTE_AMOUNT = re.compile(r"(?:我方报价|填报价|报价|总价格|总价)\s*(?:是|为|:|：)?\s*(\d+(?:\.\d+)?)")
MAX_AMOUNT = re.compile(r"(?:上限区间|接单上限|上限)\s*(?:是|为|:|：)?\s*(\d+(?:\.\d+)?)")
ROW_INDEX = re.compile(r"第\s*(\d+)\s*行")
ROW_SPOKEN = re.compile(r"第\s*(\d+|[一二三四五六七八九十])\s*行")
CN_ROW = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
FIRST_BOARD_ROW = re.compile(r"第\s*(?:1|一)\s*行")
PENDING_FIRST_QUOTE = re.compile(r"(?:待排(?:列)?|排列)第\s*(?:1|一)")
NAMED_SEND_STATION = re.compile(r"待发询价")
THIS_ORDER = re.compile(r"这[一笔张]?[单订]|这个订单|这笔订单|这单")
BUYER_QUOTE_SPEECH = (
    "填我方报价", "填写我方报价", "填报价", "填写报价", "填价格",
    "帮我填报价", "帮我填价格",
    "准备填写我方报价", "准备填写报价",
    "报报价", "报个价", "帮我报价",
)
INQUIRY_SEND_SPEECH = (
    "发询价", "发送询价", "办理发询价", "办理发送询价",
    "确认发询价", "确认发送询价", "确认办理发询价", "确认办理发送询价",
)
ROW_LIMIT = 200
STATIONS = {
    "buyer_quote": "待采购填报价",
    "inquiry_send": "待发询价",
    "supplier_quote": "待报价",
    "place_order": "待下单",
    "order_approval": "审批中",
    "accept": "待接单",
    "exhausted": "全部拒单",
}
OUTSOURCE_TYPE_LABELS = {
    "part": "零件委外",
    "operation": "工序委外",
    "mold": "模具委外",
}
AWARDED_TODO_STAGES = {
    "pending_approval": "order_approval",
    "pending_accept": "accept",
}
PENDING_INVITE_STATUS = {"sent", "draft", "draft_quoted"}

SQL = """
SELECT
    project.id AS project_id,
    project.project_no,
    project.name AS project_name,
    lower(coalesce(project.outsource_type, '')) AS outsource_type,
    molds.mold_no,
    inquiry.id AS inquiry_id,
    lower(coalesce(inquiry.status, '')) AS inquiry_status,
    inquiry.reference_total_amount,
    inquiry.our_quote_amount,
    inquiry.auto_accept_max_amount,
    inquiry.final_deal_amount,
    coalesce(invite.invitation_count, 0) AS invitation_count,
    coalesce(invite.quoted_count, 0) AS quoted_count,
    invite.invitations,
    awarded.stage AS awarded_stage,
    awarded.status AS awarded_status,
    awarded.order_id AS awarded_order_id,
    awarded.order_no AS awarded_order_no,
    awarded.supplier_id,
    awarded.supplier_code,
    awarded.supplier_name,
    awarded.awarded_amount,
    awarded.process_name,
    coalesce(awarded.dispatch_exhausted, false) AS dispatch_exhausted,
    awarded.pending_dispatch_suppliers,
    coalesce(order_parts.parts, project_parts.parts) AS parts,
    coalesce(inquiry.reference_total_amount, order_parts.parts_reference_total, project_parts.parts_reference_total) AS reference_total
FROM entrust_projects project
LEFT JOIN LATERAL (
    SELECT string_agg(DISTINCT mold.name, '、' ORDER BY mold.name) AS mold_no
    FROM entrust_molds mold
    WHERE mold.project_id = project.id
) molds ON TRUE
LEFT JOIN LATERAL (
    SELECT request_row.*
    FROM entrust_outsource_requests request_row
    WHERE request_row.project_id = project.id
      AND coalesce(request_row.inquiry_type, 'normal') <> 'reflow'
      AND coalesce(request_row.status, 'draft') IN ('draft', 'sent', 'quoted', 'awarded', 'partial_awarded', 'closed')
    ORDER BY
        CASE coalesce(request_row.status, 'draft')
            WHEN 'awarded' THEN 0
            WHEN 'partial_awarded' THEN 0
            WHEN 'quoted' THEN 1
            WHEN 'sent' THEN 2
            ELSE 3
        END,
        request_row.id
    LIMIT 1
) inquiry ON TRUE
LEFT JOIN LATERAL (
    SELECT
        count(*) FILTER (WHERE invitation.status IN ('sent', 'quoted', 'draft', 'draft_quoted')) AS invitation_count,
        count(*) FILTER (WHERE invitation.status = 'quoted') AS quoted_count,
        json_agg(
            json_build_object(
                'invitationId', invitation.id,
                'supplierId', supplier.id,
                'supplierCode', supplier.partner_code,
                'supplierName', supplier.partner_name,
                'status', invitation.status,
                'quoteAmount', quote.unit_price,
                'quoteId', quote.id
            )
            ORDER BY supplier.partner_name
        ) FILTER (WHERE invitation.id IS NOT NULL) AS invitations
    FROM entrust_invitations invitation
    LEFT JOIN partner supplier ON supplier.id = invitation.supplier_id
    LEFT JOIN entrust_quotations quote ON quote.invitation_id = invitation.id
    WHERE invitation.request_id = inquiry.id
) invite ON inquiry.id IS NOT NULL
LEFT JOIN LATERAL (
    SELECT
        lower(coalesce(order_row.stage, '')) AS stage,
        lower(coalesce(order_row.status, '')) AS status,
        order_row.id AS order_id,
        order_row.order_no,
        supplier.id AS supplier_id,
        supplier.partner_code AS supplier_code,
        supplier.partner_name AS supplier_name,
        order_row.total_amount AS awarded_amount,
        order_row.process_name,
        (
            lower(coalesce(order_row.status, '')) = 'rejected'
            AND lower(coalesce(project.outsource_type, '')) = 'operation'
            AND coalesce(jsonb_typeof(order_row.dispatch_queue_json), '') = 'array'
            AND jsonb_array_length(order_row.dispatch_queue_json) > 0
            AND coalesce(order_row.dispatch_index, 0) + 1 >= jsonb_array_length(order_row.dispatch_queue_json)
        ) AS dispatch_exhausted,
        (
            SELECT string_agg(queue_partner.partner_name, '、' ORDER BY queue_item.ordinality)
            FROM jsonb_array_elements(coalesce(order_row.dispatch_queue_json, '[]'::jsonb))
                 WITH ORDINALITY AS queue_item(supplier_id, ordinality)
            JOIN partner queue_partner
              ON queue_partner.id = nullif(btrim(queue_item.supplier_id::text, '"'), '')::bigint
            WHERE coalesce(jsonb_typeof(order_row.dispatch_queue_json), '') = 'array'
              AND queue_item.ordinality > coalesce(order_row.dispatch_index, 0)
        ) AS pending_dispatch_suppliers
    FROM entrust_outsource_orders order_row
    LEFT JOIN partner supplier ON supplier.id = order_row.supplier_id
    WHERE order_row.project_id = project.id
      AND lower(coalesce(order_row.stage, '')) <> 'pending_match'
      AND lower(coalesce(order_row.status, '')) IN ('open', 'closed', 'rejected')
    ORDER BY
        CASE
            WHEN lower(coalesce(order_row.status, '')) = 'open'
             AND lower(coalesce(order_row.stage, '')) IN ('pending_approval', 'pending_accept') THEN 0
            WHEN lower(coalesce(order_row.status, '')) = 'open' THEN 1
            WHEN lower(coalesce(order_row.stage, '')) = 'delivered' THEN 2
            ELSE 3
        END,
        order_row.id DESC
    LIMIT 1
) awarded ON TRUE
LEFT JOIN LATERAL (
    SELECT
        json_agg(
            json_build_object(
                'partNo', part.part_no,
                'partName', part.part_name,
                'moldCode', part.mold_code,
                'qty', part.order_qty,
                'processName', CASE
                    WHEN jsonb_typeof(part.processes_json) = 'array'
                    THEN (
                        SELECT string_agg(elem, '、')
                        FROM jsonb_array_elements_text(part.processes_json) AS process_elem(elem)
                    )
                    ELSE nullif(btrim(part.processes_json::text, '"'), '')
                END,
                'referenceTotalAmount', part.reference_total_amount
            )
            ORDER BY part.mold_code, part.part_no
        ) AS parts,
        sum(part.reference_total_amount) AS parts_reference_total
    FROM entrust_order_parts part
    WHERE part.order_id IN (
        SELECT order_row.id
        FROM entrust_outsource_orders order_row
        WHERE order_row.project_id = project.id
          AND (
                inquiry.id IS NULL
             OR order_row.request_id = inquiry.id
             OR order_row.order_no = awarded.order_no
          )
    )
) order_parts ON TRUE
LEFT JOIN LATERAL (
    SELECT
        json_agg(
            json_build_object(
                'partNo', ep.part_no,
                'partName', ep.part_name,
                'moldCode', em.name,
                'qty', ep.qty,
                'processName', ep.accounting_process,
                'spec', ep.spec,
                'referenceTotalAmount', ep.total_amount
            )
            ORDER BY em.name, ep.part_no
        ) AS parts,
        sum(ep.total_amount) AS parts_reference_total
    FROM entrust_molds em
    JOIN entrust_parts ep ON ep.mold_id = em.id
    WHERE em.project_id = project.id
) project_parts ON TRUE
WHERE coalesce(project.status, '') IN ('confirmed', 'in_progress')
  AND (
        %(outsource_type)s = ''
     OR lower(coalesce(project.outsource_type, '')) = %(outsource_type)s
  )
  AND (
        (%(mold_batch)s = '' AND %(mold_family)s = '' AND %(project_no)s = '')
        OR (%(project_no)s <> '' AND upper(project.project_no) = %(project_no)s)
        OR EXISTS (
            SELECT 1
            FROM entrust_molds mold
            WHERE mold.project_id = project.id
              AND (
                    (%(mold_batch)s <> '' AND upper(mold.name) = %(mold_batch)s)
                 OR (%(mold_family)s <> '' AND (
                        upper(mold.name) = %(mold_family)s
                     OR upper(mold.name) LIKE %(mold_family)s || '-P%%'
                    ))
              )
        )
  )
ORDER BY project.project_no
"""


def money(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def format_parts(parts: list[Any]) -> str:
    labels = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_no = str(part.get("partNo") or part.get("part_no") or "").strip()
        name = str(part.get("partName") or part.get("part_name") or "").strip()
        qty = part.get("qty")
        process = str(part.get("processName") or part.get("process_name") or "").strip()
        spec = str(part.get("spec") or "").strip()
        head = " ".join(piece for piece in (part_no, name) if piece) or "零件"
        extras = []
        if qty not in (None, "", 1, 1.0):
            extras.append(f"×{qty}")
        if process:
            extras.append(process)
        if spec:
            extras.append(spec)
        labels.append(head if not extras else f"{head}（{' '.join(extras)}）")
    return "；".join(labels)


def _unique_codes(values: list[str]) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for value in values:
        text = str(value or "").strip().upper()
        if not text or text in seen:
            continue
        seen.add(text)
        codes.append(text)
    return codes


def mold_codes_from(mold_no: Any, parts: list[Any] | None = None) -> list[str]:
    codes: list[str] = []
    for piece in re.split(r"[、,;/\s]+", str(mold_no or "")):
        codes.append(piece)
    for part in parts or []:
        if isinstance(part, dict):
            codes.append(str(part.get("moldCode") or part.get("mold_code") or ""))
    return _unique_codes(codes)


def mold_labels(mold_no: Any, parts: list[Any] | None = None) -> tuple[str, str]:
    codes = mold_codes_from(mold_no, parts)
    batches = [code for code in codes if MOLD_BATCH_CODE.match(code)]
    families = [code for code in codes if MOLD_FAMILY_CODE.match(code)]
    for batch in batches:
        family = batch.split("-P", 1)[0]
        if family not in families:
            families.append(family)
    return "、".join(families), "、".join(batches) or "、".join(families)


def _norm_code(value: Any) -> str:
    return str(value or "").strip().upper()


def item_identity(item: dict[str, Any]) -> tuple[str, str]:
    family = str(item.get("moldFamily") or "").strip()
    batch = str(item.get("moldBatch") or "").strip()
    if not family or not batch:
        derived_family, derived_batch = mold_labels(item.get("moldNo"), item.get("parts"))
        family = family or derived_family
        batch = batch or derived_batch
    return family, batch


def identity_display(item: dict[str, Any]) -> dict[str, str]:
    family, batch = item_identity(item)
    return {
        "订单号": str(item.get("orderNo") or "").strip() or "尚未下单",
        "模具号": family or str(item.get("moldNo") or "") or "未标注",
        "批次号": batch or str(item.get("moldNo") or "") or "未标注",
    }


def _batch_tokens(value: Any) -> list[str]:
    return [_norm_code(piece) for piece in re.split(r"[、,;]+", str(value or "")) if piece.strip()]


def _promote_batch_code(mold: str, batch: str) -> tuple[str, str]:
    if re.fullmatch(r"M\d{5,}-P\d+", mold) and not batch:
        return mold.split("-P", 1)[0], mold
    return mold, batch


PART_CODE = re.compile(
    r"(?i)(?<![A-Z0-9])([A-Z]{1,8}\d{0,4}-\d{1,4}[A-Z]{0,3}|[A-Z]{2,8}-\d{1,4}[A-Z]{0,3})(?![A-Z0-9])"
)


def normalize_part_token(value: Any) -> str:
    if isinstance(value, list):
        pieces = []
        for item in value:
            if isinstance(item, dict):
                pieces.append(str(item.get("partNo") or item.get("part_no") or item.get("partName") or ""))
            else:
                pieces.append(str(item or ""))
        value = " ".join(piece for piece in pieces if piece.strip())
    text = str(value or "").strip()
    if not text:
        return ""
    match = PART_CODE.search(text)
    return (match.group(1) if match else text).upper()


def item_matches_part(item: dict[str, Any], part: str) -> bool:
    wanted = normalize_part_token(part)
    if not wanted:
        return True
    for row in item.get("parts") or []:
        if not isinstance(row, dict):
            continue
        no = _norm_code(row.get("partNo") or row.get("part_no"))
        name = _norm_code(row.get("partName") or row.get("part_name"))
        if no == wanted or name == wanted:
            return True
    details = str(item.get("partDetails") or "")
    return bool(re.search(rf"(?i)(?<![A-Z0-9]){re.escape(wanted)}(?![A-Z0-9])", details))


def item_matches_identity(
    item: dict[str, Any],
    *,
    order_no: str = "",
    mold: str = "",
    batch: str = "",
) -> bool:
    wanted_order = _norm_code(order_no)
    wanted_mold, wanted_batch = _promote_batch_code(_norm_code(mold), _norm_code(batch))
    family, item_batch = item_identity(item)
    codes = mold_codes_from(" ".join(piece for piece in (item.get("moldNo"), family, item_batch) if piece), item.get("parts"))
    if wanted_order and _norm_code(item.get("orderNo")) != wanted_order:
        return False
    if wanted_batch:
        tokens = _batch_tokens(item_batch) or _batch_tokens(item.get("moldNo"))
        if len(tokens) > 1:
            full = _norm_code(item_batch) or _norm_code(item.get("moldNo"))
            if wanted_batch != full:
                return False
        elif wanted_batch not in {_norm_code(item_batch), _norm_code(item.get("moldNo")), *codes, *tokens}:
            return False
    if wanted_mold:
        if wanted_mold not in codes and wanted_mold != _norm_code(family) and not any(
            code.startswith(f"{wanted_mold}-P") for code in codes
        ):
            return False
    return True


def _ambiguous_identity_message(hits: list[dict[str, Any]], *, part: str = "") -> str:
    lines = [
        "同一模具/批次下有多张待办询价，并不是这个零件出现在多张工单里。",
        f"当前条件命中 {len(hits)} 张：",
    ]
    for item in hits[:8]:
        display = identity_display(item)
        details = str(item.get("partDetails") or "未标注零件").strip()
        if len(details) > 80:
            details = details[:80] + "…"
        lines.append(
            f"- {display['订单号']} / {display['模具号']} / {display['批次号']} / {details}"
        )
    if len(hits) > 8:
        lines.append(f"- 另有 {len(hits) - 8} 张未展开")
    if part:
        lines.append(f"零件 {normalize_part_token(part)} 仍无法唯一锁定，请再补订单号或表格里的完整批次号。")
    else:
        lines.append("请补零件号（例如 PH-01）或表格里的完整批次号后再办理。禁止使用内部数字编号。")
    return "\n".join(lines)


def format_process_names(parts: list[Any], fallback: str = "") -> str:
    names = []
    seen = set()
    for part in parts:
        if not isinstance(part, dict):
            continue
        process = str(part.get("processName") or part.get("process_name") or "").strip()
        if process and process not in seen:
            seen.add(process)
            names.append(process)
    fallback = str(fallback or "").strip()
    if fallback and fallback not in seen:
        names.append(fallback)
    return "、".join(names)


def format_quotes(invitations: list[Any]) -> str:
    labels = []
    for invite in invitations:
        if not isinstance(invite, dict):
            continue
        amount = money(invite.get("quoteAmount") if "quoteAmount" in invite else invite.get("quote_amount"))
        if amount is None:
            continue
        name = str(invite.get("supplierName") or invite.get("supplier_name") or "").strip() or "加工商"
        labels.append(f"{name} {amount}")
    return "；".join(labels)


def invitation_supplier_label(invitation: dict[str, Any]) -> str:
    return str(
        invitation.get("supplierName")
        or invitation.get("supplier_name")
        or invitation.get("supplierCode")
        or invitation.get("supplier_code")
        or ""
    ).strip()


def invitation_supplier_names(invitations: list[Any], dispatch_pending: str = "") -> str:
    names = []
    seen = set()
    for invite in invitations:
        if not isinstance(invite, dict):
            continue
        name = invitation_supplier_label(invite)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    if not names:
        for name in str(dispatch_pending or "").split("、"):
            text = name.strip()
            if text and text not in seen:
                seen.add(text)
                names.append(text)
    return "、".join(names)


def pending_quote_suppliers(invitations: list[Any], dispatch_pending: str = "") -> str:
    names = []
    seen = set()
    for invite in invitations:
        if not isinstance(invite, dict):
            continue
        status = str(invite.get("status") or "").strip().lower()
        if status and status not in PENDING_INVITE_STATUS:
            continue
        name = invitation_supplier_label(invite)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    if not names:
        for name in str(dispatch_pending or "").split("、"):
            text = name.strip()
            if text and text not in seen:
                seen.add(text)
                names.append(text)
    return "、".join(names)


def _invitation_codes(invitation: dict[str, Any]) -> set[str]:
    return {
        _norm_code(invitation.get("supplierCode")),
        _norm_code(invitation.get("supplier_code")),
        _norm_code(invitation.get("supplierName")),
        _norm_code(invitation.get("supplier_name")),
    } - {""}


def _invitation_supplier_id(invitation: dict[str, Any]) -> int | None:
    supplier_id = invitation.get("supplierId") or invitation.get("supplier_id")
    if supplier_id is None:
        return None
    return int(supplier_id)


def listed_supplier_names(item: dict[str, Any]) -> list[str]:
    names = []
    seen = set()
    for invitation in item.get("invitations") or []:
        if not isinstance(invitation, dict):
            continue
        name = invitation_supplier_label(invitation)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    fallback = str(item.get("pendingQuoteSuppliers") or item.get("supplierName") or "").strip()
    if fallback:
        for name in fallback.split("、"):
            text = name.strip()
            if text and text not in seen:
                seen.add(text)
                names.append(text)
    return names


def _match_invitations(invitations: list[dict[str, Any]], token: str) -> list[dict[str, Any]]:
    exact = []
    contained = []
    for invitation in invitations:
        codes = _invitation_codes(invitation)
        if token in codes:
            exact.append(invitation)
            continue
        if len(token) >= 2 and any(token in code or code in token for code in codes):
            contained.append(invitation)
    return exact or contained


def match_candidates_as_invitations(rows: list[Any]) -> list[dict[str, Any]]:
    invitations = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        supplier_id = row.get("supplierId") or row.get("supplier_id")
        if supplier_id is None:
            continue
        supplier_id = int(supplier_id)
        if supplier_id in seen:
            continue
        seen.add(supplier_id)
        invitations.append({
            "supplierId": supplier_id,
            "supplierCode": str(row.get("supplierCode") or row.get("supplier_code") or "").strip(),
            "supplierName": str(row.get("supplierName") or row.get("supplier_name") or "").strip(),
            "status": "matched",
        })
    return invitations


def invitations_from_match_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    source = payload if isinstance(payload, dict) else {}
    data = source.get("data") if isinstance(source.get("data"), dict) else source
    rows: list[Any] = []
    groups = data.get("groups") if isinstance(data, dict) else {}
    if isinstance(groups, dict):
        for key in ("A", "B", "C"):
            rows.extend(item for item in (groups.get(key) or []) if isinstance(item, dict))
    if isinstance(data, dict):
        rows.extend(item for item in (data.get("rows") or []) if isinstance(item, dict))
    return match_candidates_as_invitations(rows)


def fetch_match_candidates(project_id: Any) -> list[dict[str, Any]]:
    return load_match_candidates(project_id)


def attach_match_candidates(item: dict[str, Any]) -> dict[str, Any]:
    if str(item.get("station") or "") != "inquiry_send":
        return item
    invitations = [invite for invite in (item.get("invitations") or []) if isinstance(invite, dict)]
    if any(_invitation_supplier_id(invite) is not None for invite in invitations):
        return item
    matched = fetch_match_candidates(item.get("projectId") or item.get("project_id"))
    if not matched:
        return item
    item["invitations"] = matched
    names = invitation_supplier_names(matched)
    item["pendingQuoteSuppliers"] = names
    if not item.get("supplierName"):
        item["supplierName"] = matched[0].get("supplierName") or ""
    if not item.get("supplierCode"):
        item["supplierCode"] = matched[0].get("supplierCode") or ""
    if item.get("supplierId") is None:
        item["supplierId"] = matched[0].get("supplierId")
    return item


def resolve_suppliers(item: dict[str, Any], suppliers: list[str] | None = None) -> tuple[list[int], list[str]]:
    item = attach_match_candidates(item)
    invitations = [invite for invite in (item.get("invitations") or []) if isinstance(invite, dict)]
    wanted = [_norm_code(name) for name in (suppliers or []) if str(name).strip()]
    if not wanted:
        found: list[int] = []
        labels: list[str] = []
        seen = set()
        for invitation in invitations:
            supplier_id = _invitation_supplier_id(invitation)
            if supplier_id is None or supplier_id in seen:
                continue
            seen.add(supplier_id)
            found.append(supplier_id)
            labels.append(invitation_supplier_label(invitation) or str(supplier_id))
        if not found:
            raise DomainError(
                "NOT_FOUND",
                "ERP 选商结果里还没有可发询价的加工商。请先在 ERP 待发询价页确认匹配名单",
                404,
            )
        return found, labels
    found = []
    labels = []
    listed = "、".join(listed_supplier_names(item))
    hint = f"本单已匹配：{listed}" if listed else "本单还没有已匹配加工商"
    for raw, token in zip((name for name in (suppliers or []) if str(name).strip()), wanted):
        matches = _match_invitations(invitations, token)
        if not matches:
            raise DomainError(
                "NOT_FOUND",
                f"查询结果中没有加工商「{raw}」。{hint}。请用名单里的编码或全称，不要自行改写",
                404,
            )
        if len(matches) > 1:
            names = "、".join(invitation_supplier_label(item) or "?" for item in matches)
            raise DomainError("AMBIGUOUS", f"「{raw}」对应多家加工商：{names}。请用完整名称或编码", 409)
        supplier_id = _invitation_supplier_id(matches[0])
        if supplier_id is None:
            raise DomainError("NOT_FOUND", f"加工商「{raw}」没有可用编码，请重新查询待发询价", 404)
        found.append(supplier_id)
        labels.append(invitation_supplier_label(matches[0]) or str(raw).strip())
    return found, labels


def resolve_supplier_ids(item: dict[str, Any], suppliers: list[str] | None = None) -> list[int]:
    ids, _labels = resolve_suppliers(item, suppliers)
    return ids


def parse_question(question: str) -> dict[str, str]:
    text = question or ""
    batch = MOLD_BATCH.search(text)
    family = None if batch else MOLD_FAMILY.search(text)
    project = PROJECT_NO.search(text)
    order = ORDER_NO.search(text)
    station = ""
    if any(word in text for word in ("全部拒单", "都拒", "拒完")):
        station = "exhausted"
    elif any(word in text for word in ("待接单", "还没接", "未接单")) or (
        "接单" in text and "拒" not in text and "上限" not in text
    ):
        station = "accept"
    elif any(word in text for word in ("审批中", "在审批", "下单审批", "待审批")):
        station = "order_approval"
    elif any(word in text for word in ("待下单", "成交价")):
        station = "place_order"
    elif any(word in text for word in ("待报价", "还没报价", "加工商报价")):
        station = "supplier_quote"
    elif any(word in text for word in ("待发询价", "还没发询价", "未发询价")):
        station = "inquiry_send"
    elif any(word in text for word in ("待填价", "待采购填报价", "待填报价", "还没填", "我方报价", "接单上限")):
        station = "buyer_quote"
    outsource_type = ""
    has_part = "零件委外" in text
    has_operation = "工序委外" in text
    has_mold = "模具委外" in text
    if has_part and not has_operation and not has_mold:
        outsource_type = "part"
    elif has_operation and not has_part and not has_mold:
        outsource_type = "operation"
    elif has_mold and not has_part and not has_operation:
        outsource_type = "mold"
    return {
        "station": station,
        "mold_family": "" if batch else (family.group(1).upper() if family else ""),
        "mold_batch": batch.group(1).upper() if batch else "",
        "project_no": project.group(1).upper() if project else "",
        "order_no": order.group(1).upper() if order else "",
        "outsource_type": outsource_type,
    }


def is_spoken_buyer_quote(question: str) -> bool:
    text = question or ""
    if any(token in text for token in ("有几个", "有哪些", "有没有", "不要填", "不填报价", "不填价格")):
        return False
    return any(token in text for token in BUYER_QUOTE_SPEECH)


def is_spoken_inquiry_send(question: str) -> bool:
    text = question or ""
    if any(token in text for token in ("有几个", "有哪些", "有没有", "不要发", "不发询价", "不发送询价")):
        return False
    return any(token in text for token in INQUIRY_SEND_SPEECH)


def inquiry_send_identity_locked(arguments: dict[str, Any]) -> bool:
    return bool(
        str(arguments.get("order_no") or "").strip()
        or str(arguments.get("batch") or "").strip()
        or str(arguments.get("mold") or "").strip()
        or str(arguments.get("part") or "").strip()
    )


def _identity_arguments(source: str) -> dict[str, Any]:
    parsed = parse_question(source)
    arguments: dict[str, Any] = {}
    order = ORDER_NO.search(source or "")
    if order:
        arguments["order_no"] = order.group(1).upper()
    if parsed.get("mold_family"):
        arguments["mold"] = parsed["mold_family"]
    if parsed.get("mold_batch"):
        arguments["batch"] = parsed["mold_batch"]
        arguments.setdefault("mold", parsed["mold_batch"].split("-P", 1)[0])
    return arguments


def _sendable_board_items(items: list[Any] | None) -> list[dict[str, Any]]:
    sendable = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        station = str(item.get("station") or item.get("stationLabel") or "").strip()
        if station not in {"待发询价", "inquiry_send"}:
            continue
        if not any(str(item.get(key) or "").strip() for key in (
            "orderNo", "order_no", "mold", "moldFamily", "moldNo", "batch", "moldBatch",
        )):
            continue
        sendable.append(item)
    return sendable


def _arguments_from_send_item(item: dict[str, Any]) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    order = str(item.get("orderNo") or item.get("order_no") or "").strip()
    mold = str(item.get("mold") or item.get("moldFamily") or item.get("moldNo") or "").strip()
    batch = str(item.get("batch") or item.get("moldBatch") or "").strip()
    if order:
        arguments["order_no"] = order
    if batch:
        arguments["batch"] = batch.split("、", 1)[0].strip()
        if MOLD_BATCH_CODE.match(arguments["batch"]):
            arguments.setdefault("mold", arguments["batch"].split("-P", 1)[0])
    if mold:
        first = mold.split("、", 1)[0].strip()
        if MOLD_BATCH_CODE.match(first) and not arguments.get("batch"):
            arguments["batch"] = first
            arguments.setdefault("mold", first.split("-P", 1)[0])
        elif first:
            arguments.setdefault("mold", first.split("-P", 1)[0] if "-P" in first else first)
    family, item_batch = item_identity(item)
    if item_batch and not arguments.get("batch"):
        arguments["batch"] = item_batch.split("、", 1)[0].strip()
        if MOLD_BATCH_CODE.match(arguments["batch"]):
            arguments.setdefault("mold", arguments["batch"].split("-P", 1)[0])
    if family:
        arguments.setdefault("mold", family.split("-P", 1)[0] if "-P" in family else family)
    part = part_code_from_item(item)
    if part:
        arguments["part"] = part
    return arguments


def apply_first_pending_send_row(arguments: dict[str, Any], *, require_unique: bool = False) -> dict[str, Any]:
    """Lock 第一行/唯一待发询价 to the first pending-send enquiry."""
    parsed = {
        "station": "inquiry_send",
        "mold_family": str(arguments.get("mold") or ""),
        "mold_batch": str(arguments.get("batch") or ""),
        "project_no": "",
        "outsource_type": "",
    }
    if parsed["mold_batch"] and MOLD_BATCH_CODE.match(parsed["mold_batch"]):
        parsed["mold_family"] = ""
    try:
        items = query_items(parsed)
    except Exception:
        return arguments
    if not items:
        return arguments
    if require_unique and len(items) != 1:
        return arguments
    filled = _arguments_from_send_item(items[0])
    filled["board_row"] = 1
    return filled


def find_unique_station_item(station: str) -> dict[str, Any] | None:
    parsed = {
        "station": station,
        "mold_family": "",
        "mold_batch": "",
        "project_no": "",
        "outsource_type": "",
    }
    try:
        items = query_items(parsed)
    except Exception:
        return None
    if len(items) != 1 or not items[0].get("inquiryId"):
        return None
    return items[0]


def parse_spoken_inquiry_send(question: str, context_text: str = "") -> dict[str, Any] | None:
    if not is_spoken_inquiry_send(question):
        return None
    row_no = spoken_board_row_number(question)
    if row_no and not _question_locks_identity(question):
        item = item_from_visible_board_row(row_no)
        if item and _item_is_station(item, "inquiry_send"):
            filled = _arguments_from_send_item(item)
            filled["board_row"] = row_no
            return filled if inquiry_send_identity_locked(filled) else None
        return None
    first_row = bool(FIRST_BOARD_ROW.search(question or "") or PENDING_FIRST_QUOTE.search(question or ""))
    ignore_history = bool(
        first_row
        or NAMED_SEND_STATION.search(question or "")
        or THIS_ORDER.search(question or "")
    )
    if ignore_history:
        filled = apply_first_pending_send_row({}, require_unique=not first_row)
        if inquiry_send_identity_locked(filled):
            return filled
        return None
    arguments = _identity_arguments(f"{question or ''}\n{context_text or ''}")
    if not inquiry_send_identity_locked(arguments):
        return None
    return arguments


def _visible_row_item_for_station(question: str, station: str) -> tuple[int, dict[str, Any]] | None:
    row_no = spoken_board_row_number(question)
    if not row_no or _question_locks_identity(question):
        return None
    item = item_from_visible_board_row(row_no)
    if not item or not _item_is_station(item, station):
        return None
    return row_no, item


def send_arguments_from_board(question: str, items: list[Any] | None) -> dict[str, Any] | None:
    if not is_spoken_inquiry_send(question):
        return None
    visible = _visible_row_item_for_station(question, "inquiry_send")
    if visible:
        row_no, chosen = visible
        arguments = _arguments_from_send_item(chosen)
        arguments["board_row"] = row_no
        return arguments if inquiry_send_identity_locked(arguments) else None
    sendable = _sendable_board_items(items)
    first_row = bool(FIRST_BOARD_ROW.search(question or "") or PENDING_FIRST_QUOTE.search(question or ""))
    if first_row:
        chosen = sendable[0] if sendable else None
    elif len(sendable) == 1:
        chosen = sendable[0]
    else:
        return None
    if chosen is None:
        return None
    arguments = _arguments_from_send_item(chosen)
    if first_row:
        arguments["board_row"] = 1
    if not inquiry_send_identity_locked(arguments):
        return None
    return arguments


def _quoteable_board_items(items: list[Any] | None) -> list[dict[str, Any]]:
    quoteable = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        station = str(item.get("station") or item.get("stationLabel") or "").strip()
        if station not in {"待采购填报价", "待填价", "buyer_quote"}:
            continue
        if not any(str(item.get(key) or "").strip() for key in (
            "orderNo", "order_no", "mold", "moldFamily", "moldNo", "batch", "moldBatch",
        )):
            continue
        quoteable.append(item)
    return quoteable


def _arguments_from_quote_item(item: dict[str, Any], amounts: dict[str, Any]) -> dict[str, Any]:
    arguments = dict(amounts)
    order = str(item.get("orderNo") or item.get("order_no") or "").strip()
    mold = str(item.get("mold") or item.get("moldFamily") or item.get("moldNo") or "").strip()
    batch = str(item.get("batch") or item.get("moldBatch") or "").strip()
    if order:
        arguments["order_no"] = order
    if batch:
        first_batch = batch.split("、", 1)[0].strip()
        arguments["batch"] = first_batch
        if MOLD_BATCH_CODE.match(first_batch):
            arguments.setdefault("mold", first_batch.split("-P", 1)[0])
    if mold:
        first = mold.split("、", 1)[0].strip()
        if MOLD_BATCH_CODE.match(first) and not arguments.get("batch"):
            arguments["batch"] = first
            arguments.setdefault("mold", first.split("-P", 1)[0])
        elif first:
            arguments.setdefault("mold", first.split("-P", 1)[0] if "-P" in first else first)
    family, item_batch = item_identity(item)
    if item_batch and not arguments.get("batch"):
        arguments["batch"] = item_batch.split("、", 1)[0].strip()
        if MOLD_BATCH_CODE.match(arguments["batch"]):
            arguments.setdefault("mold", arguments["batch"].split("-P", 1)[0])
    if family:
        arguments.setdefault("mold", family.split("-P", 1)[0] if "-P" in family else family)
    part = part_code_from_item(item)
    if part:
        arguments["part"] = part
    return arguments


def apply_first_pending_quote_row(arguments: dict[str, Any]) -> dict[str, Any]:
    """Lock 待排/待排列第一 to the first pending-fill-quote enquiry."""
    parsed = {
        "station": "buyer_quote",
        "mold_family": "",
        "mold_batch": "",
        "project_no": "",
        "outsource_type": "",
    }
    try:
        items = query_items(parsed)
    except Exception:
        return arguments
    if not items:
        return arguments
    filled = _arguments_from_quote_item(items[0], {
        key: arguments[key]
        for key in ("our_quote_amount", "auto_accept_max_amount")
        if key in arguments
    })
    filled["board_row"] = 1
    return filled


def quote_arguments_from_board(question: str, items: list[Any] | None) -> dict[str, Any] | None:
    if not is_spoken_buyer_quote(question):
        return None
    quote = QUOTE_AMOUNT.search(question or "")
    ceiling = MAX_AMOUNT.search(question or "")
    if quote is None or ceiling is None:
        return None
    amounts = {
        "our_quote_amount": float(quote.group(1)),
        "auto_accept_max_amount": float(ceiling.group(1)),
    }
    visible = _visible_row_item_for_station(question, "buyer_quote")
    if visible:
        row_no, chosen = visible
        filled = _arguments_from_quote_item(chosen, amounts)
        filled["board_row"] = row_no
        return filled if buyer_quote_identity_locked(filled) else None
    quoteable = _quoteable_board_items(items)
    if PENDING_FIRST_QUOTE.search(question or ""):
        chosen = quoteable[0] if quoteable else None
    elif len(quoteable) == 1:
        chosen = quoteable[0]
    else:
        return None
    if chosen is None:
        return None
    arguments = _arguments_from_quote_item(chosen, amounts)
    if not buyer_quote_identity_locked(arguments):
        filled = apply_spoken_board_row(dict(arguments), "第1行")
        arguments.update({key: value for key, value in filled.items() if value not in (None, "")})
    if not buyer_quote_identity_locked(arguments):
        return None
    return arguments


def buyer_quote_identity_locked(arguments: dict[str, Any]) -> bool:
    """Fill-quote form only after mold is locked, plus order or batch+part."""
    mold = str(arguments.get("mold") or "").strip()
    order_no = str(arguments.get("order_no") or "").strip()
    batch = str(arguments.get("batch") or "").strip()
    part = str(arguments.get("part") or "").strip()
    if not mold:
        return False
    if order_no:
        return True
    return bool(batch and part)


def part_code_from_item(item: dict[str, Any]) -> str:
    for row in item.get("parts") or []:
        if not isinstance(row, dict):
            continue
        token = normalize_part_token(row.get("partNo") or row.get("part_no"))
        if token:
            return token
    return normalize_part_token(item.get("partDetails") or "")


def spoken_part_token(question: str) -> str:
    text = question or ""
    found = []
    for match in PART_CODE.finditer(text):
        code = normalize_part_token(match.group(1))
        if not code or re.fullmatch(r"M\d{5,}(?:-P\d+)?", code) or re.fullmatch(r"P\d+", code):
            continue
        if code not in found:
            found.append(code)
    if len(found) == 1:
        return found[0]
    match = PART_NO.search(text)
    return normalize_part_token(match.group(1)) if match else ""


def spoken_board_row_number(question: str) -> int | None:
    match = ROW_SPOKEN.search(question or "")
    if not match:
        return None
    token = match.group(1)
    if token.isdigit():
        return int(token)
    return CN_ROW.get(token)


def _question_locks_identity(question: str) -> bool:
    parsed = parse_question(question or "")
    return bool(
        parsed.get("mold_family")
        or parsed.get("mold_batch")
        or spoken_part_token(question)
        or ORDER_NO.search(question or "")
    )


def item_from_visible_board_row(row_no: int) -> dict[str, Any] | None:
    """Row N of the same unfiltered board the '查看完整表格' card shows."""
    parsed = {
        "station": "",
        "mold_family": "",
        "mold_batch": "",
        "project_no": "",
        "outsource_type": "",
    }
    try:
        items = query_items(parsed)
    except Exception:
        return None
    if row_no < 1 or row_no > len(items):
        return None
    return items[row_no - 1]


def _item_is_station(item: dict[str, Any], station: str) -> bool:
    raw = str(item.get("station") or "").strip()
    label = str(item.get("stationLabel") or "").strip()
    if not raw and not label:
        return station == "buyer_quote"
    labels = {station, STATIONS.get(station, "")}
    return raw in labels or label in labels


def apply_spoken_board_row(arguments: dict[str, Any], question: str) -> dict[str, Any]:
    """Lock 第N行 to the same enquiry the follow-up board would show."""
    row_no = spoken_board_row_number(question)
    if not row_no:
        return arguments
    tokens = _batch_tokens(arguments.get("batch"))
    query_batch = tokens[0] if tokens and MOLD_BATCH_CODE.match(tokens[0]) else ""
    parsed = {
        "station": "buyer_quote",
        "mold_family": "" if query_batch else str(arguments.get("mold") or ""),
        "mold_batch": query_batch,
        "project_no": "",
        "outsource_type": "",
    }
    try:
        items = query_items(parsed)
    except Exception:
        return arguments
    if not items or row_no < 1 or row_no > len(items):
        return arguments
    item = items[row_no - 1]
    part = part_code_from_item(item)
    if part:
        arguments["part"] = part
    family, batch = item_identity(item)
    if family:
        arguments["mold"] = family
    if batch:
        arguments["batch"] = batch
    order_no = str(item.get("orderNo") or "").strip()
    if order_no:
        arguments["order_no"] = order_no
    return arguments


def parse_spoken_buyer_quote(question: str, context_text: str = "") -> dict[str, Any] | None:
    """Parse fill-quote speech into prepare_erp_outsource_buyer_quote arguments."""
    if not is_spoken_buyer_quote(question):
        return None
    quote = QUOTE_AMOUNT.search(question or "")
    ceiling = MAX_AMOUNT.search(question or "")
    if quote is None or ceiling is None:
        return None
    amounts = {
        "our_quote_amount": float(quote.group(1)),
        "auto_accept_max_amount": float(ceiling.group(1)),
    }
    row_no = spoken_board_row_number(question)
    if row_no and not _question_locks_identity(question):
        item = item_from_visible_board_row(row_no)
        if item and _item_is_station(item, "buyer_quote"):
            filled = _arguments_from_quote_item(item, amounts)
            filled["board_row"] = row_no
            return filled if buyer_quote_identity_locked(filled) else None
        return None
    identity_source = f"{question or ''}\n{context_text or ''}"
    parsed = parse_question(identity_source)
    order = ORDER_NO.search(identity_source)
    arguments: dict[str, Any] = dict(amounts)
    if order:
        arguments["order_no"] = order.group(1).upper()
    if parsed.get("mold_family"):
        arguments["mold"] = parsed["mold_family"]
    if parsed.get("mold_batch"):
        arguments["batch"] = parsed["mold_batch"]
        arguments.setdefault("mold", parsed["mold_batch"].split("-P", 1)[0])
    part = spoken_part_token(question)
    if part:
        arguments["part"] = part
    row_no = spoken_board_row_number(question)
    if row_no and (arguments.get("mold") or arguments.get("batch")):
        arguments["board_row"] = row_no
        arguments = apply_spoken_board_row(arguments, question)
    elif PENDING_FIRST_QUOTE.search(question or "") and not buyer_quote_identity_locked(arguments):
        arguments = apply_first_pending_quote_row(arguments)
    if not buyer_quote_identity_locked(arguments):
        return None
    return arguments


def classify(row: dict[str, Any]) -> str | None:
    awarded = str(row.get("awarded_stage") or "")
    order_status = str(row.get("awarded_status") or "")
    outsource_type = str(row.get("outsource_type") or "")
    if awarded == "delivered":
        return None
    if row.get("dispatch_exhausted") or (outsource_type == "operation" and order_status == "rejected"):
        return "exhausted"
    if awarded in AWARDED_TODO_STAGES:
        return AWARDED_TODO_STAGES[awarded]
    # 已接单后的履约阶段（原料收货、生产、在途）不再算采购待办。
    if awarded:
        return None
    if row.get("inquiry_id") is None:
        return None
    status = str(row.get("inquiry_status") or "draft")
    ready = row.get("our_quote_amount") is not None and row.get("auto_accept_max_amount") is not None
    invites = int(row.get("invitation_count") or 0)
    quoted = int(row.get("quoted_count") or 0)
    if status in {"awarded", "partial_awarded"} or (status == "closed" and ready):
        return "accept"
    if outsource_type in {"part", "mold"}:
        if not ready:
            return "buyer_quote"
        if invites <= 0:
            return "inquiry_send"
        if quoted <= 0:
            return "supplier_quote"
        if row.get("final_deal_amount") is None:
            return "place_order"
        return "order_approval"
    if invites <= 0:
        return "inquiry_send"
    if quoted <= 0:
        return "supplier_quote"
    if row.get("final_deal_amount") is None:
        return "place_order"
    return "order_approval"


def query_params(parsed: dict[str, str]) -> dict[str, str]:
    return {
        "station": parsed.get("station") or "",
        "mold_family": parsed.get("mold_family") or "",
        "mold_batch": parsed.get("mold_batch") or "",
        "project_no": parsed.get("project_no") or "",
        "order_no": parsed.get("order_no") or "",
        "outsource_type": parsed.get("outsource_type") or "",
    }


def item_from_row(row: dict[str, Any], station: str) -> dict[str, Any]:
    outsource_type = str(row.get("outsource_type") or "")
    parts = as_list(row.get("parts"))
    invitations = as_list(row.get("invitations"))
    quotes = format_quotes(invitations)
    awarded_amount = money(row.get("awarded_amount"))
    pending = pending_quote_suppliers(invitations)
    if station == "inquiry_send":
        pending = invitation_supplier_names(invitations) or pending
    if not pending and station in {"inquiry_send", "supplier_quote", "exhausted"}:
        pending = invitation_supplier_names([], str(row.get("pending_dispatch_suppliers") or ""))
    if not quotes and awarded_amount is not None:
        supplier = str(row.get("supplier_name") or "").strip()
        quotes = f"{supplier} {awarded_amount}".strip() if supplier else str(awarded_amount)
    mold_no = row.get("mold_no") or ""
    mold_family, mold_batch = mold_labels(mold_no, parts)
    return {
        "station": station,
        "stationLabel": STATIONS[station],
        "outsourceType": outsource_type,
        "outsourceTypeLabel": OUTSOURCE_TYPE_LABELS.get(outsource_type, outsource_type or "委外"),
        "projectId": row.get("project_id"),
        "inquiryId": row.get("inquiry_id"),
        "orderId": row.get("awarded_order_id"),
        "orderNo": row.get("awarded_order_no") or "",
        "moldNo": mold_no,
        "moldFamily": mold_family,
        "moldBatch": mold_batch,
        "parts": parts,
        "partDetails": format_parts(parts),
        "processNames": format_process_names(parts, str(row.get("process_name") or "")),
        "referenceTotal": money(row.get("reference_total") or row.get("reference_total_amount")),
        "ourQuoteAmount": money(row.get("our_quote_amount")),
        "autoAcceptMaxAmount": money(row.get("auto_accept_max_amount")),
        "supplierQuotes": quotes,
        "finalDealAmount": money(row.get("final_deal_amount")) or awarded_amount,
        "pendingQuoteSuppliers": pending,
        "supplierName": row.get("supplier_name") or "",
        "supplierId": row.get("supplier_id"),
        "supplierCode": row.get("supplier_code") or "",
        "invitations": invitations,
    }


def query_items(parsed: dict[str, str]) -> list[dict[str, Any]]:
    scoped = query_params(parsed)
    sql_params = {key: value for key, value in scoped.items() if key != "order_no"}
    items = []
    for row in fetch_all(SQL, sql_params):
        station = classify(row)
        if station is None:
            continue
        if scoped["station"] and station != scoped["station"]:
            continue
        items.append(attach_match_candidates(item_from_row(row, station)))
    return items


def present(parsed: dict[str, str], items: list[dict[str, Any]]) -> dict[str, Any]:
    truncated = len(items) > ROW_LIMIT
    visible = items[:ROW_LIMIT]
    counts = {label: 0 for label in STATIONS.values()}
    type_counts = {label: 0 for label in OUTSOURCE_TYPE_LABELS.values()}
    for item in visible:
        counts[item["stationLabel"]] += 1
        type_counts[item.get("outsourceTypeLabel") or ""] = type_counts.get(item.get("outsourceTypeLabel") or "", 0) + 1
    scoped = query_params(parsed)
    scope = scoped["order_no"] or scoped["project_no"] or scoped["mold_batch"] or scoped["mold_family"] or "ERP 委外待办"
    station_label = STATIONS.get(scoped["station"], "全部分站")
    type_label = OUTSOURCE_TYPE_LABELS.get(scoped["outsource_type"], "零件/工序委外")
    summary = f"{scope} 的{type_label}{station_label}共 {len(visible)} 条。"
    summary += "\n" + "\n".join(f"- {label}：{count} 条" for label, count in counts.items() if count)
    summary += "\n" + "\n".join(f"- {label}：{count} 条" for label, count in type_counts.items() if count)
    if not visible:
        summary += "\n本次查询结果是 0 条。"
    else:
        lines = []
        for item in visible[:30]:
            details = item["partDetails"]
            if len(details) > 80:
                details = details[:80] + "…"
            suppliers = item.get("pendingQuoteSuppliers") or item.get("supplierName") or ""
            line = (
                f"{item['stationLabel']} {item['outsourceTypeLabel']} "
                f"{item.get('orderNo') or '尚未下单'} {item.get('moldFamily') or item['moldNo']} "
                f"{item.get('moldBatch') or item['moldNo']} {details}"
            )
            if suppliers:
                line += f" 加工商 {suppliers}"
            lines.append(line)
        summary += "\n" + "\n".join(f"- {line}".rstrip() for line in lines)
        if len(visible) > 30:
            summary += "\n明细只展开前 30 条，完整列表在 items。"
    if truncated:
        summary += f"\n结果超过 {ROW_LIMIT} 条，只返回前 {ROW_LIMIT} 条。"
    return {
        "station": scoped["station"] or "all",
        "scope": scope,
        "database": "erp",
        "counts": counts,
        "typeCounts": {key: value for key, value in type_counts.items() if value},
        "truncated": truncated,
        "summary": summary,
        "items": visible,
    }


def _identifier_hit(value: Any, tokens: list[str] | None) -> bool:
    if not tokens:
        return False
    text = str(value or "").strip().casefold()
    return bool(text) and any(text == str(token).strip().casefold() for token in tokens if str(token).strip())


def invitation_matches_processor(invitation: dict[str, Any], tokens: list[str] | None) -> bool:
    if tokens is None:
        return True
    return any(
        _identifier_hit(invitation.get(key), tokens)
        for key in ("supplierCode", "supplierId")
    )


def item_mentions_processor(item: dict[str, Any], tokens: list[str] | None) -> bool:
    if tokens is None:
        return True
    if not tokens:
        return False
    station = str(item.get("station") or "")
    has_awarded_supplier = bool(item.get("supplierCode") or item.get("supplierId"))
    if station in {"accept", "order_approval", "exhausted"} and has_awarded_supplier:
        # Once ERP has chosen a supplier only that supplier may see the row;
        # historical invitations must not leak the awarded order.
        return any(_identifier_hit(item.get(key), tokens) for key in ("supplierCode", "supplierId"))
    # No awarded order row yet (for example the inquiry is closed but the
    # order is still pending_match, or the final price was just entered):
    # fall back to the invitation list, which is still exact per supplier.
    return any(
        invitation_matches_processor(invitation, tokens)
        for invitation in item.get("invitations") or []
    )


def strip_internal_prices(item: dict[str, Any]) -> dict[str, Any]:
    visible = dict(item)
    for key in ("referenceTotal", "ourQuoteAmount", "autoAcceptMaxAmount", "finalDealAmount"):
        visible[key] = None
    return visible


def processor_next_action(item: dict[str, Any]) -> dict[str, Any]:
    station = str(item.get("station") or "")
    outsource_type = str(item.get("outsourceType") or "")
    if station == "supplier_quote":
        return {
            "action": "quote",
            "hint": "提交本加工商报价。提交后重新查询：出现待接单则提醒接单；仍待下单或审批中则等采购填成交价、主管和总经理审批。",
        }
    if station == "accept":
        if outsource_type == "operation":
            hint = "接单或拒单。拒单后 ERP 自动把同一张工序单转给下一家，不要自己选下一家。"
        else:
            hint = "接单或拒单。拒单后由采购员重选加工商再发询价。"
        family, batch = item_identity(item)
        return {
            "action": "accept_or_reject",
            "orderNo": item.get("orderNo") or "",
            "mold": family,
            "batch": batch,
            "hint": hint,
        }
    if station in {"place_order", "order_approval"}:
        return {
            "action": "wait",
            "hint": "报价已超出直接接单区间。等采购员填成交价，再等主管、总经理审批。不要自己接单或改成交价。",
        }
    if station == "exhausted":
        return {"action": "wait", "hint": "候选加工商已全部拒单，等采购员重派。"}
    return {"action": "wait", "hint": "当前不是本加工商可办阶段。"}


def clip_for_processor(item: dict[str, Any], tokens: list[str] | None) -> dict[str, Any]:
    visible = strip_internal_prices(item)
    invitations = [
        invitation for invitation in item.get("invitations") or []
        if invitation_matches_processor(invitation, tokens)
    ]
    visible["invitations"] = invitations
    if invitations:
        visible["pendingQuoteSuppliers"] = pending_quote_suppliers(invitations)
        visible["supplierQuotes"] = format_quotes(invitations)
        visible["supplierName"] = invitations[0].get("supplierName") or visible.get("supplierName")
    visible["nextAction"] = processor_next_action(visible)
    return visible


def _scan_items(mold: str | None = None) -> list[dict[str, Any]]:
    parsed = {
        "station": "",
        "mold_family": "",
        "mold_batch": "",
        "project_no": "",
        "outsource_type": "",
    }
    text = str(mold or "").strip().upper()
    if re.fullmatch(r"M\d{5,}-P\d+", text):
        parsed["mold_batch"] = text
    elif re.fullmatch(r"M\d{5,}", text):
        parsed["mold_family"] = text
    return query_items(parsed)


def find_item_by_board_row(
    row_no: int,
    *,
    mold: str | None = None,
    batch: str | None = None,
    station: str = "buyer_quote",
) -> dict[str, Any] | None:
    """Pick 第N行 from the same station board the user just named."""
    mold, batch = _promote_batch_code(str(mold or "").strip().upper(), str(batch or "").strip().upper())
    tokens = _batch_tokens(batch)
    query_batch = tokens[0] if tokens and MOLD_BATCH_CODE.match(tokens[0]) else ""
    parsed = {
        "station": station or "buyer_quote",
        "mold_family": "" if query_batch else mold,
        "mold_batch": query_batch,
        "project_no": "",
        "outsource_type": "",
    }
    if parsed["station"] == "buyer_quote" and not parsed["mold_family"] and not parsed["mold_batch"]:
        return None
    try:
        items = query_items(parsed)
    except Exception:
        return None
    if row_no < 1 or row_no > len(items):
        return None
    item = items[row_no - 1]
    return item if item.get("inquiryId") else None


def find_item(inquiry_id: int, *, mold: str | None = None) -> dict[str, Any] | None:
    for item in _scan_items(mold):
        if item.get("inquiryId") == inquiry_id:
            return item
    return None


def find_item_by_order(order_id: int, *, mold: str | None = None) -> dict[str, Any] | None:
    for item in _scan_items(mold):
        if item.get("orderId") == order_id:
            return item
    return None


def _query_station_items(station: str) -> list[dict[str, Any]]:
    parsed = {
        "station": station,
        "mold_family": "",
        "mold_batch": "",
        "project_no": "",
        "outsource_type": "",
    }
    try:
        return query_items(parsed)
    except Exception:
        return []


def find_item_by_identity(
    *,
    order_no: str | None = None,
    mold: str | None = None,
    batch: str | None = None,
    part: str | None = None,
    require_inquiry: bool = False,
    station: str | None = None,
) -> dict[str, Any] | None:
    """Locate one enquiry.

    When *station* is set (办理), search that station first.  Same mold/batch/part
    on another station must not count.  If that station has exactly one row,
    that row is the target even if leftover mold/batch from an earlier turn
    would miss it.
    """
    order_no = str(order_no or "").strip()
    mold, batch = _promote_batch_code(str(mold or "").strip().upper(), str(batch or "").strip().upper())
    part = normalize_part_token(part)
    if not order_no and not mold and not batch and not part and not station:
        raise DomainError("INVALID_TOOL_INPUT", "请用订单号、模具号、批次号或零件号定位，不要使用内部数字编号")

    def matches(item: dict[str, Any]) -> bool:
        return item_matches_identity(
            item, order_no=order_no, mold=mold, batch=batch
        ) and item_matches_part(item, part)

    if station:
        pool = _query_station_items(station)
        if require_inquiry:
            pool = [item for item in pool if item.get("inquiryId")]
        hits = [item for item in pool if matches(item)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) == 0 and len(pool) == 1:
            return pool[0]
        if len(hits) > 1:
            raise DomainError("AMBIGUOUS", _ambiguous_identity_message(hits, part=part), 409)
        if not hits and not pool:
            return None
        if not hits:
            return None
        return hits[0]

    hits = [item for item in _scan_items(batch or mold) if matches(item)]
    if not hits and (order_no or part):
        hits = [item for item in _scan_items(None) if matches(item)]
    if require_inquiry:
        hits = [item for item in hits if item.get("inquiryId")]
    if not hits:
        return None
    if len(hits) > 1:
        raise DomainError("AMBIGUOUS", _ambiguous_identity_message(hits, part=part), 409)
    return hits[0]


def find_invitation(invitation_id: int, *, mold: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for item in _scan_items(mold):
        for invitation in item.get("invitations") or []:
            if invitation.get("invitationId") == invitation_id:
                return item, invitation
    return None, None


def run(parsed: dict[str, str], *, processor_tokens: list[str] | None = None) -> dict[str, Any]:
    items = query_items(parsed)
    order_no = str(parsed.get("order_no") or "").strip()
    if order_no:
        items = [item for item in items if item_matches_identity(item, order_no=order_no)]
    if processor_tokens is not None:
        items = [
            clip_for_processor(item, processor_tokens)
            for item in items
            if item_mentions_processor(item, processor_tokens)
        ]
    return present(parsed, items)
