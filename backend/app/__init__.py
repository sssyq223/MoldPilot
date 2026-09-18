"""Generic application host.

Attribute-style imports of domain modules are forwarded to the configured
pack for source compatibility.  The host neither names nor imports a concrete
ERP package.
"""
from agent_core.domain_pack import active_pack_name, component
from importlib import import_module, util


def __getattr__(name: str):
    if name.startswith("_"):
        raise AttributeError(name)
    local_name = f"{__name__}.{name}"
    if util.find_spec(local_name) is not None:
        value = import_module(f".{name}", __name__)
        globals()[name] = value
        return value
    try:
        value = component(name)
    except ModuleNotFoundError as exc:
        expected = f"domain_packs.{active_pack_name()}.{name}"
        if exc.name != expected:
            raise
        package = import_module(f"domain_packs.{active_pack_name()}")
        try:
            value = getattr(package, name)
        except AttributeError:
            raise AttributeError(name) from None
    globals()[name] = value
    return value
