"""Case orchestration and verifier-gated output assembly for K4."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import time
from typing import Any

from .agents.customer import investigate_customer
from .agents.delivery import investigate_delivery
from .agents.order_product import investigate_order_and_product
from .agents.payment import investigate_payment
from .contracts import (
    AgentName,
    AggregatedFacts,
    ContractError,
    CustomerFacts,
    DeliveryFacts,
    Handoff,
    OrderProductFacts,
    PaymentFacts,
    PolicyDecision,
    RunResult,
    Stage,
    StructuredError,
    Ticket,
    json_value,
)
from .policy import apply_policy
from .verifier import verify_output


def _error(code: str, stage: Stage, message: str, *, agent: str | None = None, details: dict[str, Any] | None = None) -> StructuredError:
    return StructuredError(code=code, stage=stage.value, message=message, agent=agent, details=details or {})


class _StageFailure(Exception):
    def __init__(self, code: str, stage: Stage, agent: AgentName, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.agent = agent


def _emit(trace: Any, event: dict[str, Any]) -> None:
    """Best-effort trace emission; trace ownership stays with Member 5."""

    if trace is None:
        return
    try:
        if callable(trace):
            trace(event)
        else:
            trace.write(event)
    except Exception:
        # A trace sink must never leak an exception that masks the pipeline stage
        # that was being audited.  Batch lifecycle records trace finalization.
        return


def _run_stage(
    stage: Stage,
    agent: AgentName,
    function: Callable[..., dict[str, Any]],
    *args: Any,
    case_id: str,
    durations: dict[str, float],
    trace: Any,
) -> Handoff:
    started = time.perf_counter()
    try:
        raw = function(*args)
        handoff = Handoff.from_dict(raw, expected_agent=agent.value, case_id=case_id)
        _emit(trace, {"event_type": "handoff", "case_id": case_id, "stage": stage.value, "from_agent": agent.value, "to_agent": "coordinator", "status": "ok", "evidence_ids": list(handoff.evidence_ids)})
        return handoff
    except ContractError as exc:
        _emit(trace, {"event_type": "handoff_failed", "case_id": case_id, "stage": stage.value, "from_agent": agent.value, "status": "blocked", "errors": [str(exc)]})
        raise _StageFailure("HANDOFF_CONTRACT_INVALID", stage, agent, str(exc)) from exc
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        _emit(trace, {"event_type": "agent_failed", "case_id": case_id, "stage": stage.value, "from_agent": agent.value, "status": "error", "errors": [message]})
        raise _StageFailure("AGENT_EXECUTION_FAILED", stage, agent, message) from exc
    finally:
        durations[stage.value] = round((time.perf_counter() - started) * 1000, 3)


def _stable_unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _repository_rows(repository: object, method_name: str, mapping_name: str, order_id: str) -> list[dict[str, Any]]:
    """Read a source-order list without coupling domain agents to one repo API."""

    method = getattr(repository, method_name, None)
    if callable(method):
        value = method(order_id)
        return list(value) if value is not None else []
    mapping = getattr(repository, mapping_name, None)
    if isinstance(mapping, dict):
        value = mapping.get(order_id, [])
        return list(value) if isinstance(value, list) else []
    return []


def _customer_adapter(ticket: dict[str, Any], repository: object) -> dict[str, Any]:
    """Adapt Member 2's public agent result to the shared M1 handoff contract."""

    raw = investigate_customer(ticket, repository)
    if not isinstance(raw, dict) or not raw or raw.get("next_task") == "aggregate_customer_facts":
        return raw
    order_id = ticket["customer_request"]["claimed_order_id"]
    order = repository.get_order(order_id)
    if not isinstance(order, dict):
        raise ContractError("customer adapter cannot load the claimed order")
    customer_id = order.get("customer_id")
    customer_unique_id = raw["facts"].get("customer_unique_id")
    if not isinstance(customer_id, str) or not isinstance(customer_unique_id, str):
        raise ContractError("customer adapter received incomplete customer identity")
    all_related = getattr(repository, "customer_unique_to_orders", {}).get(customer_unique_id, [])
    related_order_count = sum(order_value != order_id for order_value in all_related)
    related_order_ids = list(raw["facts"].get("related_order_ids", []))[:5]
    return {
        **raw,
        "task": "investigate_customer",
        "next_task": "aggregate_customer_facts",
        "facts": {
            "claimed_order_id": order_id,
            "customer_id": customer_id,
            "customer_unique_id": customer_unique_id,
            "related_order_ids": related_order_ids,
            "related_order_count": related_order_count,
            "has_related_orders": related_order_count > 0,
        },
    }


