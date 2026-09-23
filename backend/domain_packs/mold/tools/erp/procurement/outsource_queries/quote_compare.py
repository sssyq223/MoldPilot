"""Read-only outsource quote comparison."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from domain_packs.mold.erp.procurement.erp_outsource_db import fetch_all

MOLD_FAMILY = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,})(?!-P\d+)(?![A-Z0-9])")
MOLD_BATCH = re.compile(r"(?i)(?<![A-Z0-9])(M\d{5,}-P\d+)(?![A-Z0-9])")
PROJECT_NO = re.compile(r"(?i)(?<![A-Z0-9])(E\d+-\d+|ENT-BATCH-[A-Z0-9]+)(?![A-Z0-9])")
ROW_LIMIT = 50

SQL = """
SELECT
    project.project_no,
    project.name AS project_name,
    molds.mold_no,
    inquiry.id AS inquiry_id,
    inquiry.status AS inquiry_status,
    inquiry.reference_total_amount,
    inquiry.our_quote_amount,
    inquiry.auto_accept_max_amount,
    inquiry.final_deal_amount,
    supplier.partner_name AS supplier_name,
    invitation.status AS invitation_status,
    quote.unit_price AS quote_amount,
    quote.lead_time_days,
    quote.delivery_date
FROM entrust_projects project
JOIN entrust_outsource_requests inquiry
  ON inquiry.project_id = project.id
 AND coalesce(inquiry.inquiry_type, 'normal') <> 'reflow'
LEFT JOIN LATERAL (
    SELECT string_agg(DISTINCT mold.name, '、' ORDER BY mold.name) AS mold_no
    FROM entrust_molds mold
    WHERE mold.project_id = project.id
) molds ON TRUE
LEFT JOIN entrust_invitations invitation ON invitation.request_id = inquiry.id
LEFT JOIN partner supplier ON supplier.id = invitation.supplier_id
LEFT JOIN entrust_quotations quote ON quote.invitation_id = invitation.id
WHERE (
        (%(mold_batch)s = '' AND %(mold_family)s = '' AND %(project_no)s = '')
        OR (%(project_no)s <> '' AND upper(project.project_no) = %(project_no)s)
        OR EXISTS (
            SELECT 1 FROM entrust_molds mold
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
  AND inquiry.id = (
        SELECT picked.id
        FROM entrust_outsource_requests picked
        WHERE picked.project_id = project.id
          AND coalesce(picked.inquiry_type, 'normal') <> 'reflow'
        ORDER BY
            CASE coalesce(picked.status, 'draft')
                WHEN 'quoted' THEN 0
                WHEN 'sent' THEN 1
                WHEN 'awarded' THEN 2
                WHEN 'partial_awarded' THEN 2
                ELSE 3
            END,
            picked.id DESC
        LIMIT 1
  )
ORDER BY project.project_no, quote.unit_price NULLS LAST, supplier.partner_name
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


def parse_question(question: str) -> dict[str, str]:
    text = question or ""
    batch = MOLD_BATCH.search(text)
    family = None if batch else MOLD_FAMILY.search(text)
    project = PROJECT_NO.search(text)
    return {
        "mold_family": "" if batch else (family.group(1).upper() if family else ""),
        "mold_batch": batch.group(1).upper() if batch else "",
        "project_no": project.group(1).upper() if project else "",
    }


def query_projects(parsed: dict[str, str]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for row in fetch_all(SQL, parsed):
        inquiry_id = int(row["inquiry_id"])
        item = grouped.get(inquiry_id)
        if item is None:
            ceiling = money(row.get("auto_accept_max_amount"))
            item = {
                "projectNo": row.get("project_no") or "",
                "projectName": row.get("project_name") or "",
                "moldNo": row.get("mold_no") or "",
                "inquiryStatus": row.get("inquiry_status") or "",
                "referenceTotal": money(row.get("reference_total_amount")),
                "ourQuoteAmount": money(row.get("our_quote_amount")),
                "autoAcceptMaxAmount": ceiling,
                "finalDealAmount": money(row.get("final_deal_amount")),
                "quotes": [],
            }
            grouped[inquiry_id] = item
        if not row.get("supplier_name") and row.get("quote_amount") is None:
            continue
        quote_amount = money(row.get("quote_amount"))
        ceiling = item["autoAcceptMaxAmount"]
        due = row.get("delivery_date")
        item["quotes"].append(
            {
                "supplierName": row.get("supplier_name") or "",
                "invitationStatus": row.get("invitation_status") or "",
                "quoteAmount": quote_amount,
                "leadTimeDays": row.get("lead_time_days"),
                "deliveryDate": due.isoformat() if hasattr(due, "isoformat") else due,
                "overRange": quote_amount is not None and ceiling is not None and quote_amount > ceiling,
            }
        )
    projects = list(grouped.values())
    for item in projects:
        priced = [quote["quoteAmount"] for quote in item["quotes"] if quote["quoteAmount"] is not None]
        item["lowestQuote"] = min(priced) if priced else None
        item["overRangeCount"] = sum(1 for quote in item["quotes"] if quote["overRange"])
    return projects


def present(parsed: dict[str, str], projects: list[dict[str, Any]]) -> dict[str, Any]:
    truncated = len(projects) > ROW_LIMIT
    visible = projects[:ROW_LIMIT]
    scope = parsed["project_no"] or parsed["mold_batch"] or parsed["mold_family"] or "全部有询价单的项目"
    summary = f"{scope} 共 {len(visible)} 张询价单可对比。"
    if not visible:
        summary += "没有查到询价单。"
    lines = []
    for item in visible[:20]:
        lines.append(
            f"{item['projectNo']} {item['moldNo']} 核算价 {item['referenceTotal']}，"
            f"我方报价 {item['ourQuoteAmount']}，上限 {item['autoAcceptMaxAmount']}，"
            f"最低报价 {item['lowestQuote']}，超上限 {item['overRangeCount']} 家"
        )
    if lines:
        summary += "\n" + "\n".join(f"- {line}" for line in lines)
    if truncated:
        summary += f"\n结果超过 {ROW_LIMIT} 张，只返回前 {ROW_LIMIT} 张。请缩小到模具批次或项目号。"
    return {
        "scope": scope,
        "database": "erp",
        "definition": "报价合计取 entrust_quotations.unit_price。超过 auto_accept_max_amount 记为超上限。",
        "truncated": truncated,
        "summary": summary,
        "projects": visible,
    }


def run(parsed: dict[str, str]) -> dict[str, Any]:
    return present(parsed, query_projects(parsed))
