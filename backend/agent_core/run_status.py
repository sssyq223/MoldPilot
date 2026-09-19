"""Internal Agent run states and their public API projection.

Scoped states keep desktop/runtime installations that share PostgreSQL from
claiming each other's work.  They are deliberately different from the legacy
states so an older worker cannot claim a run created by a newer API process.
"""

LEGACY_QUEUED = "QUEUED"
SCOPED_QUEUED = "QUEUED_SCOPED"
LEGACY_RUNNING = "RUNNING"
SCOPED_RUNNING = "RUNNING_SCOPED"

QUEUED_STATUSES = frozenset({LEGACY_QUEUED, SCOPED_QUEUED})
RUNNING_STATUSES = frozenset({LEGACY_RUNNING, SCOPED_RUNNING})
ACTIVE_STATUSES = QUEUED_STATUSES | RUNNING_STATUSES


def public_run_status(status: str) -> str:
    if status in QUEUED_STATUSES:
        return LEGACY_QUEUED
    if status in RUNNING_STATUSES:
        return LEGACY_RUNNING
    return status
