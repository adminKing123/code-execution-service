import json
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlencode

from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from sqlalchemy import func, select

from database import get_session
from models import CodeExecutionLog

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

PER_PAGE = 25
SORT_OPTIONS = {
    "created_desc": ("Newest first", CodeExecutionLog.created_at.desc()),
    "created_asc": ("Oldest first", CodeExecutionLog.created_at.asc()),
    "duration_desc": ("Duration (high → low)", CodeExecutionLog.duration_ms.desc().nulls_last()),
    "duration_asc": ("Duration (low → high)", CodeExecutionLog.duration_ms.asc().nulls_last()),
}


def _admin_configured() -> bool:
    from config import ADMIN_PASSWORD, FLASK_SECRET_KEY

    return bool(ADMIN_PASSWORD and FLASK_SECRET_KEY)


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _admin_configured():
            abort(404)
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def _parse_date(value: str, *, end_of_day: bool = False) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        if len(value) == 10:
            dt = datetime.strptime(value, "%Y-%m-%d")
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return dt.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _log_filters_from_request() -> dict:
    sort = request.args.get("sort", "created_desc")
    if sort not in SORT_OPTIONS:
        sort = "created_desc"
    status = request.args.get("status", "all").lower()
    if status not in ("all", "success", "failed"):
        status = "all"
    language = request.args.get("language", "all").lower()
    if language not in ("all", "python", "javascript"):
        language = "all"
    return {
        "status": status,
        "language": language,
        "code_q": request.args.get("code_q", "").strip(),
        "execution_id": request.args.get("execution_id", "").strip(),
        "date_from": request.args.get("date_from", "").strip(),
        "date_to": request.args.get("date_to", "").strip(),
        "sort": sort,
    }


def _apply_log_filters(stmt, filters: dict):
    if filters["status"] != "all":
        stmt = stmt.where(CodeExecutionLog.status == filters["status"])
    if filters["language"] != "all":
        stmt = stmt.where(CodeExecutionLog.language == filters["language"])
    if filters["code_q"]:
        stmt = stmt.where(CodeExecutionLog.code.ilike(f"%{filters['code_q']}%"))
    if filters["execution_id"]:
        stmt = stmt.where(CodeExecutionLog.execution_id.ilike(f"%{filters['execution_id']}%"))
    date_from = _parse_date(filters["date_from"])
    date_to = _parse_date(filters["date_to"], end_of_day=True)
    if date_from:
        stmt = stmt.where(CodeExecutionLog.created_at >= date_from)
    if date_to:
        stmt = stmt.where(CodeExecutionLog.created_at <= date_to)
    return stmt


def build_logs_query_string(filters: dict, *, page: int | None = None, sort: str | None = None) -> str:
    params: dict[str, str | int] = {}
    for key in ("code_q", "execution_id", "date_from", "date_to"):
        val = (filters.get(key) or "").strip()
        if val:
            params[key] = val
    status = filters.get("status", "all")
    if status and status != "all":
        params["status"] = status
    language = filters.get("language", "all")
    if language and language != "all":
        params["language"] = language
    sort_val = sort if sort is not None else filters.get("sort", "created_desc")
    if sort_val and sort_val != "created_desc":
        params["sort"] = sort_val
    if page and page > 1:
        params["page"] = page
    return urlencode(params)


@admin_bp.app_context_processor
def _admin_template_globals():
    return {
        "build_logs_query_string": build_logs_query_string,
        "filters_with": filters_with,
    }


def _pagination_range(page: int, pages: int) -> list[int | None]:
    if pages <= 1:
        return [1]
    if pages <= 7:
        return list(range(1, pages + 1))
    nums: list[int | None] = [1]
    if page > 4:
        nums.append(None)
    start = max(2, page - 2)
    end = min(pages - 1, page + 2)
    for n in range(start, end + 1):
        if n not in nums:
            nums.append(n)
    if page < pages - 3:
        nums.append(None)
    if pages not in nums:
        nums.append(pages)
    return nums


def filters_with(**overrides) -> dict:
    base = _log_filters_from_request()
    base.update(overrides)
    return base


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if not _admin_configured():
        abort(404)

    error = None
    if request.method == "POST":
        from config import ADMIN_PASSWORD
        import secrets

        password = request.form.get("password", "")
        if secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["admin_authenticated"] = True
            session.permanent = True
            dest = request.args.get("next") or url_for("admin.dashboard")
            if not dest.startswith("/admin"):
                dest = url_for("admin.dashboard")
            return redirect(dest)
        error = "Invalid password."

    return render_template("admin_login.html", error=error)


