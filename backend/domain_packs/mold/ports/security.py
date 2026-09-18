"""Identity services exposed through Agent Core contracts."""
from agent_core.host_ports import host_ports
from agent_core.security import digest, hasher

current_user = host_ports().current_user


__all__ = ["current_user", "digest", "hasher"]
