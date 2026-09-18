"""Compatibility model facade assembled from host core and the active domain pack."""
from agent_core.models import *  # noqa: F401,F403

from agent_core.domain_pack import component as _pack_component

_domain_model_component = _pack_component("models")
for _model_name in _domain_model_component.EXPORTED_MODELS:
    globals()[_model_name] = getattr(_domain_model_component, _model_name)
