"""Stable generic facade for the active domain pack's registered capabilities."""
from .domain_pack import component
from .errors import DomainError
from .tool_validation import input_diagnostics


_gateway = component("tool_gateway")

TOOLS = _gateway.TOOLS
SKILLS = _gateway.SKILLS
assigned = _gateway.assigned
available_tools = _gateway.available_tools
capability_descriptor = _gateway.capability_descriptor
skill_context = _gateway.skill_context
def tool_schema(key):
    """Use the same registered identity in model discovery and human surfaces."""
    schema = _gateway.tool_schema(key)
    function = dict(schema['function'])
    title = capability_descriptor('TOOL', key, TOOLS[key]).get('name')
    description = function.get('description') or ''
    if title and title != key and title not in description:
        function['description'] = f'{title}。{description}'
    return {**schema, 'function': function}


def execute(db, user, key, arguments, run=None):
    try:
        return _gateway.execute(db, user, key, arguments, run=run)
    except DomainError as error:
        diagnosed = input_diagnostics(error)
        if diagnosed is error:
            raise
        raise diagnosed from None