def _order_product_adapter(ticket: dict[str, Any], repository: object) -> dict[str, Any]:
    """Adapt Member 2 order/product facts while preserving full source counts."""

    raw = investigate_order_and_product(ticket, repository)
    if not isinstance(raw, dict) or not raw or raw.get("next_task") == "aggregate_order_product_facts":
        return raw
    order_id = ticket["customer_request"]["claimed_order_id"]
    order = repository.get_order(order_id)
    if not isinstance(order, dict):
        raise ContractError("order/product adapter cannot load the claimed order")
    raw_items = _repository_rows(repository, "get_order_items", "order_items", order_id)
    item_facts: list[dict[str, Any]] = []
    for item in raw_items:
        product_id = str(item["product_id"])
        product = repository.get_product(product_id)
        category = None
        if isinstance(product, dict):
            category = product.get("product_category_name") or None
        item_facts.append({
            "order_id": order_id,
            "order_item_id": str(item["order_item_id"]),
            "product_id": product_id,
            "seller_id": str(item["seller_id"]),
            "price_raw": item.get("price", 0),
            "freight_value_raw": item.get("freight_value", 0),
            "shipping_limit_date": item.get("shipping_limit_date") or None,
            "category_name": category,
        })
    seller_ids = list(dict.fromkeys(item["seller_id"] for item in item_facts))
    product_ids = list(dict.fromkeys(item["product_id"] for item in item_facts))
    category_names = list(dict.fromkeys(item["category_name"] for item in item_facts if item["category_name"] is not None))
    return {
        **raw,
        "task": "investigate_order_and_product",
        "next_task": "aggregate_order_product_facts",
        "facts": {
            "order_id": order_id,
            "order_status": order.get("order_status"),
            "customer_id": order.get("customer_id"),
            "item_ids": [f"{item['order_id']}:{item['order_item_id']}" for item in item_facts[:5]],
            "seller_ids": seller_ids[:3],
            "product_ids": product_ids[:5],
            "category_names": category_names[:5],
            "item_count": len(item_facts),
            "seller_count": len(seller_ids),
            "product_count": len(product_ids),
            "category_count": len(category_names),
            "items": item_facts,
        },
    }


def _payment_adapter(ticket: dict[str, Any], repository: object) -> dict[str, Any]:
    """Adapt Member 3 payment result to the central reconciliation contract."""

    raw = investigate_payment(ticket, repository)
    if not isinstance(raw, dict) or not raw or raw.get("next_task") == "aggregate_payment_facts":
        return raw
    order_id = ticket["customer_request"]["claimed_order_id"]
    payments = _repository_rows(repository, "get_order_payments", "order_payments", order_id)
    items = _repository_rows(repository, "get_order_items", "order_items", order_id)
    payment_total = round(sum(float(row.get("payment_value", 0)) for row in payments), 2)
    item_total = round(sum(float(row.get("price", 0)) for row in items), 2)
    freight_total = round(sum(float(row.get("freight_value", 0)) for row in items), 2)
    expected_total = round(item_total + freight_total, 2) if items else None
    difference = round(payment_total - expected_total, 2) if expected_total is not None else None
    payment_types = list(dict.fromkeys(str(row.get("payment_type")) for row in payments if row.get("payment_type") is not None))
    payment_ids = [f"{order_id}:{row['payment_sequential']}" for row in payments[:5]]
    return {
        **raw,
        "from_agent": AgentName.PAYMENT.value,
        "task": "investigate_payment",
        "next_task": "aggregate_payment_facts",
        "facts": {
            "order_id": order_id,
            "payment_ids": payment_ids,
            "payment_count": len(payments),
            "payment_types": payment_types,
            "item_total_brl": item_total,
            "freight_total_brl": freight_total,
            "expected_total_brl": expected_total,
            "payment_total_brl": payment_total,
            "difference_brl": difference,
            "reconciled": abs(difference) <= 0.10 if difference is not None else None,
        },
    }


