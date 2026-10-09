"""Internal Agent run states and their public API projection.

Scoped states keep desktop/runtime installations that share PostgreSQL from
claiming each other's work.  They are deliberately different from the legacy
states so an older worker cannot claim a run created by a newer API process.
"""

LEGACY_QUEUED = "QUEUED"
SCOPED_QUEUED = "QUEUED_SCOPED"
LEGACY_RUNNING = "RUNNING"
SCOPED_RUNNING = "RUNNING_SCOPED"
WAITING_DOCUMENT = "WAITING_DOCUMENT"
COMPOSITION_FAILED = "COMPOSITION_FAILED"

QUEUED_STATUSES = frozenset({LEGACY_QUEUED, SCOPED_QUEUED})
RUNNING_STATUSES = frozenset({LEGACY_RUNNING, SCOPED_RUNNING})
ACTIVE_STATUSES = QUEUED_STATUSES | RUNNING_STATUSES


def public_run_status(status: str) -> str:
    if status in QUEUED_STATUSES:
        return LEGACY_QUEUED
    if status in RUNNING_STATUSES:
        return LEGACY_RUNNING
    return status


def composition_retry_checkpoint(checkpoint: dict, result: dict | None,
                                 worker_scope: str) -> dict:
    """Prepare a terminal-only retry without clearing durable tool execution state."""
    previous = dict(checkpoint or {})
    prior_errors = list(previous.get("composition_errors") or [])[-4:]
    if isinstance(result, dict):
        prior_errors.append({
            "error_code": result.get("error_code"),
            "model_metrics": previous.get("model_metrics") or {},
            "failed_at": previous.get("completed_at"),
        })
    retried = {
        **previous,
        "worker_scope": worker_scope,
        "phase": "COMPOSITION_RETRY_QUEUED",
        "composition_errors": prior_errors,
        "composition_attempt": int(previous.get("composition_attempt") or 0) + 1,
        "protocol_repairs": 0,
        "streaming_model_message": None,
        "model_started_at": None,
    }
    retried.pop("completed_at", None)
    return retried
