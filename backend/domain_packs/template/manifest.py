"""Minimal host assembly contract for a new vehicle, fixture or ERP pack."""
import re


PUBLIC_METADATA = {
    "id": "template",
    "product_name": "Agent Workbench",
    "display_name": "通用智能体工作台",
    "tagline": "安装业务包后即可接入工具、Skill 与确认卡。",
    "workspace_tabs": [],
    "proposal_presentation": {
        "action_prefixes": [],
        "action_suffixes": [],
        "detail_links": {},
        "value_names": {},
    },
}
APP_TITLE = "Agent Workbench"


def conversation_title(prompt: str) -> str:
    text = re.sub(r"\s+", " ", (prompt or "").strip())
    return (text[:28] + "…") if len(text) > 28 else (text or "新对话")


def install(app, domain_router) -> None:
    # Deliberately do not install the host's mold compatibility router.  A new
    # pack adds its own routers here without changing agent_core or app.api.
    return None
