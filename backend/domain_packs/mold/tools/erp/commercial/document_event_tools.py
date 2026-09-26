"""受信任文档事件专用工具；不开放给聊天模型或任意请求调用。"""
from domain_packs.mold.ports.errors import DomainError

EVENT_TOOLS = {
    'create_admin_start_notice_from_bid': {'event': 'bid_notice.confirmed'},
    'propose_start_contract_matches': {'event': 'contract.ocr.ready'},
}


def execute_event_tool(db, user, key, *, event_kind, resource_id):
    spec = EVENT_TOOLS.get(key)
    if not spec or spec['event'] != event_kind:
        raise DomainError('EVENT_TOOL_FORBIDDEN', '事件与自动工具不匹配', 403)
    if key == 'create_admin_start_notice_from_bid':
        from domain_packs.mold.erp.commercial.admin_start_workflow import create_admin_start_notice_draft
        return create_admin_start_notice_draft(db, user, resource_id)
    from domain_packs.mold.erp.commercial.contract_match_workflow import propose_contract_matches
    return propose_contract_matches(db, user, resource_id)
