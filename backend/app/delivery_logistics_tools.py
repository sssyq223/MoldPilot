"""Compatibility facade for the mold domain pack's logistics application service.

New code must import :mod:`domain_packs.mold.delivery_logistics` through the
active pack.  This facade temporarily preserves imports used by older host
modules and tests while the remaining mold slices move out of ``app``.
"""
from domain_packs.mold import delivery_logistics as _implementation
from domain_packs.mold.delivery_logistics import (
    DELIVERY_KEYWORDS,
    DELIVERY_LOGISTICS_PROPOSAL_TOOLS,
    QUALITY_SOURCES,
    LogisticsQuoteProposalInput,
    LogisticsRouteProposalInput,
    confirm,
    create_logistics_quote,
    create_logistics_route,
    execute_delivery_logistics_tool,
    logistics_quote_schema,
    logistics_route_schema,
    parse_logistics_quote,
    parse_logistics_route,
    preview_logistics_quote,
    preview_logistics_route,
    query,
    source,
    validate_intent,
)


# Legacy full-outsource aggregation still reuses these read-side projections.
# Keep aliases only; no mold rule is implemented in the generic host package.
_augment_order_receipts = _implementation._augment_order_receipts
_order_headers = _implementation._order_headers
_order_rows = _implementation._order_rows
_shipment_tracking = _implementation._shipment_tracking