def _delivery_adapter(ticket: dict[str, Any], repository: object) -> dict[str, Any]:
    """Adapt Member 3 delivery result to the shared delivery contract."""

    raw = investigate_delivery(ticket, repository)
    if not isinstance(raw, dict) or not raw or raw.get("next_task") == "aggregate_delivery_facts":
        return raw
    order_id = ticket["customer_request"]["claimed_order_id"]
    facts = dict(raw["facts"])
    facts["order_id"] = order_id
    facts["seller_handoff_analysis"] = list(facts.get("seller_handoff_analysis", []))[:3]
    facts["late_handoff_seller_ids"] = [
        row["seller_id"] for row in facts["seller_handoff_analysis"] if row.get("late_handoff") is True
    ]
    return {
        **raw,
        "from_agent": AgentName.DELIVERY.value,
        "task": "investigate_delivery",
        "next_task": "aggregate_delivery_facts",
        "facts": facts,
    }


def _policy_input(facts: AggregatedFacts) -> dict[str, Any]:
    """Project shared typed facts to Member 4's stable policy-engine input."""

    late_handoff = bool(facts.delivery.late_handoff_seller_ids)
    return {
        "order_id": facts.order_product.order_id,
        "order_status": facts.order_product.order_status,
        "payment_total_brl": float(facts.payment.payment_total_brl),
        "payment_count": facts.payment.payment_count,
        "reconciled": facts.payment.reconciled,
        "freight_total_brl": float(facts.payment.freight_total_brl),
        "late_handoff": late_handoff,
        "late_handoff_seller_ids": list(facts.delivery.late_handoff_seller_ids),
        "delivery_variance_hours": float(facts.delivery.delivery_variance_hours) if facts.delivery.delivery_variance_hours is not None else None,
        "item_count": facts.order_product.item_count,
        "seller_count": facts.order_product.seller_count,
        "category_count": facts.order_product.category_count,
        "related_order_ids": list(facts.customer.related_order_ids),
        "evidence_ids": list(facts.source_evidence_ids),
    }


def _policy_adapter(aggregated_facts: dict[str, Any]) -> dict[str, Any]:
    """Wrap Member 4's plain decision as the standard Policy Agent handoff."""

    ticket = Ticket.from_dict(aggregated_facts["ticket"])
    customer = CustomerFacts.from_dict(aggregated_facts["customer"])
    order_product = OrderProductFacts.from_dict(aggregated_facts["order_product"])
    payment = PaymentFacts.from_dict(aggregated_facts["payment"], item_count=order_product.item_count)
    delivery = DeliveryFacts.from_dict(aggregated_facts["delivery"])
    source_evidence = tuple(aggregated_facts.get("source_evidence_ids", []))
    missing = tuple(aggregated_facts.get("missing_or_conflicting_data", []))
    facts = AggregatedFacts(ticket, customer, order_product, payment, delivery, source_evidence, missing)
    decision = apply_policy(aggregated_facts)
    if isinstance(decision, dict) and decision.get("from_agent") == AgentName.POLICY.value:
        return decision
    ranked_causes = decision["root_cause_analysis"]["ranked_causes"]
    return {
        "case_id": ticket.case_id,
        "from_agent": AgentName.POLICY.value,
        "to_agent": AgentName.COORDINATOR.value,
        "task": "apply_ec_policy_v2",
        "facts": {
            "primary_issue": decision["primary_issue"],
            "secondary_issues": decision["secondary_issues"],
            "case_status": decision["case_status"],
            "confidence": decision["confidence"],
            "ranked_causes": ranked_causes,
            "responsible_parties": decision["root_cause_analysis"]["responsible_parties"],
            "recommended_refund_brl": decision["financial_resolution"]["recommended_refund_brl"],
            "resolution_actions": decision["resolution_actions"],
            "policy_evidence_id": f"policy:{ranked_causes[0]['cause_code']}",
        },
        "evidence_ids": [f"policy:{ranked_causes[0]['cause_code']}"],
        "missing_or_conflicting_data": [],
        "next_task": "assemble_and_verify_output",
    }


