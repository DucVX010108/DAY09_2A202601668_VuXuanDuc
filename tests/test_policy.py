"""EC_POLICY_V2 test matrix. Owned by Member 4.

17 deterministic test cases covering every policy branch and precedence rule
listed in docs/policy_matrix.md. All fixtures use synthetic but structurally
valid facts; no CSV access required.

Test IDs map to matrix rows P01–P17.
"""

import pytest

from src.policy import (
    PolicyDecisionError,
    apply_policy,
)


# ---------------------------------------------------------------------------
# Shared fixture factory
# ---------------------------------------------------------------------------


def _base_facts(**overrides) -> dict:
    """Return a minimal valid facts dict; override individual fields as needed."""
    base = {
        "order_id": "order_abc123",
        "order_status": "delivered",
        "payment_total_brl": 212.27,
        "payment_count": 1,
        "reconciled": True,
        "freight_total_brl": 18.27,
        "item_total_brl": 194.00,
        "expected_total_brl": 212.27,
        "difference_brl": 0.00,
        "delivery_variance_hours": -5.0,   # on-time by default
        "late_handoff": False,
        "late_handoff_seller_ids": [],
        "item_count": 1,
        "seller_count": 1,
        "category_count": 1,
        "related_order_ids": [],
        "evidence_ids": [
            "order:order_abc123",
            "item:order_abc123:1",
            "payment:order_abc123:1",
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# P01 — canceled_order_paid (basic)
# ---------------------------------------------------------------------------


def test_p01_canceled_order_paid():
    """Canceled order with payment total > 0 → canceled_order_paid, full refund."""
    facts = _base_facts(order_status="canceled", payment_total_brl=150.00)
    decision = apply_policy(facts)

    assert decision["primary_issue"] == "canceled_order_paid"
    assert decision["root_cause_analysis"]["ranked_causes"][0]["cause_code"] == "ORDER_CANCELED_AFTER_PAYMENT"
    assert decision["financial_resolution"]["recommended_refund_brl"] == 150.00
    assert decision["case_status"] == "action_required"
    assert decision["resolution_actions"][0] == "issue_full_refund"
    assert decision["root_cause_analysis"]["responsible_parties"] == [
        {"party_type": "platform", "party_id": "OLIST_PLATFORM"}
    ]
    assert "policy:ORDER_CANCELED_AFTER_PAYMENT" in decision["evidence_ids"]


# ---------------------------------------------------------------------------
# P02 — unavailable_order_paid (basic)
# ---------------------------------------------------------------------------


def test_p02_unavailable_order_paid():
    """Unavailable order with payment total > 0 → unavailable_order_paid, full refund."""
    facts = _base_facts(order_status="unavailable", payment_total_brl=80.50)
    decision = apply_policy(facts)

    assert decision["primary_issue"] == "unavailable_order_paid"
    assert decision["root_cause_analysis"]["ranked_causes"][0]["cause_code"] == "ORDER_UNAVAILABLE_AFTER_PAYMENT"
    assert decision["financial_resolution"]["recommended_refund_brl"] == 80.50
    assert decision["case_status"] == "action_required"
    assert decision["resolution_actions"][0] == "issue_full_refund"
    assert decision["root_cause_analysis"]["responsible_parties"] == [
        {"party_type": "platform", "party_id": "OLIST_PLATFORM"}
    ]


# ---------------------------------------------------------------------------
# P03 — precedence: canceled beats late_delivery_seller
# ---------------------------------------------------------------------------


def test_p03_canceled_beats_late_delivery_seller():
    """canceled status + late delivery with late seller → still canceled_order_paid (P1 wins P3)."""
    facts = _base_facts(
        order_status="canceled",
        payment_total_brl=200.00,
        delivery_variance_hours=72.0,
        late_handoff=True,
        late_handoff_seller_ids=["seller_xyz"],
    )
    decision = apply_policy(facts)
    assert decision["primary_issue"] == "canceled_order_paid"


# ---------------------------------------------------------------------------
# P04 — precedence: canceled beats valid_split_payment
# ---------------------------------------------------------------------------


def test_p04_canceled_beats_valid_split_payment():
    """canceled status + 2-row reconciled payment → still canceled_order_paid (P1 wins P5)."""
    facts = _base_facts(
        order_status="canceled",
        payment_total_brl=100.00,
        payment_count=2,
        reconciled=True,
    )
    decision = apply_policy(facts)
    assert decision["primary_issue"] == "canceled_order_paid"


# ---------------------------------------------------------------------------
# P05 — precedence: unavailable beats late_delivery_seller
# ---------------------------------------------------------------------------


def test_p05_unavailable_beats_late_delivery_seller():
    """unavailable + late delivery with late seller → unavailable_order_paid (P2 wins P3)."""
    facts = _base_facts(
        order_status="unavailable",
        payment_total_brl=60.00,
        delivery_variance_hours=48.0,
        late_handoff=True,
        late_handoff_seller_ids=["seller_abc"],
    )
    decision = apply_policy(facts)
    assert decision["primary_issue"] == "unavailable_order_paid"


# ---------------------------------------------------------------------------
# P06 — late_delivery_seller (basic)
# ---------------------------------------------------------------------------


def test_p06_late_delivery_seller():
    """Late delivery + at least one late seller handoff → late_delivery_seller, freight refund."""
    seller_id = "seller_001"
    facts = _base_facts(
        delivery_variance_hours=36.0,
        late_handoff=True,
        late_handoff_seller_ids=[seller_id],
        freight_total_brl=25.50,
        evidence_ids=[
            "order:order_abc123",
            "item:order_abc123:1",
            "payment:order_abc123:1",
            f"seller:{seller_id}",
        ],
    )
    decision = apply_policy(facts)

    assert decision["primary_issue"] == "late_delivery_seller"
    assert decision["root_cause_analysis"]["ranked_causes"][0]["cause_code"] == "SELLER_HANDOFF_AFTER_LIMIT"
    assert decision["financial_resolution"]["recommended_refund_brl"] == 25.50
    assert decision["case_status"] == "action_required"
    assert decision["resolution_actions"][0] == "refund_freight"
    assert decision["root_cause_analysis"]["responsible_parties"] == [
        {"party_type": "seller", "party_id": seller_id}
    ]
    assert "policy:SELLER_HANDOFF_AFTER_LIMIT" in decision["evidence_ids"]


# ---------------------------------------------------------------------------
# P07 — late_delivery_logistics (basic)
# ---------------------------------------------------------------------------


def test_p07_late_delivery_logistics():
    """Late delivery + no late seller → late_delivery_logistics, freight refund."""
    facts = _base_facts(
        delivery_variance_hours=24.0,
        late_handoff=False,
        late_handoff_seller_ids=[],
        freight_total_brl=12.00,
    )
    decision = apply_policy(facts)

    assert decision["primary_issue"] == "late_delivery_logistics"
    assert decision["root_cause_analysis"]["ranked_causes"][0]["cause_code"] == "CARRIER_DELIVERED_AFTER_ESTIMATE"
    assert decision["financial_resolution"]["recommended_refund_brl"] == 12.00
    assert decision["case_status"] == "action_required"
    assert decision["resolution_actions"][0] == "refund_freight"
    assert decision["root_cause_analysis"]["responsible_parties"] == [
        {"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}
    ]


# ---------------------------------------------------------------------------
# P08 — seller vs logistics: late_handoff flag is the deciding factor
# ---------------------------------------------------------------------------


def test_p08_seller_vs_logistics_late_handoff_true():
    """late_handoff=True → late_delivery_seller."""
    facts = _base_facts(
        delivery_variance_hours=10.0,
        late_handoff=True,
        late_handoff_seller_ids=["seller_x"],
    )
    assert apply_policy(facts)["primary_issue"] == "late_delivery_seller"


def test_p08_seller_vs_logistics_late_handoff_false():
    """late_handoff=False → late_delivery_logistics."""
    facts = _base_facts(
        delivery_variance_hours=10.0,
        late_handoff=False,
        late_handoff_seller_ids=[],
    )
    assert apply_policy(facts)["primary_issue"] == "late_delivery_logistics"


# ---------------------------------------------------------------------------
# P09 — valid_split_payment (basic)
# ---------------------------------------------------------------------------


def test_p09_valid_split_payment():
    """3 payment rows + reconciled=True + on-time delivery → valid_split_payment, zero refund."""
    facts = _base_facts(
        payment_count=3,
        reconciled=True,
        delivery_variance_hours=-5.0,
        payment_total_brl=212.27,
    )
    decision = apply_policy(facts)

    assert decision["primary_issue"] == "valid_split_payment"
    assert decision["root_cause_analysis"]["ranked_causes"][0]["cause_code"] == "MULTIPLE_PAYMENTS_RECONCILED"
    assert decision["financial_resolution"]["recommended_refund_brl"] == 0.00
    assert decision["case_status"] == "no_action"
    assert decision["resolution_actions"][0] == "explain_valid_split_payment"
    assert decision["root_cause_analysis"]["responsible_parties"] == []


# ---------------------------------------------------------------------------
# P10 — tolerance boundary: difference_brl = 0.10 → reconciled
# ---------------------------------------------------------------------------


def test_p10_split_payment_tolerance_exactly_010():
    """Tolerance boundary: abs(difference) = 0.10 BRL → reconciled=True → valid_split_payment."""
    facts = _base_facts(
        payment_count=2,
        reconciled=True,       # M3 computes: abs(0.10) <= 0.10 → True
        difference_brl=0.10,
        delivery_variance_hours=-1.0,
    )
    decision = apply_policy(facts)
    assert decision["primary_issue"] == "valid_split_payment"


# ---------------------------------------------------------------------------
# P11 — tolerance exceeded: difference_brl = 0.11 → reconciled=False
# ---------------------------------------------------------------------------


def test_p11_split_payment_tolerance_exceeds_010():
    """Tolerance exceeded: abs(difference) = 0.11 → reconciled=False → NOT valid_split_payment.

    On-time delivery + unreconciled 2-row payment does not match any policy branch.
    This is a data-quality ambiguity → engine must raise PolicyDecisionError
    so that M1/M5 can handle it as a data error (never invent a decision).
    """
    facts = _base_facts(
        payment_count=2,
        reconciled=False,      # M3 computes: abs(0.11) > 0.10 → False
        difference_brl=0.11,
        delivery_variance_hours=-3.0,  # on-time
    )
    # Engine must NOT pick valid_split_payment (reconciled=False)
    # and must NOT pick unsupported_late_claim (reconciled=False).
    # Correct behaviour: raise, let M1/M5 flag as data error.
    with pytest.raises(PolicyDecisionError, match="no_policy_branch_matched"):
        apply_policy(facts)


# ---------------------------------------------------------------------------
# P12 — unsupported_late_claim
# ---------------------------------------------------------------------------


def test_p12_unsupported_late_claim():
    """On-time delivery + reconciled=True + single payment → unsupported_late_claim."""
    facts = _base_facts(
        delivery_variance_hours=-2.0,
        reconciled=True,
        payment_count=1,
    )
    decision = apply_policy(facts)

    assert decision["primary_issue"] == "unsupported_late_claim"
    assert decision["root_cause_analysis"]["ranked_causes"][0]["cause_code"] == "DELIVERY_WITHIN_ESTIMATE"
    assert decision["financial_resolution"]["recommended_refund_brl"] == 0.00
    assert decision["case_status"] == "no_action"
    assert decision["resolution_actions"][0] == "reject_late_refund"
    assert decision["root_cause_analysis"]["responsible_parties"] == []


# ---------------------------------------------------------------------------
# P13 — secondary issues in exact business order
# ---------------------------------------------------------------------------


def test_p13_secondary_issues_correct_order():
    """All 5 secondary issue conditions met → appear in correct business order."""
    facts = _base_facts(
        delivery_variance_hours=-1.0,
        reconciled=True,
        payment_count=2,       # → split_payment
        item_count=3,          # → multi_item_order
        seller_count=2,        # → multi_seller_order
        related_order_ids=["order_prev1"],  # → repeat_customer
        category_count=2,      # → multiple_categories
    )
    decision = apply_policy(facts)

    expected_order = [
        "multi_item_order",
        "multi_seller_order",
        "split_payment",
        "repeat_customer",
        "multiple_categories",
    ]
    assert decision["secondary_issues"] == expected_order


def test_p13_secondary_issues_partial():
    """Only some secondary conditions met → only those appear, in order."""
    facts = _base_facts(
        delivery_variance_hours=-1.0,
        reconciled=True,
        payment_count=1,       # no split_payment
        item_count=2,          # multi_item_order
        seller_count=1,        # no multi_seller_order
        related_order_ids=[],  # no repeat_customer
        category_count=3,      # multiple_categories
    )
    decision = apply_policy(facts)
    assert decision["secondary_issues"] == ["multi_item_order", "multiple_categories"]


# ---------------------------------------------------------------------------
# P14 — actions order for late_delivery_seller with multi-seller + split payment
# ---------------------------------------------------------------------------


def test_p14_actions_order_late_delivery_seller():
    """late_delivery_seller + multi_seller + split payment → actions in spec order."""
    facts = _base_facts(
        delivery_variance_hours=36.0,
        late_handoff=True,
        late_handoff_seller_ids=["seller_a", "seller_b"],
        item_count=2,
        seller_count=2,        # → multi_seller_order → coordinate_multi_seller_case
        payment_count=2,       # → split_payment → verify_payment_allocation
        category_count=1,
        related_order_ids=[],
        freight_total_brl=30.00,
    )
    decision = apply_policy(facts)

    actions = decision["resolution_actions"]
    assert actions[0] == "refund_freight"
    assert actions[1] == "review_seller_handoff"
    assert actions[2] == "verify_refund_completion"
    assert actions[3] == "coordinate_multi_seller_case"
    assert actions[4] == "verify_payment_allocation"


def test_p14_actions_review_carrier_delay_for_logistics():
    """late_delivery_logistics → review_carrier_delay (not review_seller_handoff)."""
    facts = _base_facts(
        delivery_variance_hours=12.0,
        late_handoff=False,
        late_handoff_seller_ids=[],
    )
    decision = apply_policy(facts)
    assert "review_carrier_delay" in decision["resolution_actions"]
    assert "review_seller_handoff" not in decision["resolution_actions"]


# ---------------------------------------------------------------------------
# P15 — verify_payment_allocation excluded when primary is valid_split_payment
# ---------------------------------------------------------------------------


def test_p15_no_verify_payment_allocation_for_valid_split():
    """valid_split_payment primary → verify_payment_allocation must NOT appear in actions."""
    facts = _base_facts(
        payment_count=3,
        reconciled=True,
        delivery_variance_hours=-5.0,
    )
    decision = apply_policy(facts)

    assert decision["primary_issue"] == "valid_split_payment"
    # split_payment is in secondary_issues
    assert "split_payment" in decision["secondary_issues"]
    # but verify_payment_allocation must be absent
    assert "verify_payment_allocation" not in decision["resolution_actions"]
    # and explain_valid_split_payment IS the primary action
    assert "explain_valid_split_payment" in decision["resolution_actions"]


# ---------------------------------------------------------------------------
# P16 — missing required field → PolicyDecisionError
# ---------------------------------------------------------------------------


def test_p16_missing_required_field_order_status():
    """Missing 'order_status' field → PolicyDecisionError with field name."""
    facts = _base_facts()
    del facts["order_status"]
    with pytest.raises(PolicyDecisionError, match="missing_required_field: order_status"):
        apply_policy(facts)


def test_p16_missing_required_field_payment_total():
    """Missing 'payment_total_brl' field → PolicyDecisionError with field name."""
    facts = _base_facts()
    del facts["payment_total_brl"]
    with pytest.raises(PolicyDecisionError, match="missing_required_field: payment_total_brl"):
        apply_policy(facts)


def test_p16_missing_required_field_evidence_ids():
    """Missing 'evidence_ids' field → PolicyDecisionError with field name."""
    facts = _base_facts()
    del facts["evidence_ids"]
    with pytest.raises(PolicyDecisionError, match="missing_required_field: evidence_ids"):
        apply_policy(facts)


# ---------------------------------------------------------------------------
# P17 — no policy branch matched → PolicyDecisionError (no invented decision)
# ---------------------------------------------------------------------------


def test_p17_missing_delivery_variance_not_canceled():
    """delivery_variance_hours=None + delivered status → no branch → PolicyDecisionError."""
    facts = _base_facts(
        order_status="delivered",
        delivery_variance_hours=None,
        reconciled=None,
        payment_count=1,
    )
    with pytest.raises(PolicyDecisionError, match="no_policy_branch_matched"):
        apply_policy(facts)


def test_p17_on_time_but_not_reconciled():
    """On-time delivery + reconciled=False + single payment → no branch → PolicyDecisionError."""
    facts = _base_facts(
        delivery_variance_hours=-2.0,
        reconciled=False,
        payment_count=1,
    )
    with pytest.raises(PolicyDecisionError, match="no_policy_branch_matched"):
        apply_policy(facts)


# ---------------------------------------------------------------------------
# Extra: evidence_ids correctness
# ---------------------------------------------------------------------------


def test_evidence_ids_include_policy_tag():
    """apply_policy always appends policy:<root_cause_code> to evidence_ids."""
    facts = _base_facts(order_status="canceled", payment_total_brl=50.00)
    decision = apply_policy(facts)
    assert "policy:ORDER_CANCELED_AFTER_PAYMENT" in decision["evidence_ids"]


def test_evidence_ids_capped_at_20():
    """evidence_ids are capped at 20 entries (README limit)."""
    base_evidence = [f"item:order_abc123:{i}" for i in range(25)]
    facts = _base_facts(
        order_status="canceled",
        payment_total_brl=99.00,
        evidence_ids=base_evidence,
    )
    decision = apply_policy(facts)
    assert len(decision["evidence_ids"]) <= 20
    assert decision["evidence_ids"][-1] == "policy:ORDER_CANCELED_AFTER_PAYMENT"


def test_multiple_late_sellers_capped_at_3_responsible_parties():
    """Multiple late sellers → responsible_parties capped at 3 (README limit)."""
    facts = _base_facts(
        delivery_variance_hours=50.0,
        late_handoff=True,
        late_handoff_seller_ids=["s1", "s2", "s3", "s4", "s5"],
    )
    decision = apply_policy(facts)
    assert len(decision["root_cause_analysis"]["responsible_parties"]) <= 3


# ---------------------------------------------------------------------------
# Confidence — evidence completeness, anchored on the README §6 worked example
# ---------------------------------------------------------------------------


def test_confidence_matches_readme_example_for_fully_evidenced_case():
    """A case with every needed input present scores the README's 0.92."""
    facts = _base_facts(
        delivery_variance_hours=87.39,
        late_handoff=True,
        late_handoff_seller_ids=["seller_1"],
        payment_count=2,
    )
    decision = apply_policy(facts)
    assert decision["primary_issue"] == "late_delivery_seller"
    assert decision["confidence"] == 0.92


def test_confidence_drops_when_evidence_is_missing():
    """Missing items and delivery timing must not report full confidence."""
    no_items = apply_policy(_base_facts(
        order_status="unavailable",
        payment_total_brl=80.0,
        item_count=0,
        seller_count=0,
        category_count=0,
        delivery_variance_hours=None,
        reconciled=None,
    ))
    assert no_items["confidence"] == 0.75

    unreconciled = apply_policy(_base_facts(order_status="canceled", reconciled=False))
    assert unreconciled["confidence"] < 0.92
    assert 0.0 <= unreconciled["confidence"] <= 1.0
