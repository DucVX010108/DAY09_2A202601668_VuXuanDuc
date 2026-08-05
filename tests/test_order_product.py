"""Unit tests for Order & Product Agent. Owned by Member 2."""

import pytest
from src.agents.order_product import investigate_order_and_product
from src.repository import OlistRepository


@pytest.fixture(scope="module")
def repo():
    return OlistRepository("data")


def test_order_product_single_item(repo):
    ticket = {
        "case_id": "EC_001",
        "customer_request": {
            "claimed_order_id": "9b75cdaf2d85857ef023980e15d01546"
        },
    }
    result = investigate_order_and_product(ticket, repo)

    assert result["case_id"] == "EC_001"
    assert result["from_agent"] == "order_product"
    assert "order" in result["facts"]
    assert result["facts"]["order_id"] == "9b75cdaf2d85857ef023980e15d01546"

    # Evidence IDs check
    evidence_ids = result["evidence_ids"]
    assert any(ev.startswith("order:9b75cdaf2d85857ef023980e15d01546") for ev in evidence_ids)
    assert len(result["facts"]["items"]) <= 5
    assert len(result["facts"]["seller_ids"]) <= 3
    assert len(result["facts"]["product_ids"]) <= 5
    assert len(result["facts"]["category_names"]) <= 5


def test_order_product_limits(repo):
    # Find an order with multiple items
    multi_item_oid = None
    for oid, items in repo.order_items.items():
        if len(items) >= 2:
            multi_item_oid = oid
            break

    if multi_item_oid:
        ticket = {
            "case_id": "EC_MULTI",
            "claimed_order_id": multi_item_oid,
        }
        result = investigate_order_and_product(ticket, repo)
        assert len(result["facts"]["items"]) <= 5
        assert len(result["facts"]["seller_ids"]) <= 3
        assert len(result["facts"]["product_ids"]) <= 5
        assert len(result["facts"]["category_names"]) <= 5
        assert result["facts"]["has_multiple_items"] is True


def test_order_product_missing_items(repo):
    # Find an order that has no items in order_items
    no_item_oid = None
    for oid in repo.orders.keys():
        if oid not in repo.order_items:
            no_item_oid = oid
            break

    if no_item_oid:
        ticket = {
            "case_id": "EC_NO_ITEM",
            "claimed_order_id": no_item_oid,
        }
        result = investigate_order_and_product(ticket, repo)
        assert result["facts"]["items"] == []
        assert result["facts"]["item_ids"] == []
        assert result["facts"]["seller_ids"] == []
        assert result["facts"]["product_ids"] == []
        assert result["facts"]["category_names"] == []
        assert any("order_has_no_items" in err for err in result["missing_or_conflicting_data"])