def _verifier_facts(facts: AggregatedFacts, repository: object) -> dict[str, Any]:
    """Give Verifier only source-derived rows so it can recompute independently.

    Do not copy customer or category projections from specialist handoffs here:
    doing so would let the verifier approve the same transformation bug that
    produced the candidate output.
    """

    order_id = facts.ticket.customer_request.claimed_order_id
    get_order = getattr(repository, "get_order", None)
    if not callable(get_order):
        # Enables coordinator contract tests that intentionally supply a bare
        # repository while mocking the verifier itself.
        return facts.to_dict()
    order = get_order(order_id)
    if not isinstance(order, dict):
        raise ContractError("verifier cannot retrieve the claimed order")
    items = _repository_rows(repository, "get_order_items", "order_items", order_id)
    customer_id = order.get("customer_id")
    get_customer = getattr(repository, "get_customer", None)
    customer = get_customer(customer_id) if callable(get_customer) and isinstance(customer_id, str) else None
    if not isinstance(customer, dict):
        raise ContractError("verifier cannot retrieve the claimed customer")
    customer_unique_id = customer.get("customer_unique_id")
    if not isinstance(customer_unique_id, str) or not customer_unique_id:
        raise ContractError("verifier cannot retrieve customer_unique_id")
    get_related_orders = getattr(repository, "get_related_orders", None)
    if not callable(get_related_orders):
        raise ContractError("verifier cannot retrieve customer order history")
    related_order_ids = get_related_orders(customer_unique_id, order_id, limit=5)
    get_product = getattr(repository, "get_product", None)
    if not callable(get_product):
        raise ContractError("verifier cannot retrieve product rows")
    products: list[dict[str, Any]] = []
    seen_product_ids: set[str] = set()
    for item in items:
        product_id = str(item.get("product_id", ""))
        if not product_id or product_id in seen_product_ids:
            continue
        seen_product_ids.add(product_id)
        product = get_product(product_id)
        if isinstance(product, dict):
            products.append(product)
    return {
        "case_id": facts.ticket.case_id,
        "order": order,
        "items": items,
        "payments": _repository_rows(repository, "get_order_payments", "order_payments", order_id),
        "customer": {
            "customer_unique_id": customer_unique_id,
            "related_order_ids": list(related_order_ids),
        },
        "products": products,
    }


def _validate_source_evidence(facts: AggregatedFacts) -> None:
    order_id = facts.ticket.customer_request.claimed_order_id
    allowed_items = {f"item:{item.order_id}:{item.order_item_id}" for item in facts.order_product.items}
    allowed_payments = set(facts.payment.payment_ids)
    allowed_sellers = set(facts.order_product.seller_ids)
    order_evidence = f"order:{order_id}"
    if order_evidence not in facts.source_evidence_ids:
        raise ContractError("order evidence is required")
    for evidence in facts.source_evidence_ids:
        if evidence == order_evidence:
            continue
        if evidence in allowed_items:
            continue
        if evidence.startswith("payment:"):
            _, row_order_id, payment_sequence = evidence.split(":", 2)
            if row_order_id == order_id and f"{row_order_id}:{payment_sequence}" in allowed_payments:
                continue
        if evidence.startswith("seller:") and evidence.removeprefix("seller:") in allowed_sellers:
            continue
        raise ContractError(f"source evidence is not consistent with aggregated facts: {evidence}")


def _aggregate(ticket: Ticket, handoffs: Mapping[str, Handoff]) -> AggregatedFacts:
    customer = CustomerFacts.from_dict(handoffs[AgentName.CUSTOMER.value].facts)
    order_product = OrderProductFacts.from_dict(handoffs[AgentName.ORDER_PRODUCT.value].facts)
    payment = PaymentFacts.from_dict(handoffs[AgentName.PAYMENT.value].facts, item_count=order_product.item_count)
    delivery = DeliveryFacts.from_dict(handoffs[AgentName.DELIVERY.value].facts)
    claimed_order_id = ticket.customer_request.claimed_order_id
    if customer.claimed_order_id != claimed_order_id:
        raise ContractError("customer claimed_order_id conflicts with ticket")
    if order_product.order_id != claimed_order_id or payment.order_id != claimed_order_id or delivery.order_id != claimed_order_id:
        raise ContractError("a domain fact order_id conflicts with ticket")
    if customer.customer_id != order_product.customer_id:
        raise ContractError("customer_id conflicts between customer and order/product facts")
    order_sellers = set(order_product.seller_ids)
    if any(item.seller_id not in order_sellers for item in delivery.seller_handoff_analysis):
        raise ContractError("delivery facts reference a seller outside order/product facts")
    source_evidence = _stable_unique(
        list(handoffs[AgentName.CUSTOMER.value].evidence_ids)
        + list(handoffs[AgentName.ORDER_PRODUCT.value].evidence_ids)
        + list(handoffs[AgentName.PAYMENT.value].evidence_ids)
        + list(handoffs[AgentName.DELIVERY.value].evidence_ids)
    )
    missing = _stable_unique(
        list(handoffs[AgentName.CUSTOMER.value].missing_or_conflicting_data)
        + list(handoffs[AgentName.ORDER_PRODUCT.value].missing_or_conflicting_data)
        + list(handoffs[AgentName.PAYMENT.value].missing_or_conflicting_data)
        + list(handoffs[AgentName.DELIVERY.value].missing_or_conflicting_data)
    )
    facts = AggregatedFacts(ticket, customer, order_product, payment, delivery, source_evidence, missing)
    _validate_source_evidence(facts)
    return facts


