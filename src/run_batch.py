"""Deterministic batch entry point for the fifty released K4 tickets."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from typing import Any, Callable

from .contracts import (
    EXPECTED_CASE_IDS,
    InputValidationCase,
    InputValidationReport,
    RunResult,
    RunStatus,
    Stage,
    StructuredError,
    Ticket,
    dumps_json,
)
from .coordinator import process_case
from .repository import OlistRepository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _error(code: str, message: str, *, stage: Stage = Stage.INPUT_VALIDATION, details: dict[str, Any] | None = None) -> StructuredError:
    return StructuredError(code=code, stage=stage.value, message=message, details=details or {})


def atomic_write_json(path: Path, value: Any) -> None:
    """Replace one JSON file atomically without leaving a partial document."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False)
    temporary_path = Path(handle.name)
    try:
        with handle:
            handle.write(dumps_json(value))
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


class AtomicTraceSink:
    """Minimal lifecycle adapter until Member 5 supplies the shared trace sink."""

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.{run_id}.", suffix=".tmp", dir=path.parent, text=True)
        self._temporary_path = Path(name)
        self._stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        self._sequence = 0

    def write(self, event: dict[str, Any]) -> None:
        self._sequence += 1
        record = {
            "run_id": self.run_id,
            "sequence": self._sequence,
            "recorded_at_utc": _utc_now(),
            **event,
        }
        self._stream.write(dumps_json(record))
        self._stream.flush()

    def finalize(self) -> None:
        if not self._stream.closed:
            self._stream.flush()
            try:
                os.fsync(self._stream.fileno())
            except OSError:
                pass
            self._stream.close()
        os.replace(self._temporary_path, self.path)

    def abort_and_finalize(self, error: str) -> None:
        self.write({"event_type": "batch_fatal", "case_id": None, "stage": "trace_finalize", "status": "error", "errors": [error]})
        self.finalize()


class TraceGuard:
    """Capture trace-sink failures so they are never mistaken for batch success."""

    def __init__(self, sink: Any) -> None:
        self._sink = sink
        self.errors: list[str] = []
        self._finalized = False

    @property
    def failed(self) -> bool:
        return bool(self.errors)

    def write(self, event: dict[str, Any]) -> None:
        if self._finalized:
            self.errors.append("attempted to write after trace finalization")
            return
        try:
            self._sink.write(event)
        except Exception as exc:
            self.errors.append(f"trace write failed: {type(exc).__name__}: {exc}")

    def finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        try:
            self._sink.finalize()
        except Exception as exc:
            self.errors.append(f"trace finalization failed: {type(exc).__name__}: {exc}")


def _repository_has_order(repository: object, order_id: str) -> bool:
    method = getattr(repository, "has_order", None)
    if callable(method):
        return bool(method(order_id))
    for attribute in ("orders_by_id", "orders", "orders_index"):
        index = getattr(repository, attribute, None)
        if isinstance(index, dict):
            return order_id in index
    raise RuntimeError("repository must expose has_order(order_id) or an orders index")


def _read_json_ticket(path: Path) -> Any:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"INPUT_JSON_INVALID: invalid UTF-8: {exc}") from exc
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"INPUT_JSON_INVALID: {exc.msg}") from exc
    return raw


