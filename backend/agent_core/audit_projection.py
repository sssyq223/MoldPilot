"""Optional producer-declared access requirements for sensitive audit details."""


def visible_detail(detail, access):
    """Keep audit metadata visible without granting access to source form data.

    Requirements are written by trusted event producers. The reader evaluates
    current permissions; an old ability to revise a form is not a permanent
    grant to read its historical values through the audit API.
    """
    if not isinstance(detail, dict):
        return {}, True
    if '_detail_access' not in detail:
        return detail, False
    requirements = detail['_detail_access']
    if not isinstance(requirements, list) or not requirements:
        return {}, True
    for requirement in requirements:
        if (not isinstance(requirement, dict) or not isinstance(requirement.get('permission'), str)
                or not isinstance(requirement.get('scope'), dict)
                or not isinstance(requirement.get('fields'), list)
                or not all(isinstance(field, str) for field in requirement['fields'])):
            return {}, True
        allowed = access(requirement['permission'], requirement['scope'])
        if not allowed.allowed or ('*' not in allowed.fields and not set(requirement['fields']) <= allowed.fields):
            return {}, True
    return {key:value for key,value in detail.items() if key != '_detail_access'}, False