def _output_evidence(facts: AggregatedFacts, decision: PolicyDecision) -> list[str]:
    order_id = facts.ticket.customer_request.claimed_order_id
    source = set(facts.source_evidence_ids)
    result = [f"order:{order_id}"]
    result.extend(f"item:{item.order_id}:{item.order_item_id}" for item in facts.order_product.items[:5] if f"item:{item.order_id}:{item.order_item_id}" in source)
    result.extend(f"payment:{payment_id}" for payment_id in facts.payment.payment_ids[:5] if f"payment:{payment_id}" in source)
    responsible_sellers = [party.get("party_id") for party in decision.responsible_parties if party.get("party_type") == "seller"]
    result.extend(f"seller:{seller_id}" for seller_id in responsible_sellers[:3] if isinstance(seller_id, str) and f"seller:{seller_id}" in source)
    result.append(decision.policy_evidence_id)
    deduped = list(dict.fromkeys(result))
    if len(deduped) > 20:
        raise ContractError("assembled evidence exceeds the K4 limit")
    return deduped


def assemble_output(ticket: Ticket, facts: AggregatedFacts, decision: PolicyDecision) -> dict[str, Any]:
    """Map validated facts and a policy decision to the published README schema."""

    delivery = facts.delivery
    payment = facts.payment
    return json_value(
        {
            "case_id": ticket.case_id,
            "case_assessment": {
                "primary_issue": decision.primary_issue,
                "secondary_issues": list(decision.secondary_issues),
                "case_status": decision.case_status,
                "confidence": decision.confidence,
            },
            "affected_entities": {
                "order_ids": [ticket.customer_request.claimed_order_id],
                "item_ids": list(facts.order_product.item_ids),
                "seller_ids": list(facts.order_product.seller_ids),
                "payment_ids": list(payment.payment_ids),
            },
            "customer_context": {
                "customer_unique_id": facts.customer.customer_unique_id,
                "related_order_ids": list(facts.customer.related_order_ids),
            },
            "product_context": {
                "product_ids": list(facts.order_product.product_ids),
                "category_names": list(facts.order_product.category_names),
            },
            "delivery_analysis": {
                "delivered_at": delivery.delivered_at,
                "estimated_delivery_at": delivery.estimated_delivery_at,
                "carrier_handoff_at": delivery.carrier_handoff_at,
                "delivery_variance_hours": delivery.delivery_variance_hours,
                "seller_handoff_analysis": list(delivery.seller_handoff_analysis),
                "late_handoff_seller_ids": list(delivery.late_handoff_seller_ids),
            },
            "payment_reconciliation": {
                "currency": "BRL",
                "item_total_brl": payment.item_total_brl,
                "freight_total_brl": payment.freight_total_brl,
                "expected_total_brl": payment.expected_total_brl,
                "payment_total_brl": payment.payment_total_brl,
                "difference_brl": payment.difference_brl,
                "reconciled": payment.reconciled,
                "payment_types": list(payment.payment_types),
            },
            "root_cause_analysis": {
                "ranked_causes": list(decision.ranked_causes),
                "responsible_parties": list(decision.responsible_parties),
            },
            "evidence_ids": _output_evidence(facts, decision),
            "financial_resolution": {"currency": "BRL", "recommended_refund_brl": decision.recommended_refund_brl},
            "resolution_actions": list(decision.resolution_actions),
        }
    )


