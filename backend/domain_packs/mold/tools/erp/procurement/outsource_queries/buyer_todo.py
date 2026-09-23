"""Read-only buyer todo station query."""
from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all

MOLD_FAMILY = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,})(?!-P\d+)(?![A-Z0-9])")
MOLD_BATCH = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,}-P\d+)(?![A-Z0-9])")
PROJECT_NO = re.compile(r"(?i)(?<![A-Z0-9])(E\d+-\d+|ENT-BATCH-[A-Z0-9]+)(?![A-Z0-9])")
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


def pending_quote_suppliers(invitations: list[Any], dispatch_pending: str = "") -> str:
    names = []
    seen = set()
    for invite in invitations:
        if not isinstance(invite, dict):
            continue
        status = str(invite.get("status") or "").strip().lower()
        if status not in PENDING_INVITE_STATUS:
            continue
        name = str(invite.get("supplierName") or invite.get("supplier_name") or "").strip()
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


def parse_question(question: str) -> dict[str, str]:
    text = question or ""
    batch = MOLD_BATCH.search(text)
    family = None if batch else MOLD_FAMILY.search(text)
    project = PROJECT_NO.search(text)
    station = ""
    if any(word in text for word in ("全部拒单", "都拒", "拒完")):
        station = "exhausted"
    elif any(word in text for word in ("待接单", "还没接", "未接单")):
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
        "outsource_type": outsource_type,
    }


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
        "outsource_type": parsed.get("outsource_type") or "",
    }


def item_from_row(row: dict[str, Any], station: str) -> dict[str, Any]:
    outsource_type = str(row.get("outsource_type") or "")
    parts = as_list(row.get("parts"))
    invitations = as_list(row.get("invitations"))
    quotes = format_quotes(invitations)
    awarded_amount = money(row.get("awarded_amount"))
    pending = pending_quote_suppliers(invitations)
    if not pending and station in {"inquiry_send", "supplier_quote", "exhausted"}:
        pending = pending_quote_suppliers([], str(row.get("pending_dispatch_suppliers") or ""))
    if not quotes and awarded_amount is not None:
        supplier = str(row.get("supplier_name") or "").strip()
        quotes = f"{supplier} {awarded_amount}".strip() if supplier else str(awarded_amount)
    return {
        "station": station,
        "stationLabel": STATIONS[station],
        "outsourceType": outsource_type,
        "outsourceTypeLabel": OUTSOURCE_TYPE_LABELS.get(outsource_type, outsource_type or "委外"),
        "projectId": row.get("project_id"),
        "inquiryId": row.get("inquiry_id"),
        "orderId": row.get("awarded_order_id"),
        "orderNo": row.get("awarded_order_no") or "",
        "moldNo": row.get("mold_no") or "",
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
    items = []
    for row in fetch_all(SQL, scoped):
        station = classify(row)
        if station is None:
            continue
        if scoped["station"] and station != scoped["station"]:
            continue
        items.append(item_from_row(row, station))
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
    scope = scoped["project_no"] or scoped["mold_batch"] or scoped["mold_family"] or "ERP 委外待办"
    station_label = STATIONS.get(scoped["station"], "全部分站")
    type_label = OUTSOURCE_TYPE_LABELS.get(scoped["outsource_type"], "零件/工序委外")
    summary = f"{scope} 的{type_label}{station_label}共 {len(visible)} 条。"
    summary += "\n" + "\n".join(f"- {label}：{count} 条" for label, count in counts.items() if count)
    summary += "\n" + "\n".join(f"- {label}：{count} 条" for label, count in type_counts.items() if count)
    if not visible:
        summary += "\n没有查到仍停在采购待办的项目。"
    else:
        lines = []
        for item in visible[:30]:
            details = item["partDetails"]
            if len(details) > 80:
                details = details[:80] + "…"
            lines.append(
                f"{item['stationLabel']} {item['outsourceTypeLabel']} {item['moldNo']} {details}"
            )
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
        return {"action": "accept_or_reject", "orderId": item.get("orderId"), "hint": hint}
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


def find_invitation(invitation_id: int, *, mold: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for item in _scan_items(mold):
        for invitation in item.get("invitations") or []:
            if invitation.get("invitationId") == invitation_id:
                return item, invitation
    return None, None


def run(parsed: dict[str, str], *, processor_tokens: list[str] | None = None) -> dict[str, Any]:
    items = query_items(parsed)
    if processor_tokens is not None:
        items = [
            clip_for_processor(item, processor_tokens)
            for item in items
            if item_mentions_processor(item, processor_tokens)
        ]
    return present(parsed, items)
