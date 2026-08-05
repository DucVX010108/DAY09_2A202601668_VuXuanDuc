"""Verifier regression tests. Owned by Member 5."""

from copy import deepcopy

from src.verifier import verify_output


class FakeRepository:
    def has_order(self, order_id: str) -> bool:
        return order_id == "order-1"

    def has_item(self, order_id: str, item_id: str) -> bool:
        return (order_id, item_id) == ("order-1", "1")

    def has_payment(self, order_id: str, sequential: str) -> bool:
        return (order_id, sequential) in {("order-1", "1"), ("order-1", "2")}

    def has_seller(self, seller_id: str) -> bool:
        return seller_id == "seller-1"


def _facts() -> dict:
    return {
        "case_id": "EC_001",
        "order": {
            "order_id": "order-1",
            "order_status": "delivered",
            "order_delivered_customer_date": "2018-03-31 15:23:33",
            "order_estimated_delivery_date": "2018-03-28 00:00:00",
            "order_delivered_carrier_date": "2018-03-15 21:33:51",
        },
        "items": [{
            "order_item_id": "1",
            "product_id": "product-1",
            "seller_id": "seller-1",
            "shipping_limit_date": "2018-03-15 20:31:15",
            "price": "194.00",
            "freight_value": "18.27",
        }],
        "payments": [{
            "payment_sequential": "1",
            "payment_type": "credit_card",
            "payment_value": "212.27",
        }],
        "customer": {
            "customer_unique_id": "customer-1",
            "related_order_ids": ["order-old"],
        },
        "products": [{"product_id": "product-1", "product_category_name": "bed_bath_table"}],
    }


def _valid_output() -> dict:
    return {
        "case_id": "EC_001",
        "case_assessment": {
            "primary_issue": "late_delivery_seller",
            "secondary_issues": ["repeat_customer"],
            "case_status": "action_required",
            "confidence": 1.0,
        },
        "affected_entities": {
            "order_ids": ["order-1"],
            "item_ids": ["order-1:1"],
            "seller_ids": ["seller-1"],
            "payment_ids": ["order-1:1"],
        },
        "customer_context": {
            "customer_unique_id": "customer-1",
            "related_order_ids": ["order-old"],
        },
        "product_context": {
            "product_ids": ["product-1"],
            "category_names": ["bed_bath_table"],
        },
        "delivery_analysis": {
            "delivered_at": "2018-03-31 15:23:33",
            "estimated_delivery_at": "2018-03-28 00:00:00",
            "carrier_handoff_at": "2018-03-15 21:33:51",
            "delivery_variance_hours": 87.39,
            "seller_handoff_analysis": [{
                "seller_id": "seller-1",
                "shipping_limit_at": "2018-03-15 20:31:15",
                "handoff_variance_hours": 1.04,
                "late_handoff": True,
            }],
            "late_handoff_seller_ids": ["seller-1"],
        },
        "payment_reconciliation": {
            "currency": "BRL",
            "item_total_brl": 194.0,
            "freight_total_brl": 18.27,
            "expected_total_brl": 212.27,
            "payment_total_brl": 212.27,
            "difference_brl": 0.0,
            "reconciled": True,
            "payment_types": ["credit_card"],
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": "SELLER_HANDOFF_AFTER_LIMIT", "rank": 1}],
            "responsible_parties": [{"party_type": "seller", "party_id": "seller-1"}],
        },
        "evidence_ids": [
            "order:order-1",
            "item:order-1:1",
            "payment:order-1:1",
            "seller:seller-1",
            "policy:SELLER_HANDOFF_AFTER_LIMIT",
        ],
        "financial_resolution": {"currency": "BRL", "recommended_refund_brl": 18.27},
        "resolution_actions": ["refund_freight", "review_seller_handoff", "verify_refund_completion"],
    }


def test_valid_late_seller_output_passes() -> None:
    assert verify_output(_valid_output(), _facts(), FakeRepository()) == []


def test_wrong_refund_is_rejected() -> None:
    candidate = deepcopy(_valid_output())
    candidate["financial_resolution"]["recommended_refund_brl"] = 100.0
    assert "policy.recommended_refund_brl" in verify_output(candidate, _facts(), FakeRepository())


def test_wrong_evidence_is_rejected() -> None:
    candidate = deepcopy(_valid_output())
    candidate["evidence_ids"][0] = "order:other-order"
    assert "reference.evidence_ids" in verify_output(candidate, _facts(), FakeRepository())


def test_wrong_action_order_is_rejected() -> None:
    candidate = deepcopy(_valid_output())
    candidate["resolution_actions"] = ["verify_refund_completion", "refund_freight", "review_seller_handoff"]
    assert "policy.resolution_actions" in verify_output(candidate, _facts(), FakeRepository())


def test_invalid_reference_is_rejected() -> None:
    candidate = deepcopy(_valid_output())
    candidate["affected_entities"]["seller_ids"] = ["unknown-seller"]
    errors = verify_output(candidate, _facts(), FakeRepository())
    assert "reference.unknown_seller:unknown-seller" in errors