def process_case(ticket: Ticket | Mapping[str, Any], repository: object, trace: Any = None) -> RunResult:
    """Run all K4 agents and return a publishable output only after verifier pass."""

    durations: dict[str, float] = {}
    try:
        parsed_ticket = ticket if isinstance(ticket, Ticket) else Ticket.from_dict(ticket)
    except ContractError as exc:
        return RunResult.blocked("unknown", [_error("INPUT_SCHEMA_INVALID", Stage.INPUT_VALIDATION, str(exc))], durations)
    case_id = parsed_ticket.case_id
    _emit(trace, {"event_type": "case_started", "case_id": case_id, "stage": "coordinator", "status": "started"})
    try:
        ticket_dict = parsed_ticket.to_dict()
        handoffs = {
            AgentName.CUSTOMER.value: _run_stage(Stage.CUSTOMER, AgentName.CUSTOMER, _customer_adapter, ticket_dict, repository, case_id=case_id, durations=durations, trace=trace),
            AgentName.ORDER_PRODUCT.value: _run_stage(Stage.ORDER_PRODUCT, AgentName.ORDER_PRODUCT, _order_product_adapter, ticket_dict, repository, case_id=case_id, durations=durations, trace=trace),
            AgentName.PAYMENT.value: _run_stage(Stage.PAYMENT, AgentName.PAYMENT, _payment_adapter, ticket_dict, repository, case_id=case_id, durations=durations, trace=trace),
            AgentName.DELIVERY.value: _run_stage(Stage.DELIVERY, AgentName.DELIVERY, _delivery_adapter, ticket_dict, repository, case_id=case_id, durations=durations, trace=trace),
        }
        facts = _aggregate(parsed_ticket, handoffs)
    except _StageFailure as exc:
        return RunResult.blocked(case_id, [_error(exc.code, exc.stage, str(exc), agent=exc.agent.value)], durations)
    except ContractError as exc:
        return RunResult.blocked(case_id, [_error("DOMAIN_FACT_CONTRACT_INVALID", Stage.ASSEMBLY, str(exc))], durations)
    except Exception as exc:
        return RunResult.blocked(case_id, [_error("AGENT_EXECUTION_FAILED", Stage.ASSEMBLY, str(exc))], durations)
    try:
        policy_handoff = _run_stage(Stage.POLICY, AgentName.POLICY, _policy_adapter, facts.to_dict(), case_id=case_id, durations=durations, trace=trace)
        decision = PolicyDecision.from_dict(policy_handoff.facts)
        if decision.policy_evidence_id not in policy_handoff.evidence_ids:
            raise ContractError("policy handoff must contain its decision evidence")
    except _StageFailure as exc:
        return RunResult.blocked(case_id, [_error("POLICY_CONTRACT_INVALID" if exc.code == "HANDOFF_CONTRACT_INVALID" else "POLICY_EXECUTION_FAILED", exc.stage, str(exc), agent=exc.agent.value)], durations)
    except ContractError as exc:
        return RunResult.blocked(case_id, [_error("POLICY_CONTRACT_INVALID", Stage.POLICY, str(exc), agent=AgentName.POLICY.value)], durations)
    except Exception as exc:
        return RunResult.blocked(case_id, [_error("POLICY_EXECUTION_FAILED", Stage.POLICY, str(exc), agent=AgentName.POLICY.value)], durations)
    try:
        started = time.perf_counter()
        output = assemble_output(parsed_ticket, facts, decision)
        durations[Stage.ASSEMBLY.value] = round((time.perf_counter() - started) * 1000, 3)
    except Exception as exc:
        return RunResult.blocked(case_id, [_error("OUTPUT_ASSEMBLY_FAILED", Stage.ASSEMBLY, f"{type(exc).__name__}: {exc}")], durations)
    try:
        started = time.perf_counter()
        verifier_errors = verify_output(output, _verifier_facts(facts, repository), repository)
        durations[Stage.VERIFICATION.value] = round((time.perf_counter() - started) * 1000, 3)
        if not isinstance(verifier_errors, list):
            raise ContractError("verifier must return a list of errors")
        if verifier_errors:
            _emit(trace, {"event_type": "verification_failed", "case_id": case_id, "stage": "verification", "status": "blocked", "errors": list(verifier_errors)})
            return RunResult.blocked(case_id, [_error("VERIFICATION_FAILED", Stage.VERIFICATION, str(message), agent=AgentName.VERIFIER.value) for message in verifier_errors], durations)
    except ContractError as exc:
        return RunResult.blocked(case_id, [_error("VERIFICATION_FAILED", Stage.VERIFICATION, str(exc), agent=AgentName.VERIFIER.value)], durations)
    except Exception as exc:
        return RunResult.blocked(case_id, [_error("VERIFICATION_FAILED", Stage.VERIFICATION, f"{type(exc).__name__}: {exc}", agent=AgentName.VERIFIER.value)], durations)
    _emit(trace, {"event_type": "verification_passed", "case_id": case_id, "stage": "verification", "status": "verified"})
    _emit(trace, {"event_type": "case_finished", "case_id": case_id, "stage": "verification", "status": "verified"})
    return RunResult.verified(case_id, output, durations)
