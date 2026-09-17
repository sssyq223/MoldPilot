"""Empty capability registry for bootstrapping a new business pack."""

TOOLS = {}
SKILLS = {}


def assigned(db, user, kind, key):
    return False


def available_tools(db, user):
    return []


def capability_descriptor(kind, key, spec):
    raise KeyError(key)


def skill_context(db, user):
    return []


def tool_schema(key):
    raise KeyError(key)


def execute(db, user, key, arguments, run=None):
    raise KeyError(key)
