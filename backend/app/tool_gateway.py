"""Compatibility facade for the business-neutral Agent Core gateway."""
from agent_core.tool_gateway import (
    SKILLS,
    TOOLS,
    assigned,
    available_tools,
    capability_descriptor,
    execute,
    skill_context,
    tool_schema,
)

__all__ = [
    "SKILLS", "TOOLS", "assigned", "available_tools", "capability_descriptor",
    "execute", "skill_context", "tool_schema",
]
