"""Behavioral tests for the Member 1 orchestration boundary."""

from __future__ import annotations

from decimal import Decimal
import unittest
from unittest.mock import patch

from src.contracts import RunStatus, Ticket
from src.coordinator import process_case


ORDER_ID = "a" * 32


def ticket() -> Ticket:
    return Ticket.from_dict(
        {
            "case_id": "EC_001",
            "customer_request": {"language": "vi", "message": "Kiểm tra", "claimed_order_id": ORDER_ID},
            "investigation_scope": {"include_customer_history": True, "include_product_context": True},
            "policy_version": "EC_POLICY_V2",
        }
    )


def handoff(agent: str, facts: dict, evidence: list[str]) -> dict:
    task, next_task = {
        "customer": ("investigate_customer", "aggregate_customer_facts"),
        "order_product": ("investigate_order_and_product", "aggregate_order_product_facts"),
        "payment": ("investigate_payment", "aggregate_payment_facts"),
        "delivery": ("investigate_delivery", "aggregate_delivery_facts"),
        "policy": ("apply_ec_policy_v2", "assemble_and_verify_output"),
    }[agent]
    return {
        "case_id": "EC_001",
        "from_agent": agent,
        "to_agent": "coordinator",
        "task": task,
        "facts": facts,
        "evidence_ids": evidence,
        "missing_or_conflicting_data": [],
        "next_task": next_task,
    }


def collaborator_handoffs() -> dict[str, dict]:
    item_id = f"{ORDER_ID}:1"
    return {
        "customer": handoff("customer", {
            "claimed_order_id": ORDER_ID, "customer_id": "customer-1", "customer_unique_id": "unique-1",
            "related_order_ids": [], "related_order_count": 0, "has_related_orders": False,
        }, []),
        "order_product": handoff("order_product", {
            "order_id": ORDER_ID, "order_status": "delivered", "customer_id": "customer-1",
            "item_ids": [item_id], "seller_ids": ["seller-1"], "product_ids": ["product-1"],
            "category_names": ["books"], "item_count": 1, "seller_count": 1, "product_count": 1,
            "category_count": 1,
            "items": [{"order_id": ORDER_ID, "order_item_id": "1", "product_id": "product-1", "seller_id": "seller-1", "price_raw": "10.00", "freight_value_raw": "2.00", "shipping_limit_date": "2018-01-01 00:00:00", "category_name": "books"}],
        }, [f"order:{ORDER_ID}", f"item:{item_id}", "seller:seller-1"]),
        "payment": handoff("payment", {
            "order_id": ORDER_ID, "payment_ids": [f"{ORDER_ID}:1"], "payment_count": 1,
            "payment_types": ["credit_card"], "item_total_brl": "10.00", "freight_total_brl": "2.00",
            "expected_total_brl": "12.00", "payment_total_brl": "12.00", "difference_brl": "0.00", "reconciled": True,
        }, [f"payment:{ORDER_ID}:1"]),
        "delivery": handoff("delivery", {
            "order_id": ORDER_ID, "delivered_at": "2018-01-02 00:00:00",
            "estimated_delivery_at": "2018-01-03 00:00:00", "carrier_handoff_at": "2018-01-01 00:00:00",
            "delivery_variance_hours": "-24.00", "seller_handoff_analysis": [{"seller_id": "seller-1", "shipping_limit_at": "2018-01-01 00:00:00", "handoff_variance_hours": "0.00", "late_handoff": False}],
            "late_handoff_seller_ids": [],
        }, []),
        "policy": handoff("policy", {
            "primary_issue": "unsupported_late_claim", "secondary_issues": [], "case_status": "no_action",
            "confidence": 1.0, "ranked_causes": [{"cause_code": "DELIVERY_WITHIN_ESTIMATE", "rank": 1}],
            "responsible_parties": [], "recommended_refund_brl": "0.00",
            "resolution_actions": ["reject_late_refund"], "policy_evidence_id": "policy:DELIVERY_WITHIN_ESTIMATE",
        }, ["policy:DELIVERY_WITHIN_ESTIMATE"]),
    }


class CoordinatorTests(unittest.TestCase):
    def test_verified_case_assembles_output_only_after_verifier_passes(self) -> None:
        """Removing the verifier gate must make this test fail by exposing an unverified output."""

        handoffs = collaborator_handoffs()
        order: list[str] = []

        def agent(name: str):
            def run(_ticket: dict, _repository: object) -> dict:
                order.append(name)
                return handoffs[name]
            return run

        def policy(facts: dict) -> dict:
            self.assertEqual(facts["ticket"]["case_id"], "EC_001")
            order.append("policy")
            return handoffs["policy"]

        def verifier(output: dict, facts: dict, repository: object) -> list[str]:
            order.append("verifier")
            self.assertEqual(output["case_id"], "EC_001")
            self.assertEqual(output["payment_reconciliation"]["payment_total_brl"], 12.0)
            self.assertIs(repository, repo)
            return []

        repo = object()
        with (
            patch("src.coordinator.investigate_customer", agent("customer")),
            patch("src.coordinator.investigate_order_and_product", agent("order_product")),
            patch("src.coordinator.investigate_payment", agent("payment")),
            patch("src.coordinator.investigate_delivery", agent("delivery")),
            patch("src.coordinator.apply_policy", policy),
            patch("src.coordinator.verify_output", verifier),
        ):
            result = process_case(ticket(), repo)

        self.assertEqual(order, ["customer", "order_product", "payment", "delivery", "policy", "verifier"])
        self.assertEqual(result.status, RunStatus.VERIFIED)
        self.assertEqual(result.output["evidence_ids"][-1], "policy:DELIVERY_WITHIN_ESTIMATE")

    def test_verifier_error_blocks_case_without_output(self) -> None:
        """A verifier rejection must never be converted into a publishable fallback document."""

        handoffs = collaborator_handoffs()
        with (
            patch("src.coordinator.investigate_customer", lambda *_: handoffs["customer"]),
            patch("src.coordinator.investigate_order_and_product", lambda *_: handoffs["order_product"]),
            patch("src.coordinator.investigate_payment", lambda *_: handoffs["payment"]),
            patch("src.coordinator.investigate_delivery", lambda *_: handoffs["delivery"]),
            patch("src.coordinator.apply_policy", lambda *_: handoffs["policy"]),
            patch("src.coordinator.verify_output", lambda *_: ["evidence invalid"]),
        ):
            result = process_case(ticket(), object())

        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertIsNone(result.output)
        self.assertEqual(result.errors[0].code, "VERIFICATION_FAILED")

    def test_bad_customer_handoff_reports_its_actual_stage_and_agent(self) -> None:
        """A malformed first handoff must not be misdiagnosed later as an assembly failure."""

        with patch("src.coordinator.investigate_customer", lambda *_: {}):
            result = process_case(ticket(), object())

        self.assertEqual(result.status, RunStatus.BLOCKED)
        self.assertEqual(result.errors[0].code, "HANDOFF_CONTRACT_INVALID")
        self.assertEqual(result.errors[0].stage, "customer")
        self.assertEqual(result.errors[0].agent, "customer")


if __name__ == "__main__":
    unittest.main()
