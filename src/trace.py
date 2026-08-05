"""JSONL trace writer for actual agent handoffs. Owned by Member 5."""


def write_trace(event: dict) -> None:
    """Write one scrubbed trace event for the current run.

    TODO(M5): own run lifecycle and replace—not append—the previous trace.
    """

    raise NotImplementedError

