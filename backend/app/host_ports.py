"""Concrete application adapter for the stable Agent Core port contract."""
from agent_core.host_ports import HostPorts

from . import model_catalog, model_discovery, models, object_storage
from .authorization import access, fingerprint, grants_for, predicate, require, select_fields
from .bpm import content_hash
from .confirmation_policy import proposal_confirmation_policy
from .config import model_settings, settings
from .db import get_db, now
from .events import record
from .files import conversation_files, metadata as file_metadata, run_files, uploaded_file, validate_file
from .security import current_user


PORTS = HostPorts(
    models=models,
    object_storage=object_storage,
    model_catalog=model_catalog,
    model_discovery=model_discovery,
    access=access,
    grants_for=grants_for,
    fingerprint=fingerprint,
    predicate=predicate,
    require=require,
    select_fields=select_fields,
    content_hash=content_hash,
    proposal_confirmation_policy=proposal_confirmation_policy,
    settings=settings,
    model_settings=model_settings,
    get_db=get_db,
    now=now,
    record=record,
    current_user=current_user,
    conversation_files=conversation_files,
    run_files=run_files,
    uploaded_file=uploaded_file,
    file_metadata=file_metadata,
    validate_file=validate_file,
)
