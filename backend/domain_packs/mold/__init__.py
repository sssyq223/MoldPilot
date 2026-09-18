"""MoldPilot mold-manufacturing domain pack."""
from functools import lru_cache
from importlib import import_module
from pathlib import Path
import re

PACK_ID = "mold"
DISPLAY_NAME = "Mold manufacturing"

_MODULE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@lru_cache
def _categorized_module(name: str):
    """Resolve a uniquely named categorized module for internal compatibility.

    Domain services use this only for ``from domain_packs.mold import x``.
    Concrete component entry points stay at package root for the host contract.
    """
    if not _MODULE_NAME.fullmatch(name):
        raise AttributeError(name)
    root = Path(__file__).resolve().parent
    matches = [path for path in root.rglob(f"{name}.py") if path.parent != root]
    if len(matches) != 1:
        raise AttributeError(name)
    relative = matches[0].relative_to(root).with_suffix("")
    return import_module("." + ".".join(relative.parts), __name__)


def __getattr__(name: str):
    value = _categorized_module(name)
    globals()[name] = value
    return value
