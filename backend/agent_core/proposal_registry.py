"""Generic access to proposal handlers supplied by the active business pack."""
from agent_core.domain_pack import component
from agent_core.errors import DomainError


def handler_for_action(action: str):
    return component("proposal_handlers").handler_for_action(action)


def handler_for_tool(tool: str):
    return component("proposal_handlers").handler_for_tool(tool)


def require_action_handler(action: str):
    handler = handler_for_action(action)
    if handler is None:
        raise DomainError("ACTION_UNKNOWN", "未登记的人工动作")
    return handler


def require_tool_handler(tool: str):
    handler = handler_for_tool(tool)
    if handler is None:
        raise DomainError("PROPOSAL_UNKNOWN", "该能力没有登记确认卡处理器", 404)
    return handler
