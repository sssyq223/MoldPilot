from copy import deepcopy

import pytest

from app.errors import DomainError
from agent_core import workflow
from domain_packs.mold.erp.core.erp_workflow_templates import templates


def test_all_erp_templates_validate_and_compile():
    migrated = templates()
    assert len(migrated) == 17
    assert len({item['process_key'] for item in migrated}) == 17
    for item in migrated:
        workflow.validate(item['config'])
        xml = workflow.compile_bpmn(item['config'])
        assert 'taskType="approval"' in xml
        assert item['config']['metadata']['migration_status'] == 'MIGRATED_DRAFT'


def test_form_schema_and_line_item_contract_are_exposed_by_simulation():
    item = next(item for item in templates() if item['process_key'] == 'procure_hardware_award_approval')
    result = workflow.simulate(item['config'], {'lines': [{'quantity': '1', 'category': 'hardware'}]})
    assert result['outcome'] == 'ROUTE_VALID'
    assert result['path'][0]['line_item_scope'] == 'line_required'
    assert result['path'][0]['form_schema']['fields'][0]['key'] == 'decision'


def test_business_task_requires_external_action_and_keeps_bpmn_metadata():
    item = next(item for item in templates() if item['process_key'] == 'purchase_request_approval')
    config = deepcopy(item['config'])
    config['nodes'][0]['task_type'] = 'business_task'
    with pytest.raises(DomainError):
        workflow.validate(config)
    config['nodes'][0]['external_action'] = 'erp.purchase.request.commit'
    workflow.validate(config)
    xml = workflow.compile_bpmn(config)
    assert 'taskType="business_task"' in xml


def test_form_values_are_bounded_and_unknown_fields_are_rejected():
    item = next(item for item in templates() if item['process_key'] == 'procure_hardware_award_approval')
    schema = item['config']['nodes'][0]['form_schema']
    workflow.validate_form_values(schema, {'decision': 'AWARD', 'unit_price': '12.50', 'remark': '已核价'})
    with pytest.raises(DomainError):
        workflow.validate_form_values(schema, {'decision': 'AWARD', 'unit_price': '12.50'})
    with pytest.raises(DomainError):
        workflow.validate_form_values(schema, {'decision': 'AWARD', 'unit_price': '12.50', 'remark': 'x', 'sql': 'drop'})


def test_parallel_group_must_be_contiguous_all_stages():
    item = next(item for item in templates() if item['process_key'] == 'design_modify_model_approval')
    config = deepcopy(item['config'])
    config['nodes'][0]['parallel_group'] = 'broken'
    config['nodes'][2]['parallel_group'] = 'broken'
    with pytest.raises(DomainError):
        workflow.validate(config)
