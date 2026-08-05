"""JSONL trace writer for actual agent handoffs. Owned by Member 5."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_TRACE_PATH = Path("logging/trace.jsonl")
_active_trace_path = DEFAULT_TRACE_PATH
_sequence = 0
_run_id: str | None = None
_SENSITIVE_KEYWORDS = ("api_key", "authorization", "token", "secret")


def start_trace(run_id: str, path: Path | str = DEFAULT_TRACE_PATH) -> None:
    """Start a fresh run by replacing the previous trace file."""

    global _active_trace_path, _run_id, _sequence
    _active_trace_path = Path(path)
    _active_trace_path.parent.mkdir(parents=True, exist_ok=True)
    _active_trace_path.write_text("", encoding="utf-8")
    _run_id = run_id
    _sequence = 0


def write_trace(event: dict[str, Any]) -> None:
    """Append one scrubbed event to the active trace run."""

    global _sequence
    if _run_id is None:
        raise RuntimeError("start_trace(run_id) must be called before write_trace")

    required = {"case_id", "event", "status"}
    missing = sorted(required - event.keys())
    if missing:
        raise ValueError(f"trace event missing required fields: {', '.join(missing)}")

    _sequence += 1
    record = {
        "run_id": _run_id,
        "sequence": _sequence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **_redact(event),
    }
    with _active_trace_path.open("a", encoding="utf-8", newline="\n") as trace_file:
        trace_file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        trace_file.write("\n")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(keyword in key.lower() for keyword in _SENSITIVE_KEYWORDS)
            else _redact(nested_value)
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value

