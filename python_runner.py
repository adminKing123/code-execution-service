"""
Subprocess entrypoint: reads user code from argv[1], optional stdin lines JSON from argv[2].
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from typing import TextIO

from python_guardrails import GuardrailViolation, safe_globals, validate_python_source


def _emit(payload: dict) -> None:
    sys.__stdout__.write(json.dumps(payload, default=str) + "\n")
    sys.__stdout__.flush()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _StreamForwarder:
    def __init__(self, stream_name: str, underlying: TextIO):
        self._stream_name = stream_name
        self._underlying = underlying
        self._buffer = ""

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                _emit(
                    {
                        "type": "log",
                        "stream": self._stream_name,
                        "message": line,
                        "timestamp": _utc_now_iso(),
                    }
                )
        return len(data)

    def flush(self) -> None:
        if self._buffer:
            _emit(
                {
                    "type": "log",
                    "stream": self._stream_name,
                    "message": self._buffer,
                    "timestamp": _utc_now_iso(),
                }
            )
            self._buffer = ""
        self._underlying.flush()

    def isatty(self) -> bool:
        return False


def _json_safe(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _load_stdin_lines(path: str | None) -> list[str]:
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardrailViolation(f"Invalid stdin_lines file: {exc}") from exc
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise GuardrailViolation("stdin_lines must be a JSON array of strings")
    return [str(item) for item in raw]


def main() -> int:
    if len(sys.argv) < 2:
        _emit({"type": "error", "error": "runner_misconfigured", "detail": "code path required"})
        return 2

    code_path = sys.argv[1]
    stdin_path = sys.argv[2] if len(sys.argv) > 2 else None
    started_at = _utc_now_iso()
    t0 = datetime.now(timezone.utc)

    try:
        with open(code_path, encoding="utf-8") as handle:
            source = handle.read()
        stdin_lines = _load_stdin_lines(stdin_path)
        validate_python_source(source)
    except GuardrailViolation as exc:
        _emit(
            {
                "type": "error",
                "error": "guardrail_violation",
                "detail": str(exc),
                "started_at": started_at,
                "ended_at": _utc_now_iso(),
                "duration_ms": int((datetime.now(timezone.utc) - t0).total_seconds() * 1000),
            }
        )
        return 1
    except OSError as exc:
        _emit(
            {
                "type": "error",
                "error": "read_failed",
                "detail": str(exc),
                "started_at": started_at,
                "ended_at": _utc_now_iso(),
            }
        )
        return 1

    protocol_out = sys.stdout
    sys.stdout = _StreamForwarder("stdout", protocol_out)
    sys.stderr = _StreamForwarder("stderr", protocol_out)

    namespace = safe_globals(stdin_lines=stdin_lines)

    try:
        compiled = compile(source, "<user_code>", "exec")
    except SyntaxError as exc:
        sys.stdout.flush()
        sys.stderr.flush()
        _emit(
            {
                "type": "error",
                "error": "syntax_error",
                "detail": "".join(traceback.format_exception_only(exc)).strip(),
                "lineno": exc.lineno,
                "offset": exc.offset,
                "text": exc.text,
                "started_at": started_at,
                "ended_at": _utc_now_iso(),
                "duration_ms": int((datetime.now(timezone.utc) - t0).total_seconds() * 1000),
            }
        )
        return 1

    try:
        exec(compiled, namespace)  # noqa: S102
    except Exception as exc:
        sys.stdout.flush()
        sys.stderr.flush()
        _emit(
            {
                "type": "error",
                "error": "runtime_error",
                "detail": "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                ).strip(),
                "started_at": started_at,
                "ended_at": _utc_now_iso(),
                "duration_ms": int((datetime.now(timezone.utc) - t0).total_seconds() * 1000),
            }
        )
        return 1

    sys.stdout.flush()
    sys.stderr.flush()

    has_main = callable(namespace.get("main"))
    result_present = False
    result = None
    if has_main:
        try:
            result = namespace["main"]()
            result_present = True
        except Exception as exc:
            _emit(
                {
                    "type": "error",
                    "error": "main_failed",
                    "detail": "".join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__)
                    ).strip(),
                    "started_at": started_at,
                    "ended_at": _utc_now_iso(),
                    "duration_ms": int((datetime.now(timezone.utc) - t0).total_seconds() * 1000),
                }
            )
            return 1

    ended_at = _utc_now_iso()
    duration_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)

    complete_payload: dict = {
        "type": "complete",
        "has_main": has_main,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": duration_ms,
    }
    if result_present:
        complete_payload["result"] = _json_safe(result)
    _emit(complete_payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
