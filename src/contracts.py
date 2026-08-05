"""Shared, JSON-serializable contracts owned by Member 1.

All agents exchange verified facts through ``Handoff``.  Extend this module
before changing an agent payload so the team retains one stable contract.
"""

from typing import Any, TypedDict


POLICY_VERSION = "EC_POLICY_V2"


class Handoff(TypedDict):
    """Minimum envelope required for every agent-to-agent handoff."""

    case_id: str
    from_agent: str
    to_agent: str
    task: str
    facts: dict[str, Any]
    evidence_ids: list[str]
    missing_or_conflicting_data: list[str]
    next_task: str

