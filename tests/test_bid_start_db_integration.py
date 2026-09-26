from bid_start_db import bid_context, bid_db  # noqa: F401
from domain_packs.mold.erp.commercial.bid_start_workflow import consume_confirmed_bid_notice


def test_confirmed_event_is_consumed_into_one_pending_match(bid_context):
    context = bid_context
    first = consume_confirmed_bid_notice(context.db, context.owner, context.event.id)
    second = consume_confirmed_bid_notice(context.db, context.owner, context.event.id)

    assert first.id == second.id
    assert first.status == "PENDING_MATCH"
    assert first.project_id is None
    assert first.evidence_snapshot["event_type"] == "BID_WON"
