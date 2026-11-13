import json
import uuid
from typing import Any

from database import get_session
from models import CodeExecutionLog


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def fields_from_request(data: dict | None) -> dict[str, Any]:
    if not data:
        return {
            "language": "python",
            "code": "",
            "streamed": False,
        }
    return {
        "language": str(data.get("language", "python")).strip().lower() or "python",
        "code": str(data.get("code", "")) if data.get("code") is not None else "",
        "streamed": bool(data.get("stream")),
    }


def create_execution_log(
    *,
    language: str,
    code: str,
    streamed: bool,
    client_ip: str | None,
    execution_id: str | None = None,
) -> tuple[int, str]:
    execution_id = execution_id or str(uuid.uuid4())
    encoded_len = len(code.encode("utf-8"))

    session = get_session()
    try:
        log = CodeExecutionLog(
            status="failed",
            execution_id=execution_id,
            language=language,
            code=code,
            code_bytes=encoded_len,
            streamed=streamed,
            client_ip=client_ip,
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        return log.id, execution_id
    finally:
        session.close()


def finalize_execution_log(
    log_id: int,
    *,
    ok: bool,
    has_main: bool | None = None,
    result=None,
    started_at: str | None = None,
    ended_at: str | None = None,
    duration_ms: int | None = None,
    error_detail: str | None = None,
    error_type: str | None = None,
    logs: list | None = None,
    store_logs: bool = False,
) -> None:
    session = get_session()
    try:
        log = session.get(CodeExecutionLog, log_id)
        if not log:
            return
        log.status = "success" if ok else "failed"
        log.has_main = has_main
        log.result_json = CodeExecutionLog.result_to_json(result) if ok and has_main else None
        log.started_at = started_at
        log.ended_at = ended_at
        log.duration_ms = duration_ms
        log.error_detail = error_detail
        log.error_type = error_type
        if store_logs and logs:
            log.logs_json = json.dumps(logs, default=str)
        session.commit()
    finally:
        session.close()


def record_failed_execution(
    *,
    error_detail: str,
    client_ip: str | None,
    data: dict | None = None,
    error_type: str = "validation",
    **overrides: Any,
) -> int:
    fields = fields_from_request(data)
    fields.update(overrides)
    code = fields["code"] or "(no code)"
    execution_id = overrides.get("execution_id") or str(uuid.uuid4())

    session = get_session()
    try:
        log = CodeExecutionLog(
            status="failed",
            execution_id=execution_id,
            language=fields["language"],
            code=code,
            code_bytes=len(code.encode("utf-8")),
            streamed=fields["streamed"],
            error_detail=error_detail,
            error_type=error_type,
            client_ip=client_ip,
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        return log.id
    finally:
        session.close()
