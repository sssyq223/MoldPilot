"""Runtime configuration obtained only through the host port contract."""
from agent_core.host_ports import host_ports


def settings():
    return host_ports().settings()


def model_settings():
    return host_ports().model_settings()


__all__ = ["model_settings", "settings"]
