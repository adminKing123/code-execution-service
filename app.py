import json
import secrets
from functools import wraps

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import config
import settings_store
from admin_routes import admin_bp
from code_executor import ExecutionValidationError, execute_code, stream_execute_code
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


def _parse_stream_flag(data: dict | None) -> bool:
    if data and "stream" in data:
        value = data["stream"]
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        raise TypeError("stream must be a boolean")
    if request.args.get("stream", "").lower() in ("1", "true", "yes", "on"):
        return True
    accept = request.headers.get("Accept", "")
    return "text/event-stream" in accept


def _sse_format(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


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
        stream = _parse_stream_flag(data)
        include_logs = bool(data.get("include_logs", False))
    except (TypeError, ExecutionValidationError) as exc:
        return _validation_fail(data, str(exc))

    client_ip = _client_ip()

    if stream:
        try:
            log_id, execution_id = create_execution_log(
                language=language,
                code=code,
                streamed=True,
                client_ip=client_ip,
            )
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc), "error_type": "internal"}), 500

        @stream_with_context
        def event_stream():
            collected_logs: list[dict] = []
            final_payload: dict | None = None
            final_ok = False

            try:
                for item in stream_execute_code(
                    language=language, code=code, execution_id=execution_id
                ):
                    event = item["event"]
                    payload = dict(item["data"])

                    if event == "started":
                        payload["log_id"] = log_id
                    if event == "log":
                        collected_logs.append(payload)
                    if event in ("complete", "error"):
                        payload["log_id"] = log_id
                        final_payload = payload
                        final_ok = event == "complete" and payload.get("ok") is not False

                    yield _sse_format(event, payload)
            finally:
                if final_payload:
                    finalize_execution_log(
                        log_id,
                        ok=final_ok,
                        has_main=final_payload.get("has_main"),
                        result=final_payload.get("result"),
                        started_at=final_payload.get("started_at"),
                        ended_at=final_payload.get("ended_at"),
                        duration_ms=final_payload.get("duration_ms"),
                        error_detail=final_payload.get("error") or final_payload.get("detail"),
                        error_type=final_payload.get("error_type") or final_payload.get("error"),
                        logs=collected_logs,
                        store_logs=bool(collected_logs),
                    )
                else:
                    finalize_execution_log(
                        log_id,
                        ok=False,
                        error_detail="Stream ended without completion",
                        error_type="stream_incomplete",
                        logs=collected_logs,
                        store_logs=bool(collected_logs),
                    )

        return Response(
            event_stream(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        log_id, execution_id = create_execution_log(
            language=language,
            code=code,
            streamed=False,
            client_ip=client_ip,
        )
        outcome = execute_code(language=language, code=code, execution_id=execution_id)
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
