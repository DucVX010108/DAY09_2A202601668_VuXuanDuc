"""Trace writer regression tests. Owned by Member 5."""

import json

from src.trace import start_trace, write_trace


def test_trace_replaces_previous_run_and_redacts_secret(tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('{"old": true}\n', encoding="utf-8")

    start_trace("run-1", trace_path)
    write_trace({
        "case_id": "EC_001",
        "event": "case_started",
        "status": "started",
        "api_key": "must-not-appear",
    })

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["run_id"] == "run-1"
    assert records[0]["sequence"] == 1
    assert records[0]["api_key"] == "[REDACTED]"

