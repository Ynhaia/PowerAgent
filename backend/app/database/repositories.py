"""数据库访问的仓储契约。

具体 PostgreSQL 实现在 ``postgres.py``。服务层不得直接拼接 SQL；读写权限与审计策略
必须由部署角色和仓储方法共同限制。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..domain.schemas import AlertEvent, TelemetryPoint, TraceEvent, UnitSummary


class PlantDataRepository(Protocol):
    """只读机组、测点字典、时序与预警数据的仓储契约。"""

    def fetch_units(self) -> list[UnitSummary]: ...

    def fetch_tag_codes(self) -> list[str]: ...

    def fetch_tag_definition(self, tag_code: str) -> dict[str, object]: ...

    def fetch_telemetry(
        self, unit_id: str, tag_codes: list[str], start_time: datetime, end_time: datetime
    ) -> dict[str, list[TelemetryPoint]]: ...

    def fetch_alerts(self, unit_id: str, start_time: datetime, end_time: datetime) -> list[AlertEvent]: ...


class TraceRepository(Protocol):
    """Trace 的仅追加审计契约。"""

    def append_event(self, event: TraceEvent, user_id: str = "agent-api", unit_scope: list[str] | None = None) -> None: ...

    def fetch_events(self, trace_id: str) -> list[TraceEvent]: ...
