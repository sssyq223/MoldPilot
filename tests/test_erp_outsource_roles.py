"""ERP outsource role vocabulary registration."""
from domain_packs.mold.authorization import DIMENSIONS, PERMISSIONS
from domain_packs.mold.erp.procurement.erp_outsource_roles import (
    ERP_OUTSOURCE_DEPARTMENTS,
    ERP_OUTSOURCE_PERMISSIONS,
    ERP_OUTSOURCE_ROLES,
    role_by_key,
)


def test_erp_outsource_permissions_registered():
    assert set(ERP_OUTSOURCE_PERMISSIONS) <= set(PERMISSIONS)
    for permission, fields in ERP_OUTSOURCE_PERMISSIONS.items():
        assert PERMISSIONS[permission] == fields
        kind, action = permission.split(".", 1)
        assert kind.startswith("erp_outsource_")
        assert action in {"read", "execute", "approve"}


def test_erp_outsource_role_catalog_isolates_five_roles():
    assert len(ERP_OUTSOURCE_ROLES) == 5
    keys = [role["key"] for role in ERP_OUTSOURCE_ROLES]
    assert keys == [
        "erp_outsource_buyer",
        "erp_outsource_approval",
        "erp_outsource_processor",
        "erp_outsource_warehouse",
        "erp_outsource_quality",
    ]
    assert ERP_OUTSOURCE_DEPARTMENTS == ("采购", "加工商", "仓储", "质检")

    buyer = role_by_key("erp_outsource_buyer")
    warehouse = role_by_key("erp_outsource_warehouse")
    quality = role_by_key("erp_outsource_quality")
    processor = role_by_key("erp_outsource_processor")
    approval = role_by_key("erp_outsource_approval")

    assert "erp_outsource_buyer.execute" in buyer["permissions"]
    assert "erp_outsource_warehouse.execute" not in buyer["permissions"]
    assert "erp_outsource_quality.execute" not in warehouse["permissions"]
    assert "erp_outsource_buyer.execute" not in processor["permissions"]
    assert "erp_outsource_approval.approve" in approval["permissions"]
    assert "erp_outsource_buyer.execute" not in approval["permissions"]
    assert "erp_outsource_buyer.read" in approval["permissions"]
    assert quality["department_name"] == "质检"


def test_supplier_scope_dimension_available_for_processor():
    assert "supplier_id" in DIMENSIONS
