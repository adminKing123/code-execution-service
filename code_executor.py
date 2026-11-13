from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Generator, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import settings_store
from languages import get_language

RUNNER_PATH = Path(__file__).resolve().parent / "python_runner.py"


class ExecutionValidationError(ValueError):
    pass


@dataclass
class ExecutionOutcome:
    ok: bool
    language: str
    execution_id: str
    has_main: bool | None = None
    result: object = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    error_type: str | None = None
    detail: str | None = None
    exit_code: int | None = None
    logs: list[dict] | None = None

    def to_response_dict(self, *, include_logs: bool) -> dict:
        body: dict = {
            "ok": self.ok,
            "execution_id": self.execution_id,
            "language": self.language,
            "has_main": self.has_main,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
        }
        if self.ok:
            if self.has_main:
                body["result"] = self.result
        else:
            body["error"] = self.error or "execution_failed"
            if self.error_type:
                body["error_type"] = self.error_type
            if self.detail:
                body["detail"] = self.detail
            if self.exit_code is not None:
                body["exit_code"] = self.exit_code
        if include_logs and self.logs is not None:
            body["logs"] = self.logs
        return body


def _utc_request_started() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_execute_request(*, language: str, code: str) -> None:
    if not get_language(language):
        raise ExecutionValidationError(f"Unsupported language: {language}")
    if not isinstance(code, str) or not code.strip():
        raise ExecutionValidationError("code is required")
    encoded = code.encode("utf-8")
    max_bytes = settings_store.max_code_bytes()
    if len(encoded) > max_bytes:
        raise ExecutionValidationError(
            f"code exceeds max size of {max_bytes} bytes ({len(encoded)} bytes)"
        )


def _parse_protocol_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return {"type": "log", "stream": "stdout", "message": line}
    if not isinstance(payload, dict):
        return {"type": "log", "stream": "stdout", "message": line}
    return payload


