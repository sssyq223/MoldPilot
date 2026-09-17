"""Stable generic facade for the active domain pack's registered capabilities."""
from .domain_pack import component


_gateway = component("tool_gateway")

TOOLS = _gateway.TOOLS
SKILLS = _gateway.SKILLS
assigned = _gateway.assigned
available_tools = _gateway.available_tools
capability_descriptor = _gateway.capability_descriptor
skill_context = _gateway.skill_context
tool_schema = _gateway.tool_schema
execute = _gateway.execute
