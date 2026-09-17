"""Business-neutral language policy for the empty starter pack."""

ACTION_INTENT_TERMS = ()
FORMAL_ACTION_TERMS = ()
FORMAL_ACTION_NEGATED_PHRASES = ()
WORKBENCH_SUPPORT_HINTS = (
    "harness", "tool", "skill", "模型", "model", "配置", "接口", "api", "错误", "调试",
)
BUSINESS_OBJECT_HINTS = ()
BUSINESS_ACTION_HINTS = ()
PURE_CONVERSATION_TERMS = (
    "你好", "您好", "hello", "hi", "谢谢", "好的", "收到", "ok",
)
ELLIPTICAL_ACTION_TERMS = ()

SYSTEM_PROMPT = """你是通用智能体工作台。当前没有安装任何业务能力。
直接回答一般问题；需要外部业务事实或正式操作时，明确说明当前业务包没有登记相应工具。
最后输出 JSON 对象，字段 response_kind 为 CONVERSATION 或 CLARIFICATION，summary 为回复，evidence_ids 为空数组，suggestions 为字符串数组。"""
