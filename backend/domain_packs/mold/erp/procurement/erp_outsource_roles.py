"""ERP part/operation outsource role vocabulary.

These roles are organizational assignment templates plus Grant permission codes.
Buyer-side read Skills are registered under skills/erp/procurement/.
Organization ROLE/DEPARTMENT names are used by admin login isolation; permission
codes appear in the grant catalog so administrators can authorize accounts.
"""
from __future__ import annotations

from typing import TypedDict


class OutsourceRoleSpec(TypedDict):
    key: str
    role_name: str
    department_name: str
    description: str
    permissions: list[str]
    # Optional synthetic login used by the local seed script only.
    username: str
    display_name: str


# Permission codes use a single dotted kind.action form so admin UI labels split correctly.
ERP_OUTSOURCE_PERMISSIONS: dict[str, list[str]] = {
    "erp_outsource_buyer.read": ["*"],
    "erp_outsource_buyer.execute": ["*"],
    "erp_outsource_approval.read": ["*"],
    "erp_outsource_approval.approve": ["*"],
    "erp_outsource_processor.read": ["*"],
    "erp_outsource_processor.execute": ["*"],
    "erp_outsource_warehouse.read": ["*"],
    "erp_outsource_warehouse.execute": ["*"],
    "erp_outsource_quality.read": ["*"],
    "erp_outsource_quality.execute": ["*"],
}

# Shared read of project context so role accounts can locate work items later.
_COMMON_READ = ("project.read",)

ERP_OUTSOURCE_ROLES: tuple[OutsourceRoleSpec, ...] = (
    {
        "key": "erp_outsource_buyer",
        "role_name": "委外采购员",
        "department_name": "采购",
        "description": "零件/工序委外责任域：填价、发询价、成交价、拒单重选等采购侧办理。",
        "permissions": [
            *_COMMON_READ,
            "erp_outsource_buyer.read",
            "erp_outsource_buyer.execute",
        ],
        "username": "outsource_buyer",
        "display_name": "委外采购员",
    },
    {
        "key": "erp_outsource_approval",
        "role_name": "委外采购主管",
        "department_name": "采购",
        "description": "零件委外超区间下单审批：通过或驳回待审下单。",
        "permissions": [
            *_COMMON_READ,
            "erp_outsource_approval.read",
            "erp_outsource_approval.approve",
            # Supervisor may inspect buyer-side progress without executing buyer actions.
            "erp_outsource_buyer.read",
        ],
        "username": "outsource_approver",
        "display_name": "委外采购主管",
    },
    {
        "key": "erp_outsource_gm",
        "role_name": "总经理",
        "department_name": "管理",
        "description": "零件委外超区间下单审批的总经理节点：通过或驳回待审下单。",
        "permissions": [
            *_COMMON_READ,
            "erp_outsource_approval.read",
            "erp_outsource_approval.approve",
            "erp_outsource_buyer.read",
        ],
        "username": "outsource_gm",
        "display_name": "总经理",
    },
    {
        "key": "erp_outsource_processor",
        "role_name": "委外加工商",
        "department_name": "加工商",
        "description": "加工商侧：报价、接单拒单、收料、成品发货与异常上报（数据范围限本供应商）。",
        "permissions": [
            *_COMMON_READ,
            "erp_outsource_processor.read",
            "erp_outsource_processor.execute",
        ],
        "username": "outsource_processor",
        "display_name": "委外加工商",
    },
    {
        "key": "erp_outsource_warehouse",
        "role_name": "委外仓管",
        "department_name": "仓储",
        "description": "委外原料发货与回厂入库；不含退换货异常闭环。",
        "permissions": [
            *_COMMON_READ,
            "erp_outsource_warehouse.read",
            "erp_outsource_warehouse.execute",
            "warehouse.read",
        ],
        "username": "outsource_warehouse",
        "display_name": "委外仓管",
    },
    {
        "key": "erp_outsource_quality",
        "role_name": "委外质检",
        "department_name": "质检",
        "description": "委外回厂检验结论与质检异常确认。",
        "permissions": [
            *_COMMON_READ,
            "erp_outsource_quality.read",
            "erp_outsource_quality.execute",
        ],
        "username": "outsource_quality",
        "display_name": "委外质检",
    },
)

ERP_OUTSOURCE_DEPARTMENTS: tuple[str, ...] = tuple(
    dict.fromkeys(role["department_name"] for role in ERP_OUTSOURCE_ROLES)
)


def role_by_key(key: str) -> OutsourceRoleSpec:
    for role in ERP_OUTSOURCE_ROLES:
        if role["key"] == key:
            return role
    raise KeyError(key)
