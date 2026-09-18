"""Host-facing alias for the mold ERP transaction implementation.

Keeping this as a module alias (rather than copying exported names) preserves
module identity for host integrations and runtime instrumentation.
"""
from importlib import import_module
import sys


_implementation = import_module("domain_packs.mold.erp.core.business")
sys.modules[__name__] = _implementation
