"""Unit tests for OlistRepository. Owned by Member 2."""

import pytest
from src.repository import OlistRepository


@pytest.fixture(scope="module")
def repo():
    return OlistRepository("data")


def test_repository_loads_orders(repo):
    assert len(repo.orders) > 0
    # EC_001 order_id from input sample
    sample_oid = "9b75cdaf2d85857ef023980e15d01546"
    order = repo.get_order(sample_oid)
    assert order is not None
    assert order["order_id"] == sample_oid


def test_repository_loads_customers(repo):
    assert len(repo.customers) > 0
    sample_oid = "9b75cdaf2d85857ef023980e15d01546"
    order = repo.get_order(sample_oid)
    assert order is not None
    cid = order["customer_id"]
    unique_id = repo.get_customer_unique_id(cid)
    assert unique_id is not None


def test_repository_category_translation(repo):
    translated = repo.translate_category("perfumaria")
    assert translated == "perfumery"
    assert repo.translate_category("non_existent_category") == "non_existent_category"