@admin_bp.post("/logout")
@require_admin
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@admin_bp.get("/")
@require_admin
def dashboard():
    filters = _log_filters_from_request()
    page = max(1, request.args.get("page", 1, type=int))

    db = get_session()
    try:
        count_stmt = _apply_log_filters(select(func.count(CodeExecutionLog.id)), filters)
        list_stmt = _apply_log_filters(select(CodeExecutionLog), filters)

        total = db.scalar(count_stmt) or 0
        success_total = db.scalar(
            select(func.count(CodeExecutionLog.id)).where(CodeExecutionLog.status == "success")
        )
        failed_total = db.scalar(
            select(func.count(CodeExecutionLog.id)).where(CodeExecutionLog.status == "failed")
        )

        pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        page = min(max(1, page), pages)

        _, order = SORT_OPTIONS[filters["sort"]]
        logs = db.scalars(
            list_stmt.order_by(order).offset((page - 1) * PER_PAGE).limit(PER_PAGE)
        ).all()

        rows = [_log_row(log) for log in logs]
    finally:
        db.close()

    page_numbers = _pagination_range(page, pages)
    qs = build_logs_query_string(filters)

    return render_template(
        "admin_dashboard.html",
        logs=rows,
        page=page,
        pages=pages,
        page_numbers=page_numbers,
        total=total,
        filters=filters,
        query_string=qs,
        status=filters["status"],
        sort=filters["sort"],
        sort_options=SORT_OPTIONS,
        success_total=success_total or 0,
        failed_total=failed_total or 0,
        per_page=PER_PAGE,
    )


@admin_bp.get("/logs/<int:log_id>")
@require_admin
def log_detail(log_id: int):
    db = get_session()
    try:
        log = db.get(CodeExecutionLog, log_id)
        if not log:
            abort(404)
        row = _log_row(log, include_code=True)
    finally:
        db.close()

    return render_template("admin_log_detail.html", log=row)


@admin_bp.get("/test")
@require_admin
def test_execute():
    import config
    import settings_store

    return render_template(
        "admin_test.html",
        api_key=config.SERVICE_API_KEY,
        api_key_masked=_mask_secret(config.SERVICE_API_KEY),
        api_url="/api/execute",
        max_code_kb=settings_store.bytes_to_kb(settings_store.max_code_bytes()),
        execution_timeout_seconds=settings_store.execution_timeout_seconds(),
    )


@admin_bp.route("/settings", methods=["GET", "POST"])
@require_admin
def settings_page():
    import settings_store

    errors: list[str] = []
    saved = False
    if request.method == "POST":
        errors = settings_store.save_admin_form(request.form)
        saved = not errors

    values = settings_store.admin_form_values()
    return render_template(
        "admin_settings.html",
        values=values,
        fields=settings_store.SETTING_FIELDS,
        errors=errors,
        saved=saved,
    )


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "••••••••"
    visible = 4
    middle = max(4, len(value) - visible * 2)
    return f"{value[:visible]}{'•' * middle}{value[-visible:]}"


def _log_row(log: CodeExecutionLog, include_code: bool = False) -> dict:
    created = log.created_at
    if created.tzinfo is None:
        created_display = created.isoformat(sep=" ", timespec="seconds")
    else:
        created_display = created.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    result = None
    if log.result_json:
        try:
            result = json.loads(log.result_json)
        except json.JSONDecodeError:
            result = log.result_json

    row = {
        "id": log.id,
        "created_at": created_display,
        "status": log.status,
        "language": log.language,
        "execution_id": log.execution_id,
        "execution_id_short": _preview(log.execution_id, 12),
        "has_main": log.has_main,
        "result": result,
        "result_preview": _preview(json.dumps(result, default=str) if result is not None else "", 48),
        "streamed": log.streamed,
        "error_detail": log.error_detail,
        "error_type": log.error_type,
        "client_ip": log.client_ip,
        "duration_ms": log.duration_ms,
        "duration_display": _format_duration_seconds(log.duration_ms),
        "code_preview": _preview(log.code.replace("\n", " "), 80),
        "code_bytes": log.code_bytes,
        "started_at": log.started_at,
        "ended_at": log.ended_at,
        "logs": log.logs_list(),
        "created_at_short": _short_timestamp(created_display),
    }
    if include_code:
        row["code"] = log.code
    return row


def _preview(text: str, length: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= length:
        return compact
    return compact[: length - 1] + "…"


def _format_duration_seconds(duration_ms: int | None) -> str | None:
    if duration_ms is None:
        return None
    seconds = duration_ms / 1000
    if seconds < 10:
        return f"{seconds:.2f} s"
    return f"{seconds:.1f} s"


def _short_timestamp(display: str) -> str:
    parts = display.split()
    if len(parts) >= 2 and len(parts[0]) >= 10 and "-" in parts[0]:
        return f"{parts[0][5:]} {parts[1][:5]}"
    return display
