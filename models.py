import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CodeExecutionLog(Base):
    __tablename__ = "code_execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    execution_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    code_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    has_main: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    streamed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    logs_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ended_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @staticmethod
    def result_to_json(value) -> str | None:
        if value is None:
            return None
        return json.dumps(value, default=str)

    def result_value(self):
        if not self.result_json:
            return None
        return json.loads(self.result_json)

    def logs_list(self) -> list:
        if not self.logs_json:
            return []
        try:
            return json.loads(self.logs_json)
        except json.JSONDecodeError:
            return []


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
