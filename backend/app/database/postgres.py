"""PostgreSQL 仓储实现。

PlantDataRepository 只包含参数化 SELECT，供 postgres 数据源读取测点、预警和字典。
TraceRepository 只向 execution_trace 追加审计事件，并确保会话存在。生产部署应使用两个
不同数据库角色：只读数据角色和仅可写 conversation_session/execution_trace 的审计角色。
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable, Protocol

import psycopg
from psycopg.rows import dict_row

from ..domain.schemas import AlertEvent, TelemetryPoint, TraceEvent, UnitSummary


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[object, ...] | list[object] | None = None) -> None: ...

    def fetchall(self) -> list[dict[str, Any]]: ...

    def __enter__(self) -> "Cursor": ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str], Connection]


class PowerAgentDatabaseError(RuntimeError):
    """数据库不可用、结构不匹配或数据无法转换时使用的统一异常。"""


def default_connection_factory(database_url: str) -> Connection:
    return psycopg.connect(database_url, row_factory=dict_row)  # type: ignore[return-value]


class PostgresPlantDataRepository:
    """只读数据库仓储。所有值均通过 psycopg 参数绑定，禁止拼接用户输入 SQL。"""

    def __init__(self, database_url: str, connection_factory: ConnectionFactory = default_connection_factory) -> None:
        if not database_url:
            raise ValueError("POWERAGENT_DATABASE_URL 未配置")
        self.database_url = database_url
        self.connection_factory = connection_factory

    def fetch_units(self) -> list[UnitSummary]:
        rows = self._fetchall(
            "SELECT unit_id, unit_name, unit_type, rated_capacity_mw, status "
            "FROM plant_unit ORDER BY unit_id"
        )
        try:
            return [UnitSummary.model_validate(row) for row in rows]
        except Exception as exc:
            raise PowerAgentDatabaseError("plant_unit 数据不符合 PowerAgent 契约") from exc

    def fetch_tag_codes(self) -> list[str]:
        rows = self._fetchall("SELECT tag_code FROM tag_definition ORDER BY tag_code")
        return [str(row["tag_code"]).upper() for row in rows]

    def fetch_tag_definition(self, tag_code: str) -> dict[str, object]:
        rows = self._fetchall(
            "SELECT tag_name, unit_name, warning_low, warning_high, alarm_low, alarm_high, setpoint_value "
            "FROM tag_definition WHERE tag_code = %s",
            (tag_code.upper(),),
        )
        if not rows:
            raise ValueError(f"未知测点：{tag_code}")
        row = rows[0]
        definition: dict[str, object] = {"name": str(row["tag_name"]), "unit": str(row["unit_name"] or "")}
        for key in ("warning_low", "warning_high", "alarm_low", "alarm_high", "setpoint_value"):
            if row.get(key) is not None:
                definition[key] = float(row[key])
        return definition

    def fetch_telemetry(
        self,
        unit_id: str,
        tag_codes: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[TelemetryPoint]]:
        if not tag_codes:
            return {}
        rows = self._fetchall(
            "SELECT tag_code, ts AS timestamp, value, quality_code "
            "FROM telemetry_point "
            "WHERE unit_id = %s AND tag_code = ANY(%s) AND ts >= %s AND ts <= %s "
            "ORDER BY tag_code, ts",
            (unit_id, [tag.upper() for tag in tag_codes], start_time, end_time),
        )
        grouped: dict[str, list[TelemetryPoint]] = {tag.upper(): [] for tag in tag_codes}
        try:
            for row in rows:
                tag_code = str(row["tag_code"]).upper()
                grouped.setdefault(tag_code, []).append(TelemetryPoint.model_validate(row))
        except Exception as exc:
            raise PowerAgentDatabaseError("telemetry_point 数据不符合 PowerAgent 契约") from exc
        missing = [tag for tag, points in grouped.items() if not points]
        if missing:
            raise ValueError(f"指定时间窗口没有测点数据：{', '.join(missing)}")
        return grouped

    def fetch_alerts(self, unit_id: str, start_time: datetime, end_time: datetime) -> list[AlertEvent]:
        rows = self._fetchall(
            "SELECT alert_id::text AS alert_id, unit_id, tag_code, alert_type, severity, start_time, end_time, "
            "message, observed_value, threshold_value, related_alert_ids "
            "FROM alert_event "
            "WHERE unit_id = %s AND start_time <= %s AND (end_time IS NULL OR end_time >= %s) "
            "ORDER BY start_time DESC",
            (unit_id, end_time, start_time),
        )
        try:
            return [AlertEvent.model_validate(row) for row in rows]
        except Exception as exc:
            raise PowerAgentDatabaseError("alert_event 数据不符合 PowerAgent 契约") from exc

    def _fetchall(self, query: str, params: tuple[object, ...] | None = None) -> list[dict[str, Any]]:
        try:
            connection = self.connection_factory(self.database_url)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchall()
            finally:
                connection.close()
        except PowerAgentDatabaseError:
            raise
        except Exception as exc:
            raise PowerAgentDatabaseError("PostgreSQL 只读查询失败") from exc


class PostgresTraceRepository:
    """execution_trace 的仅追加仓储。"""

    def __init__(self, database_url: str, connection_factory: ConnectionFactory = default_connection_factory) -> None:
        if not database_url:
            raise ValueError("POWERAGENT_DATABASE_URL 未配置")
        self.database_url = database_url
        self.connection_factory = connection_factory

    def append_event(
        self,
        event: TraceEvent,
        user_id: str = "agent-api",
        unit_scope: list[str] | None = None,
    ) -> None:
        session_query = (
            "INSERT INTO conversation_session(session_id, user_id, unit_scope) VALUES (%s::uuid, %s, %s::jsonb) "
            "ON CONFLICT(session_id) DO UPDATE SET last_active_at = NOW()"
        )
        trace_query = (
            "INSERT INTO execution_trace(trace_id, request_id, session_id, user_id, node_name, status, "
            "output_payload, started_at, ended_at) "
            "VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s::jsonb, %s, %s)"
        )
        try:
            connection = self.connection_factory(self.database_url)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(session_query, (event.session_id, user_id, json.dumps(unit_scope or [])))
                    cursor.execute(
                        trace_query,
                        (
                            event.trace_id,
                            event.request_id,
                            event.session_id,
                            user_id,
                            event.node_name,
                            event.status,
                            json.dumps(event.payload, ensure_ascii=False),
                            event.created_at,
                            event.created_at,
                        ),
                    )
                connection.commit()
            finally:
                connection.close()
        except Exception as exc:
            raise PowerAgentDatabaseError("execution_trace 写入失败") from exc

    def fetch_events(self, trace_id: str) -> list[TraceEvent]:
        query = (
            "SELECT trace_id::text AS trace_id, request_id::text AS request_id, session_id::text AS session_id, "
            "node_name, status, output_payload AS payload, started_at AS created_at "
            "FROM execution_trace WHERE trace_id = %s::uuid ORDER BY trace_event_id"
        )
        try:
            connection = self.connection_factory(self.database_url)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(query, (trace_id,))
                    rows = cursor.fetchall()
            finally:
                connection.close()
            return [TraceEvent.model_validate(row) for row in rows]
        except Exception as exc:
            raise PowerAgentDatabaseError("execution_trace 读取失败") from exc


class PostgresPlantDataAdapter:
    """将 PostgreSQL 只读仓储适配为 PlantDataAdapter。"""

    def __init__(self, repository: PostgresPlantDataRepository) -> None:
        self.repository = repository

    def list_units(self) -> list[UnitSummary]:
        return self.repository.fetch_units()

    def list_tag_codes(self) -> list[str]:
        return self.repository.fetch_tag_codes()

    def tag_definition(self, tag_code: str) -> dict[str, object]:
        return self.repository.fetch_tag_definition(tag_code)

    def get_telemetry(self, unit_id: str, tag_codes: list[str], start_time: datetime, end_time: datetime) -> dict[str, list[TelemetryPoint]]:
        return self.repository.fetch_telemetry(unit_id, tag_codes, start_time, end_time)

    def get_alerts(self, unit_id: str, start_time: datetime, end_time: datetime) -> list[AlertEvent]:
        return self.repository.fetch_alerts(unit_id, start_time, end_time)

    def default_end_time(self) -> datetime:
        return datetime.now(timezone.utc).astimezone()
