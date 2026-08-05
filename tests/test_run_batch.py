"""Integration tests for release validation and transactional publication."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.contracts import EXPECTED_CASE_IDS, RunResult, StructuredError, Ticket
from src.run_batch import run_batch, validate_released_inputs


class FakeRepository:
    def __init__(self, _data_dir: Path, order_ids: set[str]) -> None:
        self.order_ids = order_ids

    def has_order(self, order_id: str) -> bool:
        return order_id in self.order_ids


class FakeTrace:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.finalized = False

    def write(self, event: dict) -> None:
        self.events.append(event)

    def finalize(self) -> None:
        self.finalized = True


class FinalizeFailTrace(FakeTrace):
    def finalize(self) -> None:
        raise OSError("trace destination unavailable")


def make_ticket(case_id: str, order_id: str) -> dict:
    return {
        "case_id": case_id,
        "customer_request": {"language": "vi", "message": "Kiểm tra", "claimed_order_id": order_id},
        "investigation_scope": {"include_customer_history": True, "include_product_context": True},
        "policy_version": "EC_POLICY_V2",
    }


def make_release(root: Path) -> set[str]:
    input_dir = root / "input"
    data_dir = root / "data"
    input_dir.mkdir()
    data_dir.mkdir()
    (root / "output").mkdir()
    (root / "logging").mkdir()
    order_ids: set[str] = set()
    for index, case_id in enumerate(EXPECTED_CASE_IDS, start=1):
        order_id = f"{index:032x}"
        order_ids.add(order_id)
        (input_dir / f"{case_id}.json").write_text(json.dumps(make_ticket(case_id, order_id)), encoding="utf-8")
    (data_dir / "olist_orders_dataset.csv").write_text("order_id\n" + "\n".join(sorted(order_ids)) + "\n", encoding="utf-8")
    return order_ids


class BatchValidationTests(unittest.TestCase):
    def test_wrong_case_extension_is_reported_as_unexpected_release_input(self) -> None:
        """A casing variant must not hide beside the immutable EC_001.json release file."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_ids = make_release(root)
            original = root / "input" / "EC_001.json"
            original.rename(root / "input" / "EC_001.JSON")

            report = validate_released_inputs(root / "input", FakeRepository(root / "data", order_ids), run_id="run-1")

            self.assertIn("INPUT_FILE_UNEXPECTED", {error.code for error in report.release_errors})

    def test_duplicate_claimed_order_blocks_every_related_ticket(self) -> None:
        """Choosing a first duplicate would silently investigate the wrong released case."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_ids = make_release(root)
            duplicate = json.loads((root / "input" / "EC_002.json").read_text(encoding="utf-8"))
            duplicate["customer_request"]["claimed_order_id"] = json.loads(
                (root / "input" / "EC_001.json").read_text(encoding="utf-8")
            )["customer_request"]["claimed_order_id"]
            (root / "input" / "EC_002.json").write_text(json.dumps(duplicate), encoding="utf-8")
            repo = FakeRepository(root / "data", order_ids)

            report = validate_released_inputs(root / "input", repo, run_id="run-1")

            self.assertFalse(report.success)
            self.assertIn("DUPLICATE_CLAIMED_ORDER_ID", {error.code for error in report.cases["EC_001"].errors} | {error.code for error in report.cases["EC_002"].errors})

    def test_case_id_collision_blocks_the_validly_named_partner_too(self) -> None:
        """A filename mismatch cannot let its duplicate case ID continue into processing."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_ids = make_release(root)
            malformed = json.loads((root / "input" / "EC_002.json").read_text(encoding="utf-8"))
            malformed["case_id"] = "EC_001"
            (root / "input" / "EC_002.json").write_text(json.dumps(malformed), encoding="utf-8")

            report = validate_released_inputs(root / "input", FakeRepository(root / "data", order_ids), run_id="run-1")

            self.assertFalse(report.cases["EC_001"].valid)
            self.assertIn("DUPLICATE_CASE_ID", {error.code for error in report.cases["EC_001"].errors})

    def test_partial_batch_keeps_last_published_output_set(self) -> None:
        """Publishing 49/50 cases would replace a known-good submission with an invalid partial set."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_ids = make_release(root)
            old_output = root / "output" / "EC_001.json"
            old_output.write_text('{"previous":true}\n', encoding="utf-8")
            traces: list[FakeTrace] = []

            def repository_factory(data_dir: Path) -> FakeRepository:
                return FakeRepository(data_dir, order_ids)

            def process(ticket: Ticket, _repo: object, _trace: object) -> RunResult:
                if ticket.case_id == "EC_050":
                    return RunResult.blocked(ticket.case_id, [StructuredError("VERIFICATION_FAILED", "verification", "bad")])
                return RunResult.verified(ticket.case_id, {"case_id": ticket.case_id})

            code = run_batch(root, repository_factory=repository_factory, processor=process, trace_factory=lambda *_: traces.append(FakeTrace()) or traces[-1])

            self.assertEqual(code, 1)
            self.assertEqual(old_output.read_text(encoding="utf-8"), '{"previous":true}\n')
            self.assertTrue(traces[0].finalized)

    def test_full_verified_batch_publishes_all_expected_case_files(self) -> None:
        """A successful batch must publish precisely the fifty verified case documents."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_ids = make_release(root)

            def repository_factory(data_dir: Path) -> FakeRepository:
                return FakeRepository(data_dir, order_ids)

            def process(ticket: Ticket, _repo: object, _trace: object) -> RunResult:
                return RunResult.verified(ticket.case_id, {"case_id": ticket.case_id})

            code = run_batch(root, repository_factory=repository_factory, processor=process, trace_factory=lambda *_: FakeTrace())

            self.assertEqual(code, 0)
            self.assertEqual(
                sorted(path.name for path in (root / "output").glob("EC_*.json")),
                [f"{case_id}.json" for case_id in EXPECTED_CASE_IDS],
            )

    def test_trace_finalize_failure_blocks_publish_and_success_exit(self) -> None:
        """A lost audit trace must not be reported as a successful, publishable batch."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order_ids = make_release(root)
            previous = root / "output" / "EC_001.json"
            previous.write_text('{"previous":true}\n', encoding="utf-8")

            def repository_factory(data_dir: Path) -> FakeRepository:
                return FakeRepository(data_dir, order_ids)

            def process(ticket: Ticket, _repo: object, _trace: object) -> RunResult:
                return RunResult.verified(ticket.case_id, {"case_id": ticket.case_id})

            traces: list[FinalizeFailTrace] = []
            code = run_batch(
                root,
                repository_factory=repository_factory,
                processor=process,
                trace_factory=lambda *_: traces.append(FinalizeFailTrace()) or traces[-1],
            )

            self.assertEqual(code, 1)
            self.assertEqual(previous.read_text(encoding="utf-8"), '{"previous":true}\n')
            self.assertNotIn(
                "verified",
                [event.get("status") for event in traces[0].events if event.get("event_type") == "batch_finished"],
            )


if __name__ == "__main__":
    unittest.main()
