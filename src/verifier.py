"""Independent EC_POLICY_V2 output verifier. Owned by Member 5."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


MAX_LENGTHS = {
    "order_ids": 5,
    "item_ids": 5,
    "seller_ids": 3,
    "payment_ids": 5,
    "related_order_ids": 5,
    "product_ids": 5,
    "category_names": 5,
    "ranked_causes": 3,
    "responsible_parties": 3,
    "evidence_ids": 20,
    "resolution_actions": 5,
}
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
ROOT_CAUSES = {
    "canceled_order_paid": "ORDER_CANCELED_AFTER_PAYMENT",
    "unavailable_order_paid": "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "late_delivery_seller": "SELLER_HANDOFF_AFTER_LIMIT",
    "late_delivery_logistics": "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "valid_split_payment": "MULTIPLE_PAYMENTS_RECONCILED",
    "unsupported_late_claim": "DELIVERY_WITHIN_ESTIMATE",
}
PRIMARY_ACTIONS = {
    "canceled_order_paid": "issue_full_refund",
    "unavailable_order_paid": "issue_full_refund",
    "late_delivery_seller": "refund_freight",
    "late_delivery_logistics": "refund_freight",
    "valid_split_payment": "explain_valid_split_payment",
    "unsupported_late_claim": "reject_late_refund",
}


def verify_output(output: dict[str, Any], facts: dict[str, Any], repository: object | None) -> list[str]:
    """Return error codes; an empty list means the candidate is safe to write."""

    errors: list[str] = []
    _validate_schema(output, errors)
    if errors:
        return errors

    expected = _derive_expected(facts, errors)
    _validate_references(output, facts, repository, errors)
    if expected is None:
        return errors

    _validate_limits(output, errors)
    _validate_timestamp_fields(output, errors)
    _validate_candidate_against_expected(output, expected, errors)
    return errors


def _validate_schema(output: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(output, dict):
        errors.append("schema.output_not_object")
        return
    required_sections = {
        "case_id": str,
        "case_assessment": dict,
        "affected_entities": dict,
        "customer_context": dict,
        "product_context": dict,
        "delivery_analysis": dict,
        "payment_reconciliation": dict,
        "root_cause_analysis": dict,
        "evidence_ids": list,
        "financial_resolution": dict,
        "resolution_actions": list,
    }
    for field, expected_type in required_sections.items():
        if field not in output:
            errors.append(f"schema.missing_{field}")
        elif not isinstance(output[field], expected_type):
            errors.append(f"schema.invalid_{field}_type")
    if errors:
        return

    assessment = output["case_assessment"]
    for field in ("primary_issue", "secondary_issues", "case_status", "confidence"):
        if field not in assessment:
            errors.append(f"schema.missing_case_assessment.{field}")
    if assessment.get("case_status") not in {"action_required", "no_action"}:
        errors.append("schema.invalid_case_status")
    confidence = assessment.get("confidence")
    if not _is_number(confidence) or not 0 <= float(confidence) <= 1:
        errors.append("schema.invalid_confidence")
    if not isinstance(assessment.get("secondary_issues"), list):
        errors.append("schema.invalid_secondary_issues")

    sections = {
        "affected_entities": ("order_ids", "item_ids", "seller_ids", "payment_ids"),
        "customer_context": ("customer_unique_id", "related_order_ids"),
        "product_context": ("product_ids", "category_names"),
        "delivery_analysis": (
            "delivered_at", "estimated_delivery_at", "carrier_handoff_at",
            "delivery_variance_hours", "seller_handoff_analysis", "late_handoff_seller_ids",
        ),
        "payment_reconciliation": (
            "currency", "item_total_brl", "freight_total_brl", "expected_total_brl",
            "payment_total_brl", "difference_brl", "reconciled", "payment_types",
        ),
        "root_cause_analysis": ("ranked_causes", "responsible_parties"),
        "financial_resolution": ("currency", "recommended_refund_brl"),
    }
    for section, fields in sections.items():
        for field in fields:
            if field not in output[section]:
                errors.append(f"schema.missing_{section}.{field}")


def _derive_expected(facts: dict[str, Any], errors: list[str]) -> dict[str, Any] | None:
    order = facts.get("order")
    items = facts.get("items")
    payments = facts.get("payments")
    if not isinstance(order, dict):
        errors.append("reference.missing_order_facts")
        return None
    if not isinstance(items, list) or not isinstance(payments, list):
        errors.append("reference.missing_item_or_payment_facts")
        return None
    order_id = _value(order, "order_id")
    if not order_id:
        errors.append("reference.missing_order_id")
        return None

    item_total = _sum_decimal(items, "price", errors)
    freight_total = _sum_decimal(items, "freight_value", errors)
    payment_total = _sum_decimal(payments, "payment_value", errors)
    has_items = bool(items)
    expected_total = _round_money(item_total + freight_total) if has_items else None
    difference = _round_money(payment_total - expected_total) if has_items else None
    reconciled = abs(difference) <= Decimal("0.10") if difference is not None else None

    seller_ids = _unique([_value(item, "seller_id") for item in items])[:MAX_LENGTHS["seller_ids"]]
    item_ids = [f"{order_id}:{_value(item, 'order_item_id')}" for item in items][:MAX_LENGTHS["item_ids"]]
    payment_ids = [f"{order_id}:{_value(payment, 'payment_sequential')}" for payment in payments][:MAX_LENGTHS["payment_ids"]]
    product_ids = _unique([_value(item, "product_id") for item in items])[:MAX_LENGTHS["product_ids"]]
    categories = _categories(facts, items)[:MAX_LENGTHS["category_names"]]
    related_order_ids = _related_orders(facts, order_id)[:MAX_LENGTHS["related_order_ids"]]
    payment_types = _unique([_value(payment, "payment_type") for payment in payments])

    delivery = _delivery_expected(order, items, seller_ids, errors)
    secondary_issues = _secondary_issues(items, seller_ids, payments, related_order_ids, categories)
    primary_issue = _primary_issue(order, payment_total, delivery, payments, reconciled)
    if primary_issue is None:
        errors.append("policy.no_matching_primary_issue")
        return None

    root_cause = ROOT_CAUSES[primary_issue]
    responsible_parties = _responsible_parties(primary_issue, delivery["late_handoff_seller_ids"])
    refund = _refund(primary_issue, payment_total, freight_total)
    actions = _actions(primary_issue, secondary_issues)
    evidence_ids = _evidence_ids(order_id, item_ids, payment_ids, responsible_parties, root_cause)

    return {
        "case_id": facts.get("case_id"),
        "order_id": order_id,
        "item_ids": item_ids,
        "seller_ids": seller_ids,
        "payment_ids": payment_ids,
        "customer_unique_id": _customer_unique_id(facts),
        "related_order_ids": related_order_ids,
        "product_ids": product_ids,
        "categories": categories,
        "delivery": delivery,
        "payment": {
            "item_total_brl": _round_money(item_total),
            "freight_total_brl": _round_money(freight_total),
            "expected_total_brl": expected_total,
            "payment_total_brl": _round_money(payment_total),
            "difference_brl": difference,
            "reconciled": reconciled,
            "payment_types": payment_types,
        },
        "primary_issue": primary_issue,
        "secondary_issues": secondary_issues,
        "root_cause": root_cause,
        "responsible_parties": responsible_parties,
        "refund": refund,
        "actions": actions,
        "evidence_ids": evidence_ids,
    }


def _validate_references(output: dict[str, Any], facts: dict[str, Any], repository: object | None, errors: list[str]) -> None:
    if not isinstance(output, dict) or "affected_entities" not in output:
        return
    entities = output["affected_entities"]
    checks = (
        ("order", entities.get("order_ids", [])),
        ("seller", entities.get("seller_ids", [])),
    )
    for kind, identifiers in checks:
        for identifier in identifiers:
            if not _repository_has(repository, kind, identifier):
                errors.append(f"reference.unknown_{kind}:{identifier}")
    for item_id in entities.get("item_ids", []):
        parts = str(item_id).rsplit(":", 1)
        if len(parts) != 2 or not _repository_has(repository, "item", parts[0], parts[1]):
            errors.append(f"reference.unknown_item:{item_id}")
    for payment_id in entities.get("payment_ids", []):
        parts = str(payment_id).rsplit(":", 1)
        if len(parts) != 2 or not _repository_has(repository, "payment", parts[0], parts[1]):
            errors.append(f"reference.unknown_payment:{payment_id}")

    for evidence_id in output.get("evidence_ids", []):
        if not _valid_evidence_format(evidence_id):
            errors.append(f"reference.invalid_evidence_format:{evidence_id}")


def _repository_has(repository: object | None, kind: str, *identifier: str) -> bool:
    if repository is None:
        return False
    predicate = getattr(repository, f"has_{kind}", None)
    if not callable(predicate):
        return False
    return bool(predicate(*identifier))


def _validate_limits(output: dict[str, Any], errors: list[str]) -> None:
    entities = output["affected_entities"]
    groups = {
        "order_ids": entities["order_ids"],
        "item_ids": entities["item_ids"],
        "seller_ids": entities["seller_ids"],
        "payment_ids": entities["payment_ids"],
        "related_order_ids": output["customer_context"]["related_order_ids"],
        "product_ids": output["product_context"]["product_ids"],
        "category_names": output["product_context"]["category_names"],
        "ranked_causes": output["root_cause_analysis"]["ranked_causes"],
        "responsible_parties": output["root_cause_analysis"]["responsible_parties"],
        "evidence_ids": output["evidence_ids"],
        "resolution_actions": output["resolution_actions"],
    }
    for field, values in groups.items():
        if not isinstance(values, list):
            errors.append(f"schema.invalid_{field}_type")
        elif len(values) > MAX_LENGTHS[field]:
            errors.append(f"limit.{field}")


def _validate_timestamp_fields(output: dict[str, Any], errors: list[str]) -> None:
    delivery = output["delivery_analysis"]
    for field in ("delivered_at", "estimated_delivery_at", "carrier_handoff_at"):
        value = delivery[field]
        if value is not None and not _parse_timestamp(value):
            errors.append(f"schema.invalid_timestamp:{field}")


def _validate_candidate_against_expected(output: dict[str, Any], expected: dict[str, Any], errors: list[str]) -> None:
    if output["case_id"] != expected["case_id"]:
        errors.append("consistency.case_id")
    entities = output["affected_entities"]
    _expect_equal(entities["order_ids"], [expected["order_id"]], "consistency.order_ids", errors)
    _expect_equal(entities["item_ids"], expected["item_ids"], "consistency.item_ids", errors)
    _expect_equal(entities["seller_ids"], expected["seller_ids"], "consistency.seller_ids", errors)
    _expect_equal(entities["payment_ids"], expected["payment_ids"], "consistency.payment_ids", errors)
    _expect_equal(output["customer_context"]["customer_unique_id"], expected["customer_unique_id"], "consistency.customer_unique_id", errors)
    _expect_equal(output["customer_context"]["related_order_ids"], expected["related_order_ids"], "consistency.related_order_ids", errors)
    _expect_equal(output["product_context"]["product_ids"], expected["product_ids"], "consistency.product_ids", errors)
    _expect_equal(output["product_context"]["category_names"], expected["categories"], "consistency.category_names", errors)

    assessment = output["case_assessment"]
    _expect_equal(assessment["primary_issue"], expected["primary_issue"], "policy.primary_issue", errors)
    _expect_equal(assessment["secondary_issues"], expected["secondary_issues"], "policy.secondary_issues", errors)
    expected_status = "action_required" if expected["refund"] > Decimal("0") else "no_action"
    _expect_equal(assessment["case_status"], expected_status, "policy.case_status", errors)

    delivery = output["delivery_analysis"]
    for field in ("delivered_at", "estimated_delivery_at", "carrier_handoff_at", "seller_handoff_analysis", "late_handoff_seller_ids"):
        _expect_equal(delivery[field], expected["delivery"][field], f"calculation.delivery_{field}", errors)
    _expect_money(delivery["delivery_variance_hours"], expected["delivery"]["delivery_variance_hours"], "calculation.delivery_variance_hours", errors)

    payment = output["payment_reconciliation"]
    _expect_equal(payment["currency"], "BRL", "consistency.payment_currency", errors)
    for field in ("item_total_brl", "freight_total_brl", "expected_total_brl", "payment_total_brl", "difference_brl"):
        _expect_money(payment[field], expected["payment"][field], f"calculation.{field}", errors)
    _expect_equal(payment["reconciled"], expected["payment"]["reconciled"], "calculation.reconciled", errors)
    _expect_equal(payment["payment_types"], expected["payment"]["payment_types"], "consistency.payment_types", errors)

    root_cause = output["root_cause_analysis"]
    _expect_equal(root_cause["ranked_causes"], [{"cause_code": expected["root_cause"], "rank": 1}], "policy.ranked_causes", errors)
    _expect_equal(root_cause["responsible_parties"], expected["responsible_parties"], "policy.responsible_parties", errors)
    _expect_equal(output["evidence_ids"], expected["evidence_ids"], "reference.evidence_ids", errors)
    _expect_equal(output["financial_resolution"]["currency"], "BRL", "consistency.refund_currency", errors)
    _expect_money(output["financial_resolution"]["recommended_refund_brl"], expected["refund"], "policy.recommended_refund_brl", errors)
    _expect_equal(output["resolution_actions"], expected["actions"], "policy.resolution_actions", errors)


def _delivery_expected(order: dict[str, Any], items: list[dict[str, Any]], seller_ids: list[str], errors: list[str]) -> dict[str, Any]:
    delivered = _value(order, "order_delivered_customer_date")
    estimated = _value(order, "order_estimated_delivery_date")
    carrier = _value(order, "order_delivered_carrier_date")
    variance = _hours_between(delivered, estimated, errors, "delivery_variance")
    seller_analysis: list[dict[str, Any]] = []
    late_sellers: list[str] = []
    for seller_id in seller_ids:
        seller_items = [item for item in items if _value(item, "seller_id") == seller_id]
        limits = [_value(item, "shipping_limit_date") for item in seller_items if _value(item, "shipping_limit_date")]
        shipping_limit = min(limits) if limits else None
        handoff_variance = _hours_between(carrier, shipping_limit, errors, f"handoff_variance:{seller_id}")
        late_handoff = handoff_variance is not None and handoff_variance > Decimal("0")
        if late_handoff:
            late_sellers.append(seller_id)
        seller_analysis.append({
            "seller_id": seller_id,
            "shipping_limit_at": shipping_limit,
            "handoff_variance_hours": _number_or_none(handoff_variance),
            "late_handoff": late_handoff,
        })
    return {
        "delivered_at": delivered,
        "estimated_delivery_at": estimated,
        "carrier_handoff_at": carrier,
        "delivery_variance_hours": _number_or_none(variance),
        "seller_handoff_analysis": seller_analysis,
        "late_handoff_seller_ids": late_sellers,
    }


def _primary_issue(order: dict[str, Any], payment_total: Decimal, delivery: dict[str, Any], payments: list[dict[str, Any]], reconciled: bool | None) -> str | None:
    status = _value(order, "order_status")
    if status == "canceled" and payment_total > 0:
        return "canceled_order_paid"
    if status == "unavailable" and payment_total > 0:
        return "unavailable_order_paid"
    variance = delivery["delivery_variance_hours"]
    if variance is not None and Decimal(str(variance)) > 0:
        return "late_delivery_seller" if delivery["late_handoff_seller_ids"] else "late_delivery_logistics"
    if len(payments) >= 2 and reconciled is True:
        return "valid_split_payment"
    if variance is not None and Decimal(str(variance)) <= 0 and reconciled is True:
        return "unsupported_late_claim"
    return None


def _secondary_issues(items: list[dict[str, Any]], seller_ids: list[str], payments: list[dict[str, Any]], related_order_ids: list[str], categories: list[str]) -> list[str]:
    issues: list[str] = []
    if len(items) >= 2:
        issues.append("multi_item_order")
    if len(seller_ids) >= 2:
        issues.append("multi_seller_order")
    if len(payments) >= 2:
        issues.append("split_payment")
    if related_order_ids:
        issues.append("repeat_customer")
    if len(categories) >= 2:
        issues.append("multiple_categories")
    return issues


def _responsible_parties(primary_issue: str, late_sellers: list[str]) -> list[dict[str, str]]:
    if primary_issue == "late_delivery_seller":
        return [{"party_type": "seller", "party_id": seller_id} for seller_id in late_sellers[:3]]
    if primary_issue == "late_delivery_logistics":
        return [{"party_type": "logistics_provider", "party_id": "LOGISTICS_PROVIDER"}]
    if primary_issue in {"canceled_order_paid", "unavailable_order_paid"}:
        return [{"party_type": "platform", "party_id": "OLIST_PLATFORM"}]
    return []


def _refund(primary_issue: str, payment_total: Decimal, freight_total: Decimal) -> Decimal:
    if primary_issue in {"canceled_order_paid", "unavailable_order_paid"}:
        return _round_money(payment_total)
    if primary_issue in {"late_delivery_seller", "late_delivery_logistics"}:
        return _round_money(freight_total)
    return Decimal("0.00")


def _actions(primary_issue: str, secondary_issues: list[str]) -> list[str]:
    actions = [PRIMARY_ACTIONS[primary_issue]]
    if primary_issue == "late_delivery_seller":
        actions.append("review_seller_handoff")
    elif primary_issue == "late_delivery_logistics":
        actions.append("review_carrier_delay")
    if primary_issue in {"canceled_order_paid", "unavailable_order_paid", "late_delivery_seller", "late_delivery_logistics"}:
        actions.append("verify_refund_completion")
    if "multi_seller_order" in secondary_issues:
        actions.append("coordinate_multi_seller_case")
    if "split_payment" in secondary_issues and primary_issue != "valid_split_payment":
        actions.append("verify_payment_allocation")
    return actions


def _evidence_ids(order_id: str, item_ids: list[str], payment_ids: list[str], parties: list[dict[str, str]], root_cause: str) -> list[str]:
    evidence = [f"order:{order_id}"]
    evidence.extend(f"item:{item_id}" for item_id in item_ids)
    evidence.extend(f"payment:{payment_id}" for payment_id in payment_ids)
    evidence.extend(f"seller:{party['party_id']}" for party in parties if party["party_type"] == "seller")
    evidence.append(f"policy:{root_cause}")
    return evidence[:MAX_LENGTHS["evidence_ids"]]


def _categories(facts: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    values = facts.get("category_names")
    if isinstance(values, list):
        return _unique(values)
    products = {str(_value(product, "product_id")): _value(product, "product_category_name") for product in facts.get("products", []) if isinstance(product, dict)}
    return _unique([products.get(_value(item, "product_id")) or _value(item, "product_category_name") for item in items])


def _related_orders(facts: dict[str, Any], order_id: str) -> list[str]:
    customer = facts.get("customer", {})
    values = customer.get("related_order_ids", []) if isinstance(customer, dict) else facts.get("related_order_ids", [])
    return [value for value in _unique(values) if value != order_id]


def _customer_unique_id(facts: dict[str, Any]) -> str | None:
    customer = facts.get("customer", {})
    value = customer.get("customer_unique_id") if isinstance(customer, dict) else facts.get("customer_unique_id")
    return _value({"value": value}, "value")


def _sum_decimal(rows: Iterable[dict[str, Any]], field: str, errors: list[str]) -> Decimal:
    total = Decimal("0")
    for row in rows:
        try:
            total += Decimal(str(_value(row, field) or "0"))
        except (InvalidOperation, ValueError):
            errors.append(f"calculation.invalid_{field}")
    return total


def _hours_between(later: str | None, earlier: str | None, errors: list[str], label: str) -> Decimal | None:
    if later is None or earlier is None:
        return None
    later_time = _parse_timestamp(later)
    earlier_time = _parse_timestamp(earlier)
    if later_time is None or earlier_time is None:
        errors.append(f"calculation.invalid_timestamp:{label}")
        return None
    hours = Decimal(str((later_time - earlier_time).total_seconds())) / Decimal("3600")
    return _round_money(hours)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, TIMESTAMP_FORMAT)
    except ValueError:
        return None


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _number_or_none(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _value(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    return str(value)


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value is None or value == "":
            continue
        value = str(value)
        if value not in result:
            result.append(value)
    return result


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _expect_equal(actual: Any, expected: Any, error: str, errors: list[str]) -> None:
    if actual != expected:
        errors.append(error)


def _expect_money(actual: Any, expected: Decimal | float | int | None, error: str, errors: list[str]) -> None:
    if expected is None:
        if actual is not None:
            errors.append(error)
        return
    try:
        expected_value = _round_money(Decimal(str(expected)))
        if not _is_number(actual) or _round_money(Decimal(str(actual))) != expected_value:
            errors.append(error)
    except (InvalidOperation, ValueError):
        errors.append(error)


def _valid_evidence_format(evidence_id: Any) -> bool:
    if not isinstance(evidence_id, str):
        return False
    prefix, separator, suffix = evidence_id.partition(":")
    if not separator or not suffix:
        return False
    if prefix in {"order", "seller", "policy"}:
        return ":" not in suffix
    if prefix in {"item", "payment"}:
        return suffix.count(":") == 1
    return False
