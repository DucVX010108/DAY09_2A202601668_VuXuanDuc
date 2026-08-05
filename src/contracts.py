"""Strict, JSON-serializable contracts shared by the K4 pipeline.

The module deliberately owns all cross-agent validation.  Domain agents may
use ordinary dictionaries internally, but every boundary is parsed here before
the coordinator trusts it.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
import re
from typing import Any, ClassVar, Mapping, Sequence


POLICY_VERSION = "EC_POLICY_V2"
MONEY_QUANTUM = Decimal("0.01")
RECONCILIATION_TOLERANCE = Decimal("0.10")
CASE_ID_RE = re.compile(r"^EC_[0-9]{3}$")

PRIMARY_ISSUES = (
    "canceled_order_paid", "unavailable_order_paid", "late_delivery_seller",
    "late_delivery_logistics", "valid_split_payment", "unsupported_late_claim",
)
SECONDARY_ISSUES = (
    "multi_item_order", "multi_seller_order", "split_payment", "repeat_customer",
    "multiple_categories",
)
ROOT_CAUSES = (
    "ORDER_CANCELED_AFTER_PAYMENT", "ORDER_UNAVAILABLE_AFTER_PAYMENT",
    "SELLER_HANDOFF_AFTER_LIMIT", "CARRIER_DELIVERED_AFTER_ESTIMATE",
    "MULTIPLE_PAYMENTS_RECONCILED", "DELIVERY_WITHIN_ESTIMATE",
)
RESOLUTION_ACTIONS = (
    "issue_full_refund", "refund_freight", "explain_valid_split_payment",
    "reject_late_refund", "review_seller_handoff", "review_carrier_delay",
    "verify_refund_completion", "coordinate_multi_seller_case", "verify_payment_allocation",
)

EXPECTED_CASE_IDS = tuple(f"EC_{index:03d}" for index in range(1, 51))


class AgentName(str, Enum):
    COORDINATOR = "coordinator"
    CUSTOMER = "customer"
    ORDER_PRODUCT = "order_product"
    PAYMENT = "payment"
    DELIVERY = "delivery"
    POLICY = "policy"
    VERIFIER = "verifier"


class RunStatus(str, Enum):
    VERIFIED = "verified"
    BLOCKED = "blocked"


class Stage(str, Enum):
    INPUT_VALIDATION = "input_validation"
    CUSTOMER = "customer"
    ORDER_PRODUCT = "order_product"
    PAYMENT = "payment"
    DELIVERY = "delivery"
    POLICY = "policy"
    ASSEMBLY = "assembly"
    VERIFICATION = "verification"
    OUTPUT_STAGING = "output_staging"
    OUTPUT_PUBLISH = "output_publish"
    TRACE_FINALIZE = "trace_finalize"


class ContractError(ValueError):
    """Raised when data crosses a contract boundary in an invalid shape."""


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{name} must be an object")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing:
        raise ContractError(f"{name} missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ContractError(f"{name} has unknown fields: {', '.join(sorted(unknown))}")


def _require_string(value: Any, name: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{name} must be a string")
    if nonempty and not value.strip():
        raise ContractError(f"{name} must not be empty")
    return value


def _require_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ContractError(f"{name} must be a boolean")
    return value


def _require_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int:
        raise ContractError(f"{name} must be an integer")
    if value < minimum:
        raise ContractError(f"{name} must be at least {minimum}")
    return value


def _require_string_list(value: Any, name: str, *, limit: int | None = None) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContractError(f"{name} must be an array")
    if limit is not None and len(value) > limit:
        raise ContractError(f"{name} must contain at most {limit} values")
    return tuple(_require_string(item, f"{name}[{index}]") for index, item in enumerate(value))


def _decimal(value: Any, name: str, *, nullable: bool = False) -> Decimal | None:
    if value is None:
        if nullable:
            return None
        raise ContractError(f"{name} must not be null")
    if isinstance(value, bool):
        raise ContractError(f"{name} must be a decimal-compatible value")
    try:
        return Decimal(str(value))
    except Exception as exc:  # Decimal exposes several implementation exceptions.
        raise ContractError(f"{name} must be a decimal-compatible value") from exc


def money(value: Decimal) -> Decimal:
    """Return a monetary value rounded once at the JSON boundary."""

    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(money(value))
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ContractError(f"value of type {type(value).__name__} is not JSON serializable")


def json_value(value: Any) -> Any:
    """Return a fresh JSON-compatible object with deterministic money handling."""

    return _json_value(value)


def dumps_json(value: Any) -> str:
    """Encode JSON deterministically for logs and output staging."""

    return json.dumps(
        json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"


@dataclass(frozen=True)
class CustomerRequest:
    language: str
    message: str
    claimed_order_id: str

    @classmethod
    def from_dict(cls, value: Any) -> "CustomerRequest":
        data = _require_mapping(value, "customer_request")
        _require_exact_keys(data, {"language", "message", "claimed_order_id"}, "customer_request")
        language = _require_string(data["language"], "customer_request.language")
        if language != "vi":
            raise ContractError("customer_request.language must be 'vi'")
        return cls(
            language=language,
            message=_require_string(data["message"], "customer_request.message"),
            claimed_order_id=_require_string(data["claimed_order_id"], "customer_request.claimed_order_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"language": self.language, "message": self.message, "claimed_order_id": self.claimed_order_id}


@dataclass(frozen=True)
class InvestigationScope:
    include_customer_history: bool
    include_product_context: bool

    @classmethod
    def from_dict(cls, value: Any) -> "InvestigationScope":
        data = _require_mapping(value, "investigation_scope")
        _require_exact_keys(
            data,
            {"include_customer_history", "include_product_context"},
            "investigation_scope",
        )
        customer_history = _require_bool(data["include_customer_history"], "investigation_scope.include_customer_history")
        product_context = _require_bool(data["include_product_context"], "investigation_scope.include_product_context")
        if not customer_history or not product_context:
            raise ContractError("both K4 investigation scopes must be true")
        return cls(customer_history, product_context)

    def to_dict(self) -> dict[str, bool]:
        return {
            "include_customer_history": self.include_customer_history,
            "include_product_context": self.include_product_context,
        }


@dataclass(frozen=True)
class Ticket:
    case_id: str
    customer_request: CustomerRequest
    investigation_scope: InvestigationScope
    policy_version: str

    @classmethod
    def from_dict(cls, value: Any, *, filename_case_id: str | None = None) -> "Ticket":
        data = _require_mapping(value, "ticket")
        _require_exact_keys(data, {"case_id", "customer_request", "investigation_scope", "policy_version"}, "ticket")
        case_id = _require_string(data["case_id"], "ticket.case_id")
        if not CASE_ID_RE.fullmatch(case_id):
            raise ContractError("ticket.case_id must match EC_###")
        if filename_case_id is not None and case_id != filename_case_id:
            raise ContractError("ticket.case_id must match its filename")
        policy_version = _require_string(data["policy_version"], "ticket.policy_version")
        if policy_version != POLICY_VERSION:
            raise ContractError(f"ticket.policy_version must be {POLICY_VERSION}")
        return cls(
            case_id=case_id,
            customer_request=CustomerRequest.from_dict(data["customer_request"]),
            investigation_scope=InvestigationScope.from_dict(data["investigation_scope"]),
            policy_version=policy_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "customer_request": self.customer_request.to_dict(),
            "investigation_scope": self.investigation_scope.to_dict(),
            "policy_version": self.policy_version,
        }


TASK_CATALOG: dict[str, tuple[str, str]] = {
    AgentName.CUSTOMER.value: ("investigate_customer", "aggregate_customer_facts"),
    AgentName.ORDER_PRODUCT.value: ("investigate_order_and_product", "aggregate_order_product_facts"),
    AgentName.PAYMENT.value: ("investigate_payment", "aggregate_payment_facts"),
    AgentName.DELIVERY.value: ("investigate_delivery", "aggregate_delivery_facts"),
    AgentName.POLICY.value: ("apply_ec_policy_v2", "assemble_and_verify_output"),
}


@dataclass(frozen=True)
class Handoff:
    case_id: str
    from_agent: str
    to_agent: str
    task: str
    facts: dict[str, Any]
    evidence_ids: tuple[str, ...]
    missing_or_conflicting_data: tuple[str, ...]
    next_task: str

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        expected_agent: str | None = None,
        case_id: str | None = None,
    ) -> "Handoff":
        data = _require_mapping(value, "handoff")
        _require_exact_keys(
            data,
            {
                "case_id",
                "from_agent",
                "to_agent",
                "task",
                "facts",
                "evidence_ids",
                "missing_or_conflicting_data",
                "next_task",
            },
            "handoff",
        )
        handoff_case_id = _require_string(data["case_id"], "handoff.case_id")
        from_agent = _require_string(data["from_agent"], "handoff.from_agent")
        to_agent = _require_string(data["to_agent"], "handoff.to_agent")
        if case_id is not None and handoff_case_id != case_id:
            raise ContractError("handoff.case_id does not match the ticket")
        if expected_agent is not None and from_agent != expected_agent:
            raise ContractError("handoff.from_agent does not match the called agent")
        if to_agent != AgentName.COORDINATOR.value:
            raise ContractError("handoff.to_agent must be coordinator")
        if from_agent not in TASK_CATALOG:
            raise ContractError("handoff.from_agent is not a K4 handoff agent")
        expected_task, expected_next_task = TASK_CATALOG[from_agent]
        if _require_string(data["task"], "handoff.task") != expected_task:
            raise ContractError("handoff.task is invalid for the source agent")
        if _require_string(data["next_task"], "handoff.next_task") != expected_next_task:
            raise ContractError("handoff.next_task is invalid for the source agent")
        facts = _require_mapping(data["facts"], "handoff.facts")
        evidence = _require_string_list(data["evidence_ids"], "handoff.evidence_ids")
        missing = _require_string_list(data["missing_or_conflicting_data"], "handoff.missing_or_conflicting_data")
        allowed_prefixes = {
            AgentName.CUSTOMER.value: (),
            AgentName.ORDER_PRODUCT.value: ("order:", "item:", "seller:"),
            AgentName.PAYMENT.value: ("payment:",),
            AgentName.DELIVERY.value: (),
            AgentName.POLICY.value: ("policy:",),
        }[from_agent]
        if any(not item.startswith(allowed_prefixes) for item in evidence):
            raise ContractError("handoff evidence belongs to a different agent domain")
        return cls(
            case_id=handoff_case_id,
            from_agent=from_agent,
            to_agent=to_agent,
            task=expected_task,
            facts=deepcopy(dict(facts)),
            evidence_ids=evidence,
            missing_or_conflicting_data=missing,
            next_task=expected_next_task,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "task": self.task,
            "facts": json_value(self.facts),
            "evidence_ids": list(self.evidence_ids),
            "missing_or_conflicting_data": list(self.missing_or_conflicting_data),
            "next_task": self.next_task,
        }


@dataclass(frozen=True)
class CustomerFacts:
    claimed_order_id: str
    customer_id: str
    customer_unique_id: str
    related_order_ids: tuple[str, ...]
    related_order_count: int
    has_related_orders: bool

    @classmethod
    def from_dict(cls, value: Any) -> "CustomerFacts":
        data = _require_mapping(value, "customer facts")
        _require_exact_keys(data, {"claimed_order_id", "customer_id", "customer_unique_id", "related_order_ids", "related_order_count", "has_related_orders"}, "customer facts")
        related = _require_string_list(data["related_order_ids"], "customer_facts.related_order_ids", limit=5)
        claimed_order_id = _require_string(data["claimed_order_id"], "customer_facts.claimed_order_id")
        if claimed_order_id in related:
            raise ContractError("customer_facts.related_order_ids must not include claimed order")
        count = _require_int(data["related_order_count"], "customer_facts.related_order_count")
        has_related = _require_bool(data["has_related_orders"], "customer_facts.has_related_orders")
        if has_related != (count > 0) or count < len(related):
            raise ContractError("customer facts count/projection invariant failed")
        return cls(claimed_order_id, _require_string(data["customer_id"], "customer_facts.customer_id"), _require_string(data["customer_unique_id"], "customer_facts.customer_unique_id"), related, count, has_related)

    def to_dict(self) -> dict[str, Any]:
        return json_value(self)


@dataclass(frozen=True)
class OrderItemFact:
    order_id: str
    order_item_id: str
    product_id: str
    seller_id: str
    price_raw: Decimal
    freight_value_raw: Decimal
    shipping_limit_date: str | None
    category_name: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "OrderItemFact":
        data = _require_mapping(value, "order item fact")
        _require_exact_keys(data, {"order_id", "order_item_id", "product_id", "seller_id", "price_raw", "freight_value_raw", "shipping_limit_date", "category_name"}, "order item fact")
        for name in ("shipping_limit_date", "category_name"):
            if data[name] is not None:
                _require_string(data[name], f"order_item.{name}")
        return cls(
            _require_string(data["order_id"], "order_item.order_id"),
            _require_string(data["order_item_id"], "order_item.order_item_id"),
            _require_string(data["product_id"], "order_item.product_id"),
            _require_string(data["seller_id"], "order_item.seller_id"),
            _decimal(data["price_raw"], "order_item.price_raw"),  # type: ignore[arg-type]
            _decimal(data["freight_value_raw"], "order_item.freight_value_raw"),  # type: ignore[arg-type]
            data["shipping_limit_date"],
            data["category_name"],
        )


@dataclass(frozen=True)
class OrderProductFacts:
    order_id: str
    order_status: str
    customer_id: str
    item_ids: tuple[str, ...]
    seller_ids: tuple[str, ...]
    product_ids: tuple[str, ...]
    category_names: tuple[str, ...]
    item_count: int
    seller_count: int
    product_count: int
    category_count: int
    items: tuple[OrderItemFact, ...]

    @classmethod
    def from_dict(cls, value: Any) -> "OrderProductFacts":
        data = _require_mapping(value, "order/product facts")
        expected = {"order_id", "order_status", "customer_id", "item_ids", "seller_ids", "product_ids", "category_names", "item_count", "seller_count", "product_count", "category_count", "items"}
        _require_exact_keys(data, expected, "order/product facts")
        items_value = data["items"]
        if not isinstance(items_value, list):
            raise ContractError("order_product_facts.items must be an array")
        items = tuple(OrderItemFact.from_dict(item) for item in items_value)
        result = cls(
            _require_string(data["order_id"], "order_product_facts.order_id"),
            _require_string(data["order_status"], "order_product_facts.order_status"),
            _require_string(data["customer_id"], "order_product_facts.customer_id"),
            _require_string_list(data["item_ids"], "order_product_facts.item_ids", limit=5),
            _require_string_list(data["seller_ids"], "order_product_facts.seller_ids", limit=3),
            _require_string_list(data["product_ids"], "order_product_facts.product_ids", limit=5),
            _require_string_list(data["category_names"], "order_product_facts.category_names", limit=5),
            _require_int(data["item_count"], "order_product_facts.item_count"),
            _require_int(data["seller_count"], "order_product_facts.seller_count"),
            _require_int(data["product_count"], "order_product_facts.product_count"),
            _require_int(data["category_count"], "order_product_facts.category_count"),
            items,
        )
        if result.item_count != len(items) or any(item.order_id != result.order_id for item in items):
            raise ContractError("order/product item invariant failed")
        if result.item_count == 0 and any((result.item_ids, result.seller_ids, result.product_ids, result.category_names, result.seller_count, result.product_count, result.category_count)):
            raise ContractError("no-item order must have empty projections and zero entity counts")
        if min(result.seller_count, result.product_count, result.category_count) < 0 or any(count < len(values) for count, values in ((result.item_count, result.item_ids), (result.seller_count, result.seller_ids), (result.product_count, result.product_ids), (result.category_count, result.category_names))):
            raise ContractError("order/product count/projection invariant failed")
        expected_item_ids = tuple(f"{item.order_id}:{item.order_item_id}" for item in items[:5])
        expected_sellers = tuple(dict.fromkeys(item.seller_id for item in items))[:3]
        expected_products = tuple(dict.fromkeys(item.product_id for item in items))[:5]
        expected_categories = tuple(dict.fromkeys(item.category_name for item in items if item.category_name is not None))[:5]
        if result.item_ids != expected_item_ids or result.seller_ids != expected_sellers or result.product_ids != expected_products or result.category_names != expected_categories:
            raise ContractError("order/product projections must be stable source-derived prefixes")
        if result.seller_count != len(dict.fromkeys(item.seller_id for item in items)) or result.product_count != len(dict.fromkeys(item.product_id for item in items)) or result.category_count != len(dict.fromkeys(item.category_name for item in items if item.category_name is not None)):
            raise ContractError("order/product entity counts must use full distinct source rows")
        return result

    def to_dict(self) -> dict[str, Any]:
        return json_value(self)


@dataclass(frozen=True)
class PaymentFacts:
    order_id: str
    payment_ids: tuple[str, ...]
    payment_count: int
    payment_types: tuple[str, ...]
    item_total_brl: Decimal
    freight_total_brl: Decimal
    expected_total_brl: Decimal | None
    payment_total_brl: Decimal
    difference_brl: Decimal | None
    reconciled: bool | None

    @classmethod
    def from_dict(cls, value: Any, *, item_count: int | None = None) -> "PaymentFacts":
        data = _require_mapping(value, "payment facts")
        expected = {"order_id", "payment_ids", "payment_count", "payment_types", "item_total_brl", "freight_total_brl", "expected_total_brl", "payment_total_brl", "difference_brl", "reconciled"}
        _require_exact_keys(data, expected, "payment facts")
        expected_total = _decimal(data["expected_total_brl"], "payment_facts.expected_total_brl", nullable=True)
        difference = _decimal(data["difference_brl"], "payment_facts.difference_brl", nullable=True)
        reconciled = data["reconciled"]
        if reconciled is not None:
            reconciled = _require_bool(reconciled, "payment_facts.reconciled")
        result = cls(
            _require_string(data["order_id"], "payment_facts.order_id"),
            _require_string_list(data["payment_ids"], "payment_facts.payment_ids", limit=5),
            _require_int(data["payment_count"], "payment_facts.payment_count"),
            _require_string_list(data["payment_types"], "payment_facts.payment_types"),
            _decimal(data["item_total_brl"], "payment_facts.item_total_brl"),  # type: ignore[arg-type]
            _decimal(data["freight_total_brl"], "payment_facts.freight_total_brl"),  # type: ignore[arg-type]
            expected_total,
            _decimal(data["payment_total_brl"], "payment_facts.payment_total_brl"),  # type: ignore[arg-type]
            difference,
            reconciled,
        )
        if result.payment_count < len(result.payment_ids):
            raise ContractError("payment_facts.payment_count must include the projection")
        no_items = item_count == 0
        if no_items and any(item is not None for item in (result.expected_total_brl, result.difference_brl, result.reconciled)):
            raise ContractError("no-item payment facts require null reconciliation")
        if item_count is not None and item_count > 0:
            if result.expected_total_brl != result.item_total_brl + result.freight_total_brl:
                raise ContractError("payment_facts.expected_total_brl formula is invalid")
            if result.difference_brl != result.payment_total_brl - result.expected_total_brl:
                raise ContractError("payment_facts.difference_brl formula is invalid")
            if result.reconciled != (abs(result.difference_brl) <= RECONCILIATION_TOLERANCE):
                raise ContractError("payment_facts.reconciled formula is invalid")
        return result

    def to_dict(self) -> dict[str, Any]:
        return json_value(self)


@dataclass(frozen=True)
class SellerHandoffFact:
    seller_id: str
    shipping_limit_at: str | None
    handoff_variance_hours: Decimal | None
    late_handoff: bool

    @classmethod
    def from_dict(cls, value: Any) -> "SellerHandoffFact":
        data = _require_mapping(value, "seller handoff fact")
        _require_exact_keys(data, {"seller_id", "shipping_limit_at", "handoff_variance_hours", "late_handoff"}, "seller handoff fact")
        shipping_limit = data["shipping_limit_at"]
        if shipping_limit is not None:
            _require_string(shipping_limit, "seller_handoff.shipping_limit_at")
        variance = _decimal(data["handoff_variance_hours"], "seller_handoff.handoff_variance_hours", nullable=True)
        late = _require_bool(data["late_handoff"], "seller_handoff.late_handoff")
        if variance is None and late:
            raise ContractError("seller_handoff.late_handoff cannot be true without variance")
        if variance is not None and shipping_limit is None:
            raise ContractError("seller_handoff.shipping_limit_at is required with variance")
        if variance is not None and late != (variance > 0):
            raise ContractError("seller_handoff.late_handoff is inconsistent with variance")
        return cls(_require_string(data["seller_id"], "seller_handoff.seller_id"), shipping_limit, variance, late)


@dataclass(frozen=True)
class DeliveryFacts:
    order_id: str
    delivered_at: str | None
    estimated_delivery_at: str | None
    carrier_handoff_at: str | None
    delivery_variance_hours: Decimal | None
    seller_handoff_analysis: tuple[SellerHandoffFact, ...]
    late_handoff_seller_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Any) -> "DeliveryFacts":
        data = _require_mapping(value, "delivery facts")
        expected = {"order_id", "delivered_at", "estimated_delivery_at", "carrier_handoff_at", "delivery_variance_hours", "seller_handoff_analysis", "late_handoff_seller_ids"}
        _require_exact_keys(data, expected, "delivery facts")
        for name in ("delivered_at", "estimated_delivery_at", "carrier_handoff_at"):
            if data[name] is not None:
                _require_string(data[name], f"delivery_facts.{name}")
        seller_analysis_value = data["seller_handoff_analysis"]
        if not isinstance(seller_analysis_value, list):
            raise ContractError("delivery_facts.seller_handoff_analysis must be an array")
        analysis = tuple(SellerHandoffFact.from_dict(item) for item in seller_analysis_value)
        late_sellers = _require_string_list(data["late_handoff_seller_ids"], "delivery_facts.late_handoff_seller_ids", limit=3)
        if tuple(item.seller_id for item in analysis if item.late_handoff) != late_sellers:
            raise ContractError("delivery_facts.late_handoff_seller_ids is inconsistent")
        delivery_variance = _decimal(data["delivery_variance_hours"], "delivery_facts.delivery_variance_hours", nullable=True)
        if (data["delivered_at"] is None or data["estimated_delivery_at"] is None) != (delivery_variance is None):
            raise ContractError("delivery_variance_hours must be null unless both delivery timestamps exist")
        if len(analysis) > 3:
            raise ContractError("delivery_facts.seller_handoff_analysis must contain at most 3 sellers")
        if data["carrier_handoff_at"] is None and any(item.handoff_variance_hours is not None for item in analysis):
            raise ContractError("carrier_handoff_at is required for seller handoff variance")
        return cls(
            _require_string(data["order_id"], "delivery_facts.order_id"),
            data["delivered_at"], data["estimated_delivery_at"], data["carrier_handoff_at"],
            delivery_variance,
            analysis, late_sellers,
        )

    def to_dict(self) -> dict[str, Any]:
        return json_value(self)


@dataclass(frozen=True)
class PolicyDecision:
    primary_issue: str
    secondary_issues: tuple[str, ...]
    case_status: str
    confidence: float
    ranked_causes: tuple[dict[str, Any], ...]
    responsible_parties: tuple[dict[str, Any], ...]
    recommended_refund_brl: Decimal
    resolution_actions: tuple[str, ...]
    policy_evidence_id: str

    @classmethod
    def from_dict(cls, value: Any) -> "PolicyDecision":
        data = _require_mapping(value, "policy decision")
        expected = {"primary_issue", "secondary_issues", "case_status", "confidence", "ranked_causes", "responsible_parties", "recommended_refund_brl", "resolution_actions", "policy_evidence_id"}
        _require_exact_keys(data, expected, "policy decision")
        primary_issue = _require_string(data["primary_issue"], "policy_decision.primary_issue")
        if primary_issue not in PRIMARY_ISSUES:
            raise ContractError("policy_decision.primary_issue is not an EC_POLICY_V2 issue")
        secondary_issues = _require_string_list(data["secondary_issues"], "policy_decision.secondary_issues", limit=5)
        if (
            any(issue not in SECONDARY_ISSUES for issue in secondary_issues)
            or len(set(secondary_issues)) != len(secondary_issues)
            or tuple(sorted(secondary_issues, key=SECONDARY_ISSUES.index)) != secondary_issues
        ):
            raise ContractError("policy_decision.secondary_issues must be unique EC_POLICY_V2 issues in business order")
        case_status = _require_string(data["case_status"], "policy_decision.case_status")
        if case_status not in {"action_required", "no_action"}:
            raise ContractError("policy_decision.case_status is invalid")
        confidence = data["confidence"]
        if type(confidence) not in (int, float) or not 0 <= float(confidence) <= 1:
            raise ContractError("policy_decision.confidence must be in [0, 1]")
        ranked = data["ranked_causes"]
        responsible = data["responsible_parties"]
        if not isinstance(ranked, list) or not all(isinstance(item, Mapping) for item in ranked) or not ranked or len(ranked) > 3:
            raise ContractError("policy_decision.ranked_causes must be an array of at most 3 objects")
        if not isinstance(responsible, list) or not all(isinstance(item, Mapping) for item in responsible) or len(responsible) > 3:
            raise ContractError("policy_decision.responsible_parties must be an array of at most 3 objects")
        evidence = _require_string(data["policy_evidence_id"], "policy_decision.policy_evidence_id")
        if not evidence.startswith("policy:"):
            raise ContractError("policy_decision.policy_evidence_id must be policy evidence")
        for index, cause in enumerate(ranked, start=1):
            _require_exact_keys(cause, {"cause_code", "rank"}, "policy_decision.ranked_causes entry")
            if _require_string(cause["cause_code"], "policy_decision.ranked_causes.cause_code") not in ROOT_CAUSES or _require_int(cause["rank"], "policy_decision.ranked_causes.rank", minimum=1) != index:
                raise ContractError("policy_decision.ranked_causes must use known contiguous ranks")
        if evidence != f"policy:{ranked[0]['cause_code']}":
            raise ContractError("policy_decision.policy_evidence_id must match the rank-1 cause")
        for party in responsible:
            _require_exact_keys(party, {"party_type", "party_id"}, "policy_decision.responsible_parties entry")
            if _require_string(party["party_type"], "policy_decision.responsible_parties.party_type") not in {"seller", "platform", "logistics_provider"}:
                raise ContractError("policy_decision responsible party type is invalid")
            _require_string(party["party_id"], "policy_decision.responsible_parties.party_id")
        actions = _require_string_list(data["resolution_actions"], "policy_decision.resolution_actions", limit=5)
        if any(action not in RESOLUTION_ACTIONS for action in actions):
            raise ContractError("policy_decision.resolution_actions contains an unknown action")
        return cls(
            primary_issue,
            secondary_issues,
            case_status,
            float(confidence),
            tuple(dict(item) for item in ranked),
            tuple(dict(item) for item in responsible),
            _decimal(data["recommended_refund_brl"], "policy_decision.recommended_refund_brl"),  # type: ignore[arg-type]
            actions,
            evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return json_value(self)


@dataclass(frozen=True)
class AggregatedFacts:
    ticket: Ticket
    customer: CustomerFacts
    order_product: OrderProductFacts
    payment: PaymentFacts
    delivery: DeliveryFacts
    source_evidence_ids: tuple[str, ...]
    missing_or_conflicting_data: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return json_value(self)


@dataclass(frozen=True)
class StructuredError:
    code: str
    stage: str
    message: str
    agent: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return json_value(self)


@dataclass(frozen=True)
class RunResult:
    case_id: str
    status: RunStatus
    output: dict[str, Any] | None
    errors: tuple[StructuredError, ...]
    stage_durations_ms: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status is RunStatus.VERIFIED and (self.output is None or self.errors):
            raise ContractError("verified run result requires output and no errors")
        if self.status is RunStatus.BLOCKED and (self.output is not None or not self.errors):
            raise ContractError("blocked run result requires errors and no output")

    @classmethod
    def verified(cls, case_id: str, output: Mapping[str, Any], durations: Mapping[str, float] | None = None) -> "RunResult":
        return cls(case_id, RunStatus.VERIFIED, dict(output), (), dict(durations or {}))

    @classmethod
    def blocked(cls, case_id: str, errors: Sequence[StructuredError], durations: Mapping[str, float] | None = None) -> "RunResult":
        return cls(case_id, RunStatus.BLOCKED, None, tuple(errors), dict(durations or {}))

    def to_dict(self) -> dict[str, Any]:
        return json_value(self)


@dataclass(frozen=True)
class InputValidationCase:
    valid: bool
    claimed_order_id: str | None
    errors: tuple[StructuredError, ...]
    ticket: Ticket | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "claimed_order_id": self.claimed_order_id,
            "errors": [error.to_dict() for error in self.errors],
        }


@dataclass(frozen=True)
class InputValidationReport:
    run_id: str
    started_at: str
    finished_at: str
    success: bool
    summary: dict[str, int]
    fatal_errors: tuple[StructuredError, ...]
    release_errors: tuple[StructuredError, ...]
    valid_case_ids: tuple[str, ...]
    cases: dict[str, InputValidationCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "success": self.success,
            "summary": dict(self.summary),
            "fatal_errors": [error.to_dict() for error in self.fatal_errors],
            "release_errors": [error.to_dict() for error in self.release_errors],
            "valid_case_ids": list(self.valid_case_ids),
            "cases": {key: value.to_dict() for key, value in self.cases.items()},
        }
