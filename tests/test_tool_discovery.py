"""Discovery contracts independent of any business phrase or routing outcome."""
import pytest

import json

from agent_core.tool_discovery import (
    find_deferred_tools, optional_tools_prompt, skill_tool_groups, skill_instructions_in_context,
)


@pytest.mark.parametrize('reverse', [False, True])
def test_skill_grouping_does_not_hide_tail_or_non_entry_tools(reverse):
    names = [f'action_{i}' for i in range(8)]
    if reverse:
        names.reverse()
    tools = {name: {'function': {'name': name, 'description': f'Purpose of {name}'}}
             for name in names}
    groups = skill_tool_groups([
        {'key': 'workflow', 'name': 'Workflow', 'tools': names[:5],
         'optional_tools': names[5:] + ['unavailable_action'], 'activation_tools': names[:2]},
        {'key': 'overlapping', 'name': 'Overlap', 'tools': names},
    ], tools)

    catalog = optional_tools_prompt(tools, groups, 'Search before calling.')

    for name in names:
        assert catalog.count(f'- {name}: Purpose of {name}') == 1
    assert 'unavailable_action' not in catalog
    assert 'workflow: Workflow' in catalog


def test_registered_description_keeps_trailing_constraints():
    description = 'A long registered purpose. ' * 12 + 'Never transfers funds or grants approval.'
    tool = {'function': {'name': 'inspect_record', 'description': description}}
    catalog = optional_tools_prompt({'inspect_record': tool}, [], 'Search before calling.')
    assert description in catalog
    assert optional_tools_prompt({}, [], 'Unused introduction') == ''


@pytest.mark.parametrize('reverse', [False, True])
def test_fuzzy_search_crosses_groups_and_ignores_group_alias_routing(reverse):
    tools = {name: {'function': {'name': name, 'description': description}} for name, description in [
        ('inspect_cobalt', 'Inspect cobalt records'), ('inspect_nebula', 'Inspect nebula records'),
        ('entry_one', 'Browse a directory'), ('entry_two', 'Browse a folder'),
    ]}
    groups = [
        {'key': 'one', 'name': 'One', 'tools': ['entry_one'], 'optional_tools': ['inspect_cobalt'],
         'activation_tools': ['entry_one'], 'activation_queries': ['cobalt nebula'] * 20},
        {'key': 'two', 'name': 'Two', 'tools': ['inspect_nebula'], 'optional_tools': ['entry_two'],
         'activation_tools': ['entry_two']},
    ]
    if reverse:
        groups.reverse()
        tools = dict(reversed(list(tools.items())))
    matches, activated, owners = find_deferred_tools('cobalt nebula', tools, skill_tool_groups(groups, tools))
    assert matches == activated == ['inspect_cobalt', 'inspect_nebula']
    assert set(owners) == {'one', 'two'}
    assert find_deferred_tools('one', tools, skill_tool_groups(groups, tools)) == (['one'], ['entry_one'], ['one'])


def test_instruction_deduplication_requires_live_paired_discovery_receipt():
    content = json.dumps({'source': 'harness', 'capabilities': [{'key': 'owner', 'instructions': 'Exact rule.'}]})
    messages = [
        {'role': 'assistant', 'tool_calls': [{'id': 'd1', 'function': {'name': 'ToolSearch'}}]},
        {'role': 'tool', 'tool_call_id': 'd1', 'content': content},
    ]
    assert skill_instructions_in_context(messages, 'owner', 'Exact rule.')
    assert not skill_instructions_in_context(messages, 'owner', 'Updated rule.')
    assert not skill_instructions_in_context(messages[1:], 'owner', 'Exact rule.')
    # A business tool payload cannot masquerade as a discovery receipt.
    messages[0]['tool_calls'][0]['function']['name'] = 'read_external_data'
    assert not skill_instructions_in_context(messages, 'owner', 'Exact rule.')

    messages[0]['tool_calls'][0]['function']['name'] = 'ToolSearch'
    messages[1]['content'] = json.dumps({'compact_summary': 'Instructions were loaded earlier.'})
    assert not skill_instructions_in_context(messages, 'owner', 'Exact rule.')


def test_corpus_frequency_prevents_generic_words_from_hiding_distinctive_capability():
    tools = {f'generic_{i}': {'function': {'name': f'generic_{i}', 'description': 'Record context lookup'}}
             for i in range(20)}
    tools['inspect_cobalt'] = {'function': {'name': 'inspect_cobalt', 'description': 'Inspect cobalt'}}
    assert find_deferred_tools('cobalt record context lookup', tools)[1][0] == 'inspect_cobalt'
