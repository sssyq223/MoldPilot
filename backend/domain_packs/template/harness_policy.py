"""Business-neutral language policy for the empty starter pack."""

# Keep the template pack compatible with the generic Harness contract while
# intentionally exposing no business vocabulary.
ACTION_INTENT_TERMS = ()
FORMAL_ACTION_TERMS = ()
WORKBENCH_SUPPORT_HINTS = ()
BUSINESS_OBJECT_HINTS = ()
BUSINESS_ACTION_HINTS = ()
PURE_CONVERSATION_TERMS = ("hello", "hi", "thanks")
ELLIPTICAL_ACTION_TERMS = ()

TOOL_SEARCH_SCHEMA_DESCRIPTION = "Activate one registered on-demand capability; this only exposes its schema and does not execute it."
TOOL_SEARCH_QUERY_DESCRIPTION = "Exact tool name or short capability description."
TOOL_SEARCH_PROMPT_INTRO = (
    "The tool index lists exact authorized tool names and purposes; skill identifiers refer to searchable instructions, not functions. Search only when the current request needs an installed capability. "
    "An exact tool name activates one tool; a capability description may activate a small related set."
)
PERMISSION_MODE_INSTRUCTIONS = {
    "delegated_auto": "This turn uses delegated automation. Apply only explicit, valid host authorization and never invent approval.",
    "ask": "This turn requires confirmation for governed side effects. Natural-language agreement is not a confirmation receipt.",
}
OLLAMA_REACT_GUIDANCE = """Use the structured ReAct action protocol for this turn.
Choose CALL_TOOL only when installed facts are required; otherwise choose RESPOND. For CALL_TOOL, select one available tool and provide schema-valid arguments. For RESPOND, leave tool_name empty and arguments as an empty object. Return one JSON object without hidden reasoning. Evidence identifiers may only come from tool results.
Available tools:"""

SYSTEM_PROMPT = """你是通用智能体工作台。当前没有安装任何业务能力。
直接回答一般问题；需要外部业务事实或正式操作时，明确说明当前业务包没有登记相应工具。
最后输出 JSON 对象，字段 response_kind 为 CONVERSATION 或 CLARIFICATION，summary 为回复，evidence_ids 为空数组，suggestions 为字符串数组。"""
