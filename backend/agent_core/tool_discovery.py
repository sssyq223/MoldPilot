"""Discover authorized tools without interpreting the user's business intent.

The host supplies the authorized registry. Search changes schema visibility,
never permissions, and never invokes a business tool on the model's behalf.
"""
import json
import math
import re

MAX_SEARCH_MATCHES = 4


def tool_name(tool):
    return (tool.get("function") or {}).get("name") or ""


def tool_description(tool):
    return (tool.get("function") or {}).get("description") or ""


def search_terms(query):
    terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", query.lower())
    for run in re.findall(r"[\u4e00-\u9fff]+", query):
        terms.extend(run[index:index + 2] for index in range(len(run) - 1))
    return list(dict.fromkeys(terms))


def score(query, *fields, term_weights=None):
    result = 0
    terms = search_terms(query)
    for field in fields:
        text = str(field or "").lower()
        result += 10000 if text == query else 1200 if query in text else 0
        result += sum((term_weights or {}).get(term, 1) for term in terms if term in text)
    return result


def skill_tool_groups(skills, all_tools, registered=None):
    registered = registered or {}
    groups = []
    seen = set()
    for skill in skills or []:
        key = skill.get("key")
        if not key or key in seen:
            continue
        seen.add(key)
        spec = registered.get(key, {})
        required = skill.get("tools", skill.get("dependencies", spec.get("tools", [])))
        optional = skill.get("optional_tools", skill.get("optional_dependencies", spec.get("optional_tools", [])))
        activation = skill.get("activation_tools", skill.get("activation_dependencies", spec.get("activation_tools")))
        members = list(dict.fromkeys(name for name in [*required, *optional, *(activation or [])]
                                     if name in all_tools))
        if not members:
            continue
        groups.append({
            "key": key, "name": spec.get("name") or skill.get("name") or key,
            "description": skill.get("agent_description") or spec.get("description") or "",
            "tools": members,
            "activation_tools": [name for name in (activation if activation is not None else members)
                                 if name in all_tools],
            "required": [name for name in required if name in all_tools],
            "activation_queries": skill.get("activation_queries", spec.get("activation_queries", [])),
        })
    return groups


def find_deferred_tools(query, deferred_tools, tool_groups=None, **_legacy_context):
    """Exact names win. Fuzzy retrieval uses only the model's search query.

    Older callers may still pass prompt/intent hints; these are deliberately
    ignored. Runtime authority comes only from the supplied tool registry.
    """
    if not isinstance(query, str) or not query.strip():
        return [], [], []
    normalized = query.strip().lower()
    exact = next((name for name in deferred_tools if name.lower() == normalized), None)
    if exact:
        # Exact lookup controls schema activation, not whether the tool's
        # operating instructions apply. Prefer its owning skills over
        # coordinators that merely reference it as an optional follow-up.
        owners = [group for group in tool_groups or [] if exact in group.get("required", [])]
        if not owners:
            owners = [group for group in tool_groups or [] if exact in group["tools"]]
        return [exact], [exact], [group["key"] for group in owners]
    # A skill pack is selected only by its explicit catalog identifier/name.
    # Descriptive retrieval ranks the entire authorized tool registry; skill
    # aliases, entry-tool declarations and membership cannot hide a match.
    explicit_group = next((group for group in tool_groups or []
                           if normalized in {str(group['key']).lower(), str(group.get('name') or '').lower()}), None)
    if explicit_group:
        names = [name for name in explicit_group.get('activation_tools', explicit_group['tools'])
                 if name in deferred_tools][:MAX_SEARCH_MATCHES]
        return [explicit_group['key']], names, [explicit_group['key']]
    # Corpus-derived inverse document frequency prevents ubiquitous words
    # (e.g. "context" or "query") from swamping a distinctive capability term.
    # No business aliases, manually weighted terms, or user-prompt routing.
    documents = [f'{name} {tool_description(tool)}'.lower() for name, tool in deferred_tools.items()]
    weights = {term: math.log(1 + (len(documents) - frequency + .5) / (frequency + .5))
               for term in search_terms(normalized)
               if (frequency := sum(term in document for document in documents))}
    ranked_tools = sorted(((score(normalized, name, tool_description(tool), term_weights=weights), name)
                           for name, tool in deferred_tools.items()),
                          key=lambda item: (-item[0], item[1]))
    names = [name for value, name in ranked_tools if value > 0][:MAX_SEARCH_MATCHES]
    owners = []
    for name in names:
        required = [group for group in tool_groups or [] if name in group.get('required', [])]
        selected = required or [group for group in tool_groups or [] if name in group['tools']]
        owners.extend(group['key'] for group in selected)
    return names, names, list(dict.fromkeys(owners))


def optional_tools_prompt(deferred_tools, groups, intro):
    """Expose the complete authorized index, not just skill entry points.

    A skill's activation surface limits schema loading, not discoverability.
    List each tool once with its full registered description so grouping and
    membership order cannot hide an action or truncate a critical limitation.
    Skill identifiers live in a separate, explicitly non-callable index.
    """
    if not deferred_tools:
        return ""
    lines = ["# 按需工具", intro, "## 工具索引（先用 ToolSearch 激活参数定义）"]
    for name, tool in deferred_tools.items():
        description = " ".join(tool_description(tool).split())
        lines.append(f"- {name}: {description}")
    skills = []
    for group in groups:
        members = [name for name in group["tools"] if name in deferred_tools]
        if not members:
            continue
        skills.append(f"- {group['key']}: {group['name']}")
    if skills:
        lines.extend(["## 技能索引（不是函数名；用 ToolSearch 搜索以加载说明）", *skills])
    return "\n".join(lines)


def skill_instructions_in_context(messages, key, instructions):
    """Only a paired discovery observation proves instructions are resident.

    Do not persist a loaded flag: compaction can remove that observation while
    tools remain active. Similar business payloads are not discovery receipts.
    """
    discovery_calls = set()
    for message in messages:
        if message.get('role') == 'assistant':
            discovery_calls.update(call.get('id') for call in message.get('tool_calls') or []
                                   if (call.get('function') or {}).get('name') == 'ToolSearch')
        elif message.get('role') == 'tool' and message.get('tool_call_id') in discovery_calls:
            try:
                result = json.loads(message.get('content') or '{}')
            except (TypeError, ValueError):
                continue
            if not isinstance(result, dict) or result.get('source') != 'harness':
                continue
            for capability in result.get('capabilities') or []:
                if (isinstance(capability, dict) and capability.get('key') == key
                        and capability.get('instructions') == instructions):
                    return True
    return False
