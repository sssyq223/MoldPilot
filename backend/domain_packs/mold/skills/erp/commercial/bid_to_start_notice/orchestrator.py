"""中标确认后的 Skill 自动编排入口。"""
from domain_packs.mold.tools.erp.commercial.document_event_tools import execute_event_tool


SKILL_KEY = "bid_to_start_notice"
TRIGGER_ACTION = "bid_notice.confirmed"


def trigger_bid_to_start_notice(db, user, event_id):
    """由人工确认事件触发 Skill，自动调用草稿 Tool 的领域实现。"""
    return execute_event_tool(
        db, user, 'create_admin_start_notice_from_bid',
        event_kind=TRIGGER_ACTION, resource_id=event_id,
    )