def _run_python_subprocess(code: str) -> Iterable[tuple[str, dict | None]]:
    """
    Yields ("event", payload) for each protocol message from the child process.
    """
    with tempfile.TemporaryDirectory(prefix="code-exec-") as tmp:
        code_path = Path(tmp) / "user_code.py"
        code_path.write_text(code, encoding="utf-8")

        proc = subprocess.Popen(
            [sys.executable, str(RUNNER_PATH), str(code_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=tmp,
            env={
                **{k: v for k, v in __import__("os").environ.items()},
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

        assert proc.stdout is not None
        line_queue: queue.Queue[str | None] = queue.Queue()

        def _stdout_reader() -> None:
            try:
                for line in proc.stdout:
                    line_queue.put(line)
            finally:
                line_queue.put(None)

        threading.Thread(target=_stdout_reader, daemon=True).start()
        timeout = settings_store.execution_timeout_seconds()
        deadline = time.monotonic() + timeout
        timed_out = False

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = line_queue.get(timeout=min(0.5, remaining))
            except queue.Empty:
                if proc.poll() is not None:
                    continue
                continue
            if line is None:
                break
            yield ("protocol", _parse_protocol_line(line))

        if timed_out:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            yield (
                "timeout",
                {
                    "error": "timeout",
                    "detail": f"Execution exceeded {timeout}s",
                },
            )
            return

        proc.wait(timeout=5)
        stderr_tail = ""
        if proc.stderr is not None:
            stderr_tail = proc.stderr.read() or ""

        if proc.returncode not in (0, 1) and stderr_tail.strip():
            yield (
                "runner_crash",
                {
                    "error": "runner_failed",
                    "detail": stderr_tail.strip(),
                    "exit_code": proc.returncode,
                },
            )


def execute_code(
    *,
    language: str,
    code: str,
    execution_id: str | None = None,
) -> ExecutionOutcome:
    execution_id = execution_id or str(uuid.uuid4())
    validate_execute_request(language=language, code=code)

    logs: list[dict] = []
    started_at = _utc_request_started()
    ended_at: str | None = None
    duration_ms: int | None = None
    has_main: bool | None = None
    result = None

    for kind, payload in _run_python_subprocess(code):
        if kind == "protocol" and payload:
            if payload.get("type") == "log":
                logs.append(payload)
                continue
            if payload.get("type") == "complete":
                has_main = bool(payload.get("has_main"))
                result = payload.get("result")
                started_at = payload.get("started_at") or started_at
                ended_at = payload.get("ended_at")
                duration_ms = payload.get("duration_ms")
                return ExecutionOutcome(
                    ok=True,
                    language=language,
                    execution_id=execution_id,
                    has_main=has_main,
                    result=result if has_main else None,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=duration_ms,
                    logs=logs,
                )
            if payload.get("type") == "error":
                return ExecutionOutcome(
                    ok=False,
                    language=language,
                    execution_id=execution_id,
                    has_main=None,
                    started_at=payload.get("started_at") or started_at,
                    ended_at=payload.get("ended_at"),
                    duration_ms=payload.get("duration_ms"),
                    error=payload.get("error") or "execution_failed",
                    error_type=payload.get("error"),
                    detail=payload.get("detail"),
                    logs=logs,
                )
        if kind == "timeout":
            ended_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            return ExecutionOutcome(
                ok=False,
                language=language,
                execution_id=execution_id,
                started_at=started_at,
                ended_at=ended_at,
                error="timeout",
                error_type="timeout",
                detail=payload.get("detail") if payload else None,
                logs=logs,
            )
        if kind == "runner_crash":
            ended_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            return ExecutionOutcome(
                ok=False,
                language=language,
                execution_id=execution_id,
                started_at=started_at,
                ended_at=ended_at,
                error=payload.get("error") if payload else "runner_failed",
                error_type="runner_failed",
                detail=payload.get("detail") if payload else None,
                exit_code=payload.get("exit_code") if payload else None,
                logs=logs,
            )

    ended_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return ExecutionOutcome(
        ok=False,
        language=language,
        execution_id=execution_id,
        started_at=started_at,
        ended_at=ended_at,
        error="no_result",
        error_type="no_result",
        detail="Runner finished without a completion event",
        logs=logs,
    )


def stream_execute_code(
    *,
    language: str,
    code: str,
    execution_id: str | None = None,
) -> Generator[dict, None, None]:
    """
    Yields SSE payloads:
      {"event": "started", ...}
      {"event": "log", ...}
      {"event": "complete", ...} or {"event": "error", ...}
    """
    execution_id = execution_id or str(uuid.uuid4())
    try:
        validate_execute_request(language=language, code=code)
    except ExecutionValidationError as exc:
        yield {
            "event": "error",
            "data": {
                "ok": False,
                "execution_id": execution_id,
                "language": language,
                "error": str(exc),
                "error_type": "validation",
            },
        }
        return

    yield {
        "event": "started",
        "data": {
            "execution_id": execution_id,
            "language": language,
            "started_at": _utc_request_started(),
        },
    }

    for kind, payload in _run_python_subprocess(code):
        if kind == "protocol" and payload:
            if payload.get("type") == "log":
                yield {"event": "log", "data": payload}
                continue
            if payload.get("type") == "complete":
                outcome = ExecutionOutcome(
                    ok=True,
                    language=language,
                    execution_id=execution_id,
                    has_main=bool(payload.get("has_main")),
                    result=payload.get("result") if payload.get("has_main") else None,
                    started_at=payload.get("started_at"),
                    ended_at=payload.get("ended_at"),
                    duration_ms=payload.get("duration_ms"),
                )
                yield {
                    "event": "complete",
                    "data": outcome.to_response_dict(include_logs=False),
                }
                return
            if payload.get("type") == "error":
                outcome = ExecutionOutcome(
                    ok=False,
                    language=language,
                    execution_id=execution_id,
                    started_at=payload.get("started_at"),
                    ended_at=payload.get("ended_at"),
                    duration_ms=payload.get("duration_ms"),
                    error=payload.get("error"),
                    error_type=payload.get("error"),
                    detail=payload.get("detail"),
                )
                yield {
                    "event": "error",
                    "data": outcome.to_response_dict(include_logs=False),
                }
                return

        if kind == "timeout":
            yield {
                "event": "error",
                "data": {
                    "ok": False,
                    "execution_id": execution_id,
                    "language": language,
                    "error": "timeout",
                    "error_type": "timeout",
                    "detail": (payload or {}).get("detail"),
                },
            }
            return

        if kind == "runner_crash":
            yield {
                "event": "error",
                "data": {
                    "ok": False,
                    "execution_id": execution_id,
                    "language": language,
                    "error": (payload or {}).get("error", "runner_failed"),
                    "error_type": "runner_failed",
                    "detail": (payload or {}).get("detail"),
                    "exit_code": (payload or {}).get("exit_code"),
                },
            }
            return

    yield {
        "event": "error",
        "data": {
            "ok": False,
            "execution_id": execution_id,
            "language": language,
            "error": "no_result",
            "error_type": "no_result",
            "detail": "Runner finished without a completion event",
        },
    }
