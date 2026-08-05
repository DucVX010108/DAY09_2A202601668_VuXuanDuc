"""Unit tests for Customer Agent. Owned by Member 2."""

import pytest
from src.agents.customer import investigate_customer
from src.repository import OlistRepository


@pytest.fixture(scope="module")
def repo():
    return OlistRepository("data")


def test_customer_agent_single_or_repeat(repo):
    ticket = {
        "case_id": "EC_001",
        "customer_request": {
            "claimed_order_id": "9b75cdaf2d85857ef023980e15d01546"
        },
    }
    result = investigate_customer(ticket, repo)

    assert result["case_id"] == "EC_001"
    assert result["from_agent"] == "customer"
    assert "customer_unique_id" in result["facts"]
    assert isinstance(result["facts"]["related_order_ids"], list)
    assert len(result["facts"]["related_order_ids"]) <= 5
    # Historical orders must NOT be in evidence
    assert result["evidence_ids"] == []


def test_customer_agent_missing_order(repo):
    ticket = {
        "case_id": "EC_INVALID",
        "claimed_order_id": "non_existent_order_id_12345",
    }
    result = investigate_customer(ticket, repo)
    assert result["case_id"] == "EC_INVALID"
    assert any("order_not_found" in err for err in result["missing_or_conflicting_data"])


def test_customer_agent_repeat_customer_limit(repo):
    # Find a customer_unique_id with multiple orders in repository
    multi_order_uniq = None
    for uniq_id, oids in repo.customer_unique_to_orders.items():
        if len(oids) >= 3:
            multi_order_uniq = uniq_id
            break

    if multi_order_uniq:
        oids = repo.customer_unique_to_orders[multi_order_uniq]
        target_oid = oids[0]
        ticket = {
            "case_id": "EC_TEST_REPEAT",
            "customer_request": {"claimed_order_id": target_oid},
        }
        result = investigate_customer(ticket, repo)
        related = result["facts"]["related_order_ids"]

        assert target_oid not in related
        assert len(related) <= 5
        assert result["facts"]["is_repeat_customer"] is True
