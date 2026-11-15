"""PostgreSQL 仓储的离线单元测试。

测试使用伪连接，不会读取 .env，也不会发起任何数据库网络连接。
"""

from datetime import datetime, timezone
import unittest

from backend.app.adapters.factory import create_plant_data_adapter
from backend.app.core.config import Settings
from backend.app.database.postgres import (
    PostgresPlantDataAdapter,
    PostgresPlantDataRepository,
    PostgresTraceRepository,
)
from backend.app.domain.schemas import TraceEvent
from backend.app.services.trace_service import InMemoryTraceService, PostgresTraceService
from backend.app.agent.service import AgentService


class FakeCursor:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, object]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, query: str, params=None) -> None:
        self.calls.append((query, params))

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class FakeConnection:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.cursor_instance = FakeCursor(rows)
        self.committed = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


class PostgresRepositoryTests(unittest.TestCase):
    def test_plant_repository_uses_parameterized_select_only(self) -> None:
        injected_unit = "U01' OR 1=1 --"
        injected_tag = "COND_ABS_PRESSURE' OR 1=1 --"
        connection = FakeConnection(
            [
                {
                    "tag_code": injected_tag.upper(),
                    "timestamp": datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc),
                    "value": 5.86,
                    "quality_code": "GOOD",
                }
            ]
        )
        repository = PostgresPlantDataRepository("postgresql://offline-test", lambda _: connection)

        result = repository.fetch_telemetry(
            injected_unit,
            [injected_tag],
            datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc),
        )

        self.assertIn(injected_tag.upper(), result)
        query, params = connection.cursor_instance.calls[0]
        self.assertTrue(query.lstrip().upper().startswith("SELECT"))
        self.assertNotIn(injected_unit, query)
        self.assertNotIn(injected_tag, query)
        self.assertEqual(params[0], injected_unit)
        self.assertEqual(params[1], [injected_tag.upper()])
        self.assertFalse(connection.committed)
        self.assertTrue(connection.closed)

    def test_adapter_converts_rows_to_domain_contract(self) -> None:
        connection = FakeConnection(
            [
                {
                    "unit_id": "U01",
                    "unit_name": "1 号机组",
                    "unit_type": "thermal",
                    "rated_capacity_mw": 660,
                    "status": "running",
                }
            ]
        )
        adapter = PostgresPlantDataAdapter(PostgresPlantDataRepository("postgresql://offline-test", lambda _: connection))

        units = adapter.list_units()

        self.assertEqual(units[0].unit_id, "U01")
        self.assertEqual(units[0].rated_capacity_mw, 660)
        self.assertTrue(connection.cursor_instance.calls[0][0].lstrip().upper().startswith("SELECT"))

    def test_trace_repository_upserts_session_then_appends_and_commits(self) -> None:
        connection = FakeConnection()
        repository = PostgresTraceRepository("postgresql://offline-test", lambda _: connection)
        event = TraceEvent(
            trace_id="5f13dd5d-cfbf-4c2e-b6e6-a73cf5ec164e",
            request_id="6e23dd5d-cfbf-4c2e-b6e6-a73cf5ec164e",
            session_id="7e33dd5d-cfbf-4c2e-b6e6-a73cf5ec164e",
            node_name="diagnose",
            status="succeeded",
            payload={"primary_cause": "cw_flow_insufficient"},
            created_at=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc),
        )

        repository.append_event(event, unit_scope=["U01"])

        calls = connection.cursor_instance.calls
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][0].lstrip().upper().startswith("INSERT INTO CONVERSATION_SESSION"))
        self.assertIn("ON CONFLICT", calls[0][0])
        self.assertTrue(calls[1][0].lstrip().upper().startswith("INSERT INTO EXECUTION_TRACE"))
        self.assertEqual(calls[1][1][0], event.trace_id)
        self.assertTrue(connection.committed)
        self.assertTrue(connection.closed)

    def test_postgres_trace_service_keeps_trace_service_contract(self) -> None:
        connection = FakeConnection()
        service = PostgresTraceService(PostgresTraceRepository("postgresql://offline-test", lambda _: connection))

        service.append(
            trace_id="5f13dd5d-cfbf-4c2e-b6e6-a73cf5ec164e",
            request_id="6e23dd5d-cfbf-4c2e-b6e6-a73cf5ec164e",
            session_id="7e33dd5d-cfbf-4c2e-b6e6-a73cf5ec164e",
            node_name="plan",
            status="succeeded",
        )

        self.assertTrue(connection.committed)
        self.assertEqual(connection.cursor_instance.calls[1][1][4], "plan")

    def test_postgres_mode_requires_valid_database_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "POWERAGENT_DATA_SOURCE=postgres"):
            create_plant_data_adapter(Settings(data_source="postgres", database_url=None))
        with self.assertRaisesRegex(ValueError, "POWERAGENT_TRACE_STORE=postgres"):
            AgentService(create_plant_data_adapter(Settings()), Settings(trace_store="postgres", database_url=None))

    def test_valid_postgres_configuration_is_lazy_and_memory_stays_default(self) -> None:
        config = Settings(data_source="postgres", database_url="postgresql://offline-test")
        adapter = create_plant_data_adapter(config)
        service = AgentService(create_plant_data_adapter(Settings()), Settings())

        self.assertIsInstance(adapter, PostgresPlantDataAdapter)
        self.assertIsInstance(service.trace_service, InMemoryTraceService)


if __name__ == "__main__":
    unittest.main()
