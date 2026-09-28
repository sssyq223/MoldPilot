"""Only an explicit, consistent settlement link supersedes responsibility facts."""

FROZEN_FIELDS = ('project_id', 'supplier_id', 'contract_subject_id', 'contact_case_id',
                 'contact_task_id', 'reason', 'responsibility', 'deduction_amount', 'currency')


def valid_follow_up(previous, current):
    return (previous is not None and previous.id != current.id
        and current.previous_deduction_id == previous.id and current.source_ref is None
        and previous.status == 'RESPONSIBILITY_CONFIRMED' and current.status == 'SETTLED'
        and previous.responsibility != 'UNKNOWN'
        and bool(current.settlement_reference and current.settlement_evidence)
        and previous.previous_deduction_id is None
        and all(getattr(previous, field) == getattr(current, field) for field in FROZEN_FIELDS)
        and (not previous.customer_acceptance_id or previous.customer_acceptance_id == current.customer_acceptance_id))


def lineage(rows):
    by_id = {row.id: row for row in rows}
    successors, invalid = {}, set()
    for row in rows:
        if not row.previous_deduction_id:
            continue
        if not valid_follow_up(by_id.get(row.previous_deduction_id), row):
            invalid.add(row.id)
            continue
        successors[row.previous_deduction_id] = row.id
    return successors, invalid
