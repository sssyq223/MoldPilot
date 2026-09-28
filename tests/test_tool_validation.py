import copy
import json
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_core.errors import DomainError
from agent_core.tool_validation import input_diagnostics
from agent_core import tool_gateway


class Line(BaseModel):
    model_config = ConfigDict(extra='forbid')
    amount: Decimal = Field(gt=0)
    evidence: str = Field(min_length=30)


class Input(BaseModel):
    lines: list[Line]


def parse(arguments):
    try:
        return Input.model_validate(arguments)
    except ValidationError:
        raise DomainError('INVALID_TOOL_INPUT', 'Input was rejected') from None


def test_native_nested_errors_survive_suppressed_context_without_values(monkeypatch):
    arguments = {'lines': [{'amount': 0, 'evidence': 'private-marker', 'unknown/key~': 'another-private-marker'}]}
    original = copy.deepcopy(arguments)
    monkeypatch.setattr(tool_gateway._gateway, 'execute', lambda db, user, key, args, run=None: parse(args))
    with pytest.raises(DomainError) as rejected:
        tool_gateway.execute(None, None, 'synthetic', arguments)
    error = rejected.value
    assert error.code == 'INVALID_TOOL_INPUT' and error.status == 400
    details = error.as_dict()['details']
    by_pointer = {item['pointer']: item for item in details['validation_errors']}
    assert by_pointer['/lines/0/amount']['code'] == 'greater_than'
    assert by_pointer['/lines/0/amount']['path'] == ['lines', 0, 'amount']
    assert by_pointer['/lines/0/evidence']['code'] == 'string_too_short'
    assert by_pointer['/lines/0/unknown~1key~0']['code'] == 'extra_forbidden'
    assert '/lines/0/amount' in error.message
    serialized = json.dumps(error.as_dict())
    assert 'private-marker' not in serialized
    assert 'https://' not in serialized
    assert arguments == original


def test_bounded_diagnostics_report_omitted_errors():
    with pytest.raises(DomainError) as rejected:
        parse({'lines': [{'amount': 0, 'evidence': ''}] * 20})
    error = input_diagnostics(rejected.value)
    assert error.details['validation_error_count'] == 40
    assert len(error.details['validation_errors']) == 20
    assert error.details['validation_errors_truncated'] is True


def test_non_validation_and_authorization_errors_are_not_reinterpreted():
    rejection = DomainError('INVALID_TOOL_INPUT', 'Business constraint')
    assert input_diagnostics(rejection) is rejection
    try:
        Input.model_validate({})
    except ValidationError:
        try:
            raise DomainError('TOOL_FORBIDDEN', 'Not authorized', 403) from None
        except DomainError as forbidden:
            assert input_diagnostics(forbidden) is forbidden
            assert forbidden.as_dict() == {'code': 'TOOL_FORBIDDEN', 'message': 'Not authorized'}


def test_success_forwards_exact_arguments_and_does_not_add_defaults(monkeypatch):
    arguments = {'lines': [{'amount': '1.50', 'evidence': 'Evidence ' * 5}]}
    expected = {'source': 'synthetic', 'data': []}
    def execute(db, user, key, args, run=None):
        assert args is arguments
        parse(args)
        return expected
    monkeypatch.setattr(tool_gateway._gateway, 'execute', execute)
    assert tool_gateway.execute(None, None, 'synthetic', arguments) is expected
