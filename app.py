import secrets
from functools import wraps

from flask import Flask, jsonify, render_template, request

import config
import settings_store
from admin_routes import admin_bp
from code_executor import ExecutionValidationError, _normalize_stdin_lines, execute_code
from database import init_db
from execution_logging import create_execution_log, finalize_execution_log, record_failed_execution
from languages import list_languages

app = Flask(__name__)
app.config["SECRET_KEY"] = config.FLASK_SECRET_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 12
app.register_blueprint(admin_bp)
init_db()


def _extract_api_key() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    return request.headers.get("X-API-Key", "").strip() or None


def require_api_key(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        provided = _extract_api_key()
        if not provided or not secrets.compare_digest(provided, config.SERVICE_API_KEY):
            return jsonify({"error": "Unauthorized"}), 401
        return view(*args, **kwargs)

    return wrapped


def _client_ip() -> str | None:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


def _bool_field(data: dict, key: str) -> bool:
    if key not in data:
        return False
    value = data[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    raise TypeError(f"{key} must be a boolean")


def _validation_fail(data: dict | None, error: str):
    log_id = record_failed_execution(
        data=data, error_detail=error, error_type="validation", client_ip=_client_ip()
    )
    return jsonify({"ok": False, "error": error, "error_type": "validation", "log_id": log_id}), 400


@app.get("/docs")
def public_api_docs():
    return render_template(
        "api_docs.html",
        in_admin=False,
        max_code_kb=settings_store.bytes_to_kb(settings_store.max_code_bytes()),
        execution_timeout_seconds=settings_store.execution_timeout_seconds(),
        flask_host=config.FLASK_HOST,
        flask_port=config.FLASK_PORT,
    )


@app.get("/api/languages")
@require_api_key
def api_languages():
    return jsonify({"ok": True, "languages": list_languages()})


@app.post("/api/execute")
@require_api_key
def api_execute():
    data = request.get_json(silent=True)
    if not data:
        return _validation_fail(None, "JSON body required")

    try:
        language = str(data.get("language", "python")).strip().lower()
        code = data.get("code")
        if code is None:
            raise ExecutionValidationError("code is required")
        if not isinstance(code, str):
            raise TypeError("code must be a string")
        stdin_lines = _normalize_stdin_lines(data.get("stdin_lines"))
        include_logs = _bool_field(data, "include_logs") or _bool_field(data, "stream")
    except (TypeError, ExecutionValidationError) as exc:
        return _validation_fail(data, str(exc))

    client_ip = _client_ip()

    try:
        log_id, execution_id = create_execution_log(
            language=language,
            code=code,
            streamed=include_logs,
            client_ip=client_ip,
        )
        outcome = execute_code(
            language=language,
            code=code,
            execution_id=execution_id,
            stdin_lines=stdin_lines,
        )
    except ExecutionValidationError as exc:
        return _validation_fail(data, str(exc))

    finalize_execution_log(
        log_id,
        ok=outcome.ok,
        has_main=outcome.has_main,
        result=outcome.result if outcome.has_main else None,
        started_at=outcome.started_at,
        ended_at=outcome.ended_at,
        duration_ms=outcome.duration_ms,
        error_detail=outcome.detail or outcome.error,
        error_type=outcome.error_type,
        logs=outcome.logs,
        store_logs=include_logs and bool(outcome.logs),
    )

    body = outcome.to_response_dict(include_logs=include_logs)
    body["log_id"] = log_id
    status = 200 if outcome.ok else 422
    return jsonify(body), status


if __name__ == "__main__":
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=False, threaded=True)
