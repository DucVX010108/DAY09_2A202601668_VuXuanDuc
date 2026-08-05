"""Regression tests for Member 1's public pipeline contracts."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import unittest

from src.contracts import (
    ContractError,
    DeliveryFacts,
    Handoff,
    OrderProductFacts,
    PaymentFacts,
    PolicyDecision,
    Ticket,
    dumps_json,
)


def valid_ticket_payload() -> dict:
    return {
        "case_id": "EC_001",
        "customer_request": {
            "language": "vi",
            "message": "Hãy kiểm tra đơn hàng.",
            "claimed_order_id": "a" * 32,
        },
        "investigation_scope": {
            "include_customer_history": True,
            "include_product_context": True,
        },
        "policy_version": "EC_POLICY_V2",
    }


class TicketContractTests(unittest.TestCase):
    def test_ticket_rejects_unknown_fields_without_mutating_source(self) -> None:
        """A schema drift must block the ticket instead of being silently ignored."""

        payload = valid_ticket_payload()
        payload["unexpected"] = True
        original = deepcopy(payload)

        with self.assertRaisesRegex(ContractError, "unknown fields"):
            Ticket.from_dict(payload)

        self.assertEqual(payload, original)

    def test_ticket_accepts_only_real_boolean_scope_values(self) -> None:
        """Truthy impostors must not enable the two mandatory K4 scopes."""

        payload = valid_ticket_payload()
        payload["investigation_scope"]["include_customer_history"] = 1

        with self.assertRaisesRegex(ContractError, "must be a boolean"):
            Ticket.from_dict(payload)


class HandoffContractTests(unittest.TestCase):
    def test_handoff_rejects_wrong_agent_route(self) -> None:
        """A payment handoff cannot claim to originate from another specialist."""

        payload = {
            "case_id": "EC_001",
            "from_agent": "customer",
            "to_agent": "policy",
            "task": "investigate_customer",
            "facts": {},
            "evidence_ids": [],
            "missing_or_conflicting_data": [],
            "next_task": "aggregate_customer_facts",
        }

        with self.assertRaisesRegex(ContractError, "to_agent"):
            Handoff.from_dict(payload, expected_agent="customer", case_id="EC_001")

    def test_handoff_rejects_evidence_from_the_wrong_domain(self) -> None:
        """A customer agent must not manufacture payment evidence for another agent."""

        payload = {
            "case_id": "EC_001", "from_agent": "customer", "to_agent": "coordinator",
            "task": "investigate_customer", "facts": {}, "evidence_ids": ["payment:order:1"],
            "missing_or_conflicting_data": [], "next_task": "aggregate_customer_facts",
        }

        with self.assertRaisesRegex(ContractError, "evidence"):
            Handoff.from_dict(payload, expected_agent="customer", case_id="EC_001")

    def test_handoff_detaches_nested_fact_data(self) -> None:
        """An agent cannot mutate an accepted handoff by retaining nested dict references."""

        facts = {"nested": {"value": "before"}}
        payload = {
            "case_id": "EC_001", "from_agent": "customer", "to_agent": "coordinator",
            "task": "investigate_customer", "facts": facts, "evidence_ids": [],
            "missing_or_conflicting_data": [], "next_task": "aggregate_customer_facts",
        }
        accepted = Handoff.from_dict(payload, expected_agent="customer", case_id="EC_001")
        facts["nested"]["value"] = "after"

        self.assertEqual(accepted.facts["nested"]["value"], "before")


class DomainFactContractTests(unittest.TestCase):
    def test_payment_without_items_requires_null_reconciliation(self) -> None:
        """No-item orders must not invent an expected total or reconciliation status."""

        facts = PaymentFacts.from_dict(
            {
                "order_id": "a" * 32,
                "payment_ids": [f"{'a' * 32}:1"],
                "payment_count": 1,
                "payment_types": ["credit_card"],
                "item_total_brl": "0.00",
                "freight_total_brl": "0.00",
                "expected_total_brl": None,
                "payment_total_brl": "10.00",
                "difference_brl": None,
                "reconciled": None,
            },
            item_count=0,
        )

        self.assertIsNone(facts.expected_total_brl)
        self.assertEqual(facts.payment_total_brl, Decimal("10.00"))

    def test_no_item_order_rejects_nonzero_entity_counts(self) -> None:
        """No-item facts with seller/product counts would fabricate entities downstream."""

        with self.assertRaisesRegex(ContractError, "no-item"):
            OrderProductFacts.from_dict({
                "order_id": "a" * 32, "order_status": "delivered", "customer_id": "customer",
                "item_ids": [], "seller_ids": [], "product_ids": [], "category_names": [],
                "item_count": 0, "seller_count": 1, "product_count": 0, "category_count": 0, "items": [],
            })

    def test_delivery_rejects_variance_without_source_timestamps(self) -> None:
        """A variance without both CSV timestamps is an inferred delivery fact."""

        with self.assertRaisesRegex(ContractError, "delivery_variance"):
            DeliveryFacts.from_dict({
                "order_id": "a" * 32, "delivered_at": None, "estimated_delivery_at": "2018-01-01 00:00:00",
                "carrier_handoff_at": None, "delivery_variance_hours": "1.00",
                "seller_handoff_analysis": [], "late_handoff_seller_ids": [],
            })

    def test_policy_rejects_unknown_primary_issue(self) -> None:
        """The coordinator must not publish decisions outside EC_POLICY_V2."""

        with self.assertRaisesRegex(ContractError, "primary_issue"):
            PolicyDecision.from_dict({
                "primary_issue": "invented_issue", "secondary_issues": [], "case_status": "no_action",
                "confidence": 1.0, "ranked_causes": [{"cause_code": "DELIVERY_WITHIN_ESTIMATE", "rank": 1}],
                "responsible_parties": [], "recommended_refund_brl": "0.00",
                "resolution_actions": ["reject_late_refund"], "policy_evidence_id": "policy:DELIVERY_WITHIN_ESTIMATE",
            })

    def test_policy_rejects_unknown_secondary_as_contract_error(self) -> None:
        """Schema errors must not leak a ValueError that bypasses structured blocking."""

        with self.assertRaisesRegex(ContractError, "secondary_issues"):
            PolicyDecision.from_dict({
                "primary_issue": "unsupported_late_claim", "secondary_issues": ["invented_secondary"], "case_status": "no_action",
                "confidence": 1.0, "ranked_causes": [{"cause_code": "DELIVERY_WITHIN_ESTIMATE", "rank": 1}],
                "responsible_parties": [], "recommended_refund_brl": "0.00",
                "resolution_actions": ["reject_late_refund"], "policy_evidence_id": "policy:DELIVERY_WITHIN_ESTIMATE",
            })

    def test_json_serialization_is_stable_and_rounds_decimals(self) -> None:
        """Equivalent output must be byte-stable and expose money at two decimals."""

        value = {"z": Decimal("1.005"), "a": "Tiếng Việt"}

        self.assertEqual(
            dumps_json(value),
            '{"a":"Tiếng Việt","z":1.01}\n',
        )


if __name__ == "__main__":
    unittest.main()
