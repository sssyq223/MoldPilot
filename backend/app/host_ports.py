"""Concrete MoldPilot host adapter for the stable Agent Core port contract."""
from agent_core.host_ports import HostPorts

from . import models
from .authorization import access, fingerprint, predicate, require, select_fields
from .bpm import content_hash
from .confirmation_policy import proposal_confirmation_policy
from .config import settings
from .db import now


PORTS = HostPorts(
    models=models,
    access=access,
    fingerprint=fingerprint,
    predicate=predicate,
    require=require,
    select_fields=select_fields,
    content_hash=content_hash,
    proposal_confirmation_policy=proposal_confirmation_policy,
    settings=settings,
    now=now,
)