def validate_released_inputs(input_dir: Path, repository: object, *, run_id: str) -> InputValidationReport:
    """Parse the immutable release and return diagnostics for every expected case."""

    started_at = _utc_now()
    errors_by_case: dict[str, list[StructuredError]] = defaultdict(list)
    tickets: dict[str, Ticket] = {}
    reported_case_ids: dict[str, list[str]] = defaultdict(list)
    release_errors: list[StructuredError] = []
    fatal_errors: list[StructuredError] = []
    if not input_dir.is_dir():
        fatal_errors.append(_error("INPUT_DIRECTORY_UNAVAILABLE", f"input directory is unavailable: {input_dir}"))
    else:
        expected_names = {f"{case_id}.json" for case_id in EXPECTED_CASE_IDS}
        discovered_case_files = {
            path.name
            for path in input_dir.iterdir()
            if path.is_file() and path.suffix.lower() == ".json" and path.stem.lower().startswith("ec_")
        }
        missing_names = sorted(expected_names - discovered_case_files)
        unexpected_names = sorted(discovered_case_files - expected_names)
        for name in missing_names:
            case_id = name.removesuffix(".json")
            error = _error("INPUT_FILE_MISSING", f"released file is missing: {name}")
            errors_by_case[case_id].append(error)
            release_errors.append(error)
        for name in unexpected_names:
            release_errors.append(_error("INPUT_FILE_UNEXPECTED", f"unexpected release-shaped input: {name}", details={"filename": name}))
        for case_id in EXPECTED_CASE_IDS:
            path = input_dir / f"{case_id}.json"
            if not path.exists():
                continue
            try:
                raw = _read_json_ticket(path)
                if isinstance(raw, dict) and isinstance(raw.get("case_id"), str):
                    reported_case_ids[raw["case_id"]].append(case_id)
                tickets[case_id] = Ticket.from_dict(raw, filename_case_id=case_id)
            except ValueError as exc:
                code, _, message = str(exc).partition(": ")
                errors_by_case[case_id].append(_error(code, message or str(exc)))
            except Exception as exc:
                errors_by_case[case_id].append(_error("INPUT_SCHEMA_INVALID", str(exc)))
    by_case_id: dict[str, list[str]] = defaultdict(list)
    by_order_id: dict[str, list[str]] = defaultdict(list)
    for filename_case_id, ticket in tickets.items():
        by_case_id[ticket.case_id].append(filename_case_id)
        by_order_id[ticket.customer_request.claimed_order_id].append(filename_case_id)
    for duplicate_case_id, case_ids in by_case_id.items():
        if len(case_ids) > 1:
            for case_id in case_ids:
                errors_by_case[case_id].append(_error("DUPLICATE_CASE_ID", f"duplicate case_id {duplicate_case_id}", details={"cases": sorted(case_ids)}))
    for duplicate_case_id, case_ids in reported_case_ids.items():
        if len(case_ids) > 1:
            for case_id in case_ids:
                errors_by_case[case_id].append(_error("DUPLICATE_CASE_ID", f"duplicate case_id {duplicate_case_id}", details={"cases": sorted(case_ids)}))
    for duplicate_order_id, case_ids in by_order_id.items():
        if len(case_ids) > 1:
            for case_id in case_ids:
                errors_by_case[case_id].append(_error("DUPLICATE_CLAIMED_ORDER_ID", f"duplicate claimed_order_id {duplicate_order_id}", details={"cases": sorted(case_ids)}))
    if not fatal_errors:
        for case_id, ticket in tickets.items():
            try:
                exists = _repository_has_order(repository, ticket.customer_request.claimed_order_id)
            except Exception as exc:
                fatal_errors.append(_error("REPOSITORY_INITIALIZATION_FAILED", str(exc)))
                break
            if not exists:
                errors_by_case[case_id].append(_error("ORDER_NOT_FOUND", "claimed_order_id does not exist in the orders CSV", details={"claimed_order_id": ticket.customer_request.claimed_order_id}))
    cases: dict[str, InputValidationCase] = {}
    for case_id in EXPECTED_CASE_IDS:
        ticket = tickets.get(case_id)
        case_errors = tuple(errors_by_case[case_id])
        cases[case_id] = InputValidationCase(valid=ticket is not None and not case_errors and not fatal_errors, claimed_order_id=ticket.customer_request.claimed_order_id if ticket else None, errors=case_errors, ticket=ticket if not case_errors and not fatal_errors else None)
    valid_case_ids = tuple(case_id for case_id in EXPECTED_CASE_IDS if cases[case_id].valid)
    summary = {
        "expected_file_count": len(EXPECTED_CASE_IDS),
        "discovered_expected_file_count": sum((input_dir / f"{case_id}.json").is_file() for case_id in EXPECTED_CASE_IDS) if input_dir.is_dir() else 0,
        "valid_case_count": len(valid_case_ids),
        "invalid_case_count": len(EXPECTED_CASE_IDS) - len(valid_case_ids),
        "missing_file_count": sum(error.code == "INPUT_FILE_MISSING" for error in release_errors),
        "unexpected_file_count": sum(error.code == "INPUT_FILE_UNEXPECTED" for error in release_errors),
    }
    return InputValidationReport(
        run_id=run_id,
        started_at=started_at,
        finished_at=_utc_now(),
        success=not fatal_errors and not release_errors and len(valid_case_ids) == len(EXPECTED_CASE_IDS),
        summary=summary,
        fatal_errors=tuple(fatal_errors),
        release_errors=tuple(release_errors),
        valid_case_ids=valid_case_ids,
        cases=cases,
    )


