"""四模块验收链路的脱敏合成夹具，不连接正式库、不执行 DDL。"""
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest


@dataclass(frozen=True)
class CommercialChangeFixture:
    customer: SimpleNamespace
    project: SimpleNamespace
    quotation: SimpleNamespace
    bid_notice: SimpleNamespace
    contract: SimpleNamespace
    contact: SimpleNamespace
    internal_mold_number: str


@pytest.fixture
def commercial_change_fixture():
    customer = SimpleNamespace(id=str(uuid4()), name="脱敏客户")
    project = SimpleNamespace(id=str(uuid4()), code="ACCEPTANCE-P001", name="四模块验收项目")
    internal_mold_number = "MOLD-ACCEPTANCE-001"
    quotation = SimpleNamespace(
        id=str(uuid4()), project_id=project.id, version=1,
        source_file_id=str(uuid4()), status="EFFECTIVE",
    )
    bid_notice = SimpleNamespace(
        id=str(uuid4()), project_id=project.id, customer_company=customer.name,
        source_file_id=str(uuid4()), confirmed_type="BID_NOTICE",
    )
    contract = SimpleNamespace(
        id=str(uuid4()), project_id=project.id, contract_number="CONTRACT-ACCEPTANCE-001",
        source_file_id=str(uuid4()), status="EFFECTIVE",
    )
    contact = SimpleNamespace(
        id=str(uuid4()), project_id=project.id, mold_number=internal_mold_number,
        source_file_id=str(uuid4()), status="WAITING_ASSIGNMENT",
    )
    return CommercialChangeFixture(
        customer=customer,
        project=project,
        quotation=quotation,
        bid_notice=bid_notice,
        contract=contract,
        contact=contact,
        internal_mold_number=internal_mold_number,
    )
