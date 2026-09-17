"""An empty pack has no human-confirmation card implementations."""

HANDLERS = ()


def handler_for_action(action: str):
    return None


def handler_for_tool(tool: str):
    return None
