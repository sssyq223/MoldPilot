"""Persistence and clock services exposed through Agent Core contracts."""
from agent_core.host_ports import host_ports
from agent_core.model_base import aware, now

get_db = host_ports().get_db


__all__ = ["aware", "get_db", "now"]
