"""Carry native input diagnostics across domain/host/transport boundaries.

No second validator, coercion, argument correction, or tool routing happens here.
Domain parsers remain authoritative; their ValidationError stays in the exception
chain even when a public DomainError is raised with ``from None``.
"""
from pydantic import ValidationError

from .errors import DomainError

MAX_DIAGNOSTICS = 20


def input_diagnostics(error: DomainError) -> DomainError:
    if error.code != 'INVALID_TOOL_INPUT' or error.details is not None:
        return error
    cause = error.__cause__ or error.__context__
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ValidationError):
            break
        cause = cause.__cause__ or cause.__context__
    if not isinstance(cause, ValidationError):
        return error
    # Never serialize rejected values, exception contexts, or validation URLs.
    errors = cause.errors(include_input=False, include_context=False, include_url=False)
    diagnostics = []
    for item in errors[:MAX_DIAGNOSTICS]:
        path = list(item['loc'])
        pointer = ''.join('/' + str(part).replace('~', '~0').replace('/', '~1') for part in path)
        diagnostics.append({'path': path, 'pointer': pointer, 'code': item['type'], 'message': item['msg']})
    details = {'validation_errors': diagnostics, 'validation_error_count': len(errors),
               'validation_errors_truncated': len(errors) > MAX_DIAGNOSTICS}
    message = error.message + '\n' + '\n'.join(
        f"{item['pointer'] or '$'}: {item['message']}" for item in diagnostics)
    return DomainError(error.code, message, error.status, details=details)
