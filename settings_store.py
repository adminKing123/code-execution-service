"""Runtime settings stored in SQLite; env vars seed defaults on first run."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from database import get_session
from models import AppSetting

KEY_EXECUTION_TIMEOUT_SECONDS = "execution_timeout_seconds"
KEY_MAX_CODE_BYTES = "max_code_bytes"

SETTING_FIELDS = (
    {
        "key": KEY_EXECUTION_TIMEOUT_SECONDS,
        "label": "Execution timeout (seconds)",
        "help": "Maximum wall time for a single code run.",
        "group": "execution",
        "type": "int",
        "env": "EXECUTION_TIMEOUT_SECONDS",
        "default": "30",
        "min": 5,
        "max": 600,
    },
    {
        "key": KEY_MAX_CODE_BYTES,
        "label": "Max code size (KB)",
        "help": "Reject requests when UTF-8 source exceeds this size.",
        "group": "execution",
        "type": "int",
        "env": "MAX_CODE_BYTES",
        "default": str(256 * 1024),
        "min": 1024,
        "max": 2 * 1024 * 1024,
        "display_unit": "kb",
    },
)

_FIELDS_BY_KEY = {f["key"]: f for f in SETTING_FIELDS}


def _env_fallback(field: dict) -> str:
    raw = os.getenv(field["env"])
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip()
    return field["default"]


def seed_settings() -> None:
    session = get_session()
    try:
        for field in SETTING_FIELDS:
            existing = session.get(AppSetting, field["key"])
            if existing:
                continue
            session.add(
                AppSetting(
                    key=field["key"],
                    value=_env_fallback(field),
                    updated_at=datetime.now(timezone.utc),
                )
            )
        session.commit()
    finally:
        session.close()


def _raw(key: str) -> str:
    field = _FIELDS_BY_KEY[key]
    session = get_session()
    try:
        row = session.get(AppSetting, key)
        if row and row.value is not None:
            return row.value.strip()
        return _env_fallback(field)
    finally:
        session.close()


def get_int(key: str) -> int:
    return int(_raw(key))


def execution_timeout_seconds() -> float:
    return float(get_int(KEY_EXECUTION_TIMEOUT_SECONDS))


def max_code_bytes() -> int:
    return get_int(KEY_MAX_CODE_BYTES)


def bytes_to_kb(value: int) -> float:
    return round(value / 1024, 1)


def kb_to_bytes(kb: float) -> int:
    return int(round(kb * 1024))


def admin_form_values() -> dict[str, float | int]:
    values: dict[str, float | int] = {}
    for field in SETTING_FIELDS:
        key = field["key"]
        if field["type"] == "int" and field.get("display_unit") == "kb":
            values[key] = bytes_to_kb(get_int(key))
        elif field["type"] == "int":
            values[key] = get_int(key)
    return values


def save_admin_form(form_data: dict) -> list[str]:
    errors: list[str] = []
    updates: dict[str, str] = {}

    for field in SETTING_FIELDS:
        key = field["key"]
        raw = form_data.get(key)
        if raw is None:
            errors.append(f"{field['label']} is required.")
            continue
        if field["type"] == "int":
            try:
                if field.get("display_unit") == "kb":
                    kb = float(raw)
                    value = kb_to_bytes(kb)
                else:
                    value = int(raw)
            except (TypeError, ValueError):
                errors.append(f"{field['label']} must be a number.")
                continue
            min_v = field.get("min")
            max_v = field.get("max")
            if min_v is not None and value < min_v:
                if field.get("display_unit") == "kb":
                    errors.append(f"{field['label']} must be at least {bytes_to_kb(min_v)} KB.")
                else:
                    errors.append(f"{field['label']} must be at least {min_v}.")
            if max_v is not None and value > max_v:
                if field.get("display_unit") == "kb":
                    errors.append(f"{field['label']} must be at most {bytes_to_kb(max_v)} KB.")
                else:
                    errors.append(f"{field['label']} must be at most {max_v}.")
            updates[key] = str(value)

    if errors:
        return errors

    session = get_session()
    try:
        now = datetime.now(timezone.utc)
        for key, value in updates.items():
            row = session.get(AppSetting, key)
            if row:
                row.value = value
                row.updated_at = now
            else:
                session.add(AppSetting(key=key, value=value, updated_at=now))
        session.commit()
    finally:
        session.close()

    return []