def _audit_staging(staging_dir: Path) -> bool:
    expected_names = {f"{case_id}.json" for case_id in EXPECTED_CASE_IDS}
    paths = {path.name: path for path in staging_dir.iterdir() if path.is_file()}
    if set(paths) != expected_names:
        return False
    for case_id in EXPECTED_CASE_IDS:
        try:
            document = json.loads(paths[f"{case_id}.json"].read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(document, dict) or document.get("case_id") != case_id:
            return False
    return True


class OutputPublication:
    """A reversible replacement of the exact fifty generated output files."""

    def __init__(self, output_dir: Path, backup_dir: Path, moved_old: list[Path], moved_new: list[Path]) -> None:
        self.output_dir = output_dir
        self.backup_dir = backup_dir
        self.moved_old = moved_old
        self.moved_new = moved_new
        self.closed = False

    def commit(self) -> None:
        if not self.closed:
            shutil.rmtree(self.backup_dir, ignore_errors=True)
            self.closed = True

    def rollback(self) -> None:
        if self.closed:
            return
        failures: list[str] = []
        for target in self.moved_new:
            try:
                target.unlink(missing_ok=True)
            except OSError as exc:
                failures.append(f"remove {target.name}: {exc}")
        for backup in self.moved_old:
            try:
                os.replace(backup, self.output_dir / backup.name)
            except OSError as exc:
                failures.append(f"restore {backup.name}: {exc}")
        if failures:
            raise RuntimeError("output rollback incomplete; backup preserved: " + "; ".join(failures))
        shutil.rmtree(self.backup_dir, ignore_errors=True)
        self.closed = True


def _publish_outputs(staging_dir: Path, output_dir: Path, work_dir: Path) -> OutputPublication:
    """Start a reversible publish transaction for exact K4 output targets."""

    if not _audit_staging(staging_dir):
        raise RuntimeError("staging audit failed")
    output_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = Path(tempfile.mkdtemp(prefix=".k4-output-backup-", dir=work_dir))
    moved_old: list[Path] = []
    moved_new: list[Path] = []
    publication = OutputPublication(output_dir, backup_dir, moved_old, moved_new)
    try:
        for case_id in EXPECTED_CASE_IDS:
            name = f"{case_id}.json"
            target = output_dir / name
            if target.exists():
                os.replace(target, backup_dir / name)
                moved_old.append(backup_dir / name)
        for case_id in EXPECTED_CASE_IDS:
            name = f"{case_id}.json"
            target = output_dir / name
            os.replace(staging_dir / name, target)
            moved_new.append(target)
    except Exception:
        try:
            publication.rollback()
        except Exception as rollback_error:
            raise RuntimeError(f"output publish failed and rollback failed: {rollback_error}")
        raise
    return publication


def run_batch(
    root: Path,
    *,
    repository_factory: Callable[[Path], object] = OlistRepository,
    processor: Callable[[Ticket, object, Any], RunResult] = process_case,
    trace_factory: Callable[[Path, str], Any] = AtomicTraceSink,
) -> int:
    """Run all released cases and publish only a complete verified set."""

    root = root.resolve()
    input_dir = root / "input"
    data_dir = root / "data"
    output_dir = root / "output"
    logging_dir = root / "logging"
    validation_path = logging_dir / "input_validation.json"
    trace_path = logging_dir / "trace.jsonl"
    run_id = str(uuid.uuid4())
    staging_dir: Path | None = None
    trace: TraceGuard | None = None
    publication: OutputPublication | None = None
    exit_code = 1
    try:
        logging_dir.mkdir(parents=True, exist_ok=True)
        try:
            trace = TraceGuard(trace_factory(trace_path, run_id))
            trace.write({"event_type": "batch_started", "case_id": None, "stage": "input_validation", "status": "started"})
        except Exception:
            trace = None
        if not data_dir.is_dir():
            report = InputValidationReport(run_id, _utc_now(), _utc_now(), False, {"expected_file_count": 50, "discovered_expected_file_count": 0, "valid_case_count": 0, "invalid_case_count": 50, "missing_file_count": 0, "unexpected_file_count": 0}, (_error("INPUT_DIRECTORY_UNAVAILABLE", f"data directory is unavailable: {data_dir}"),), (), (), {case_id: InputValidationCase(False, None, (), None) for case_id in EXPECTED_CASE_IDS})
            atomic_write_json(validation_path, report.to_dict())
            if trace is not None:
                trace.write({"event_type": "batch_failed", "case_id": None, "stage": "input_validation", "status": "blocked", "errors": ["data directory unavailable"]})
        else:
            try:
                repository = repository_factory(data_dir)
            except Exception as exc:
                report = InputValidationReport(run_id, _utc_now(), _utc_now(), False, {"expected_file_count": 50, "discovered_expected_file_count": 0, "valid_case_count": 0, "invalid_case_count": 50, "missing_file_count": 0, "unexpected_file_count": 0}, (_error("REPOSITORY_INITIALIZATION_FAILED", f"{type(exc).__name__}: {exc}"),), (), (), {case_id: InputValidationCase(False, None, (), None) for case_id in EXPECTED_CASE_IDS})
                atomic_write_json(validation_path, report.to_dict())
                if trace is not None:
                    trace.write({"event_type": "batch_failed", "case_id": None, "stage": "input_validation", "status": "blocked", "errors": ["repository initialization failed"]})
            else:
                report = validate_released_inputs(input_dir, repository, run_id=run_id)
                atomic_write_json(validation_path, report.to_dict())
                staging_dir = Path(tempfile.mkdtemp(prefix=f".k4-staging-{run_id}-", dir=root))
                results: list[RunResult] = []
                for case_id in EXPECTED_CASE_IDS:
                    entry = report.cases[case_id]
                    if not entry.valid or entry.ticket is None:
                        results.append(RunResult.blocked(case_id, entry.errors or (_error("INPUT_SCHEMA_INVALID", "case is not valid for processing"),)))
                        continue
                    try:
                        result = processor(entry.ticket, repository, trace)
                    except Exception as exc:
                        result = RunResult.blocked(case_id, (_error("UNEXPECTED_INTERNAL_ERROR", f"{type(exc).__name__}: {exc}", stage=Stage.ASSEMBLY),))
                    results.append(result)
                    if result.status is RunStatus.VERIFIED and result.output is not None:
                        atomic_write_json(staging_dir / f"{case_id}.json", result.output)
                success = report.success and len(results) == len(EXPECTED_CASE_IDS) and all(result.status is RunStatus.VERIFIED for result in results) and _audit_staging(staging_dir)
                if trace is not None:
                    trace.write({"event_type": "batch_finished", "case_id": None, "stage": "output_publish", "status": "prepared" if success else "blocked", "verified_count": sum(result.status is RunStatus.VERIFIED for result in results), "blocked_count": sum(result.status is RunStatus.BLOCKED for result in results)})
                if success and trace is not None and not trace.failed:
                    trace.write({"event_type": "output_publish", "case_id": None, "stage": "output_publish", "status": "prepared"})
                    publication = _publish_outputs(staging_dir, output_dir, root)
                    trace.finalize()
                    if not trace.failed:
                        publication.commit()
                        publication = None
                        exit_code = 0
                    else:
                        publication.rollback()
                        publication = None
    except Exception as exc:
        if trace is not None:
            trace.write({"event_type": "batch_fatal", "case_id": None, "stage": "output_publish", "status": "error", "errors": [f"{type(exc).__name__}: {exc}"]})
    finally:
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)
        if publication is not None:
            try:
                publication.rollback()
            except Exception:
                exit_code = 1
        if trace is not None:
            trace.finalize()
            if trace.failed:
                exit_code = 1
    return exit_code


def main() -> int:
    """Run the published K4 release from the repository root."""

    return run_batch(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
