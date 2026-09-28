"""Current customer-quality-acceptance state shared by delivery and outsource reads.

Rows are immutable evidence. Only an explicitly linked successor supersedes a
result; sharing a signature, date, or project never establishes that lineage.
"""


PASS_RESULTS = {"PASSED", "CONDITIONALLY_PASSED"}


def _lineage(row, by_id):
    seen, chain = set(), []
    while row:
        if row.id in seen:
            return []
        seen.add(row.id)
        chain.append(row)
        if not row.previous_acceptance_id:
            return chain
        previous = by_id.get(row.previous_acceptance_id)
        if (not previous or row.acceptance_type != 'RECHECK'
                or previous.project_id != row.project_id or previous.signature_id != row.signature_id
                or previous.accepted_date > row.accepted_date):
            return []
        row = previous
    return chain


def acceptance_chain_tips(rows):
    by_id = {row.id: row for row in rows}
    superseded = set()
    for row in rows:
        previous = by_id.get(row.previous_acceptance_id)
        if previous and _lineage(row, by_id):
            superseded.add(previous.id)
    return tuple(row for row in rows if row.id not in superseded)


def summarize_customer_acceptance(rows):
    by_id = {row.id: row for row in rows}
    latest = acceptance_chain_tips(rows)
    invalid = any(row.previous_acceptance_id and not _lineage(row, by_id) for row in rows)
    unresolved = invalid or any(row.result == "FAILED" for row in latest)
    # Old RECHECK rows have no confirmed predecessor. Preserve them, but do
    # not guess which historical failure they resolved during migration.
    verified_passes = [row for row in latest if row.result in PASS_RESULTS
                       and (row.acceptance_type == 'INITIAL' or row.previous_acceptance_id)]
    return {
        "has_customer_acceptance": not unresolved and bool(verified_passes),
        "has_failed_customer_acceptance": any(row.result == "FAILED" for row in rows),
        "has_unresolved_failure": unresolved,
        "has_recheck_record": any(row.acceptance_type == "RECHECK" for row in rows),
        "has_recheck_passed": not unresolved and any(
            row.acceptance_type == "RECHECK" for row in verified_passes
        ),
        "unlinked_recheck_ids": [row.id for row in rows if row.acceptance_type == 'RECHECK' and not row.previous_acceptance_id],
        "recheck_candidate_ids": [row.id for row in latest if any(
            ancestor.result == 'FAILED' for ancestor in _lineage(row, by_id))],
        "has_acceptance_chain_conflict": invalid,
        "has_acceptance_deduction": any(row.deduction_amount is not None for row in rows),
        "has_contract_change_required": any(row.contract_change_required for row in rows),
        "schedule_impact_days_total": sum(int(row.schedule_impact_days or 0) for row in rows),
    }
