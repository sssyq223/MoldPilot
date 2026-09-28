import pytest

pytest_plugins = ("commercial_change_db",)


def test_fixture_contains_one_linked_business_chain(commercial_change_fixture):
    data = commercial_change_fixture
    assert data.quotation.project_id == data.project.id
    assert data.bid_notice.customer_company == data.customer.name
    assert data.contract.project_id == data.project.id
    assert data.contact.project_id == data.project.id
    assert data.contact.mold_number == data.internal_mold_number
