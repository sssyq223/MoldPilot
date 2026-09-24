"""Stable confirmation policy exposed by Agent Core."""
from agent_core.confirmation_policy import (
    agent_permission_mode_from_proposal,
    proposal_confirmation_policy,
    proposal_run_is_open,
)

__all__ = [
    "agent_permission_mode_from_proposal",
    "proposal_confirmation_policy",
    "proposal_run_is_open",
]
