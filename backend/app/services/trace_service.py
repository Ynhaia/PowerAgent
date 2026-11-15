"""Agent Trace 存储服务。

默认使用进程内实现，只有显式选择 PostgreSQL 时才使用 execution_trace 的追加审计仓储。
两种实现暴露相同接口，避免改变 LangGraph 编排逻辑。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Protocol

from ..database.postgres import PostgresTraceRepository
from ..domain.schemas import TraceEvent


class TraceService(Protocol):
    """Trace 存储所需的最小接口。"""

    def append(
        self,
        trace_id: str,
        request_id: str,
        session_id: str,
        node_name: str,
        status: str,
        payload: dict[str, object] | None = None,
    ) -> None: ...

    def get(self, trace_id: str) -> list[TraceEvent]: ...


class InMemoryTraceService:
    def __init__(self) -> None:
        self._events: dict[str, list[TraceEvent]] = defaultdict(list)
        self._lock = Lock()

    def append(
        self,
        trace_id: str,
        request_id: str,
        session_id: str,
        node_name: str,
        status: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        event = TraceEvent(
            trace_id=trace_id,
            request_id=request_id,
            session_id=session_id,
            node_name=node_name,
            status=status,
            payload=payload or {},
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._events[trace_id].append(event)

    def get(self, trace_id: str) -> list[TraceEvent]:
        with self._lock:
            return list(self._events.get(trace_id, []))


class PostgresTraceService:
    """将 Trace 事件以仅追加方式写入 PostgreSQL。"""

    def __init__(self, repository: PostgresTraceRepository) -> None:
        self.repository = repository

    def append(
        self,
        trace_id: str,
        request_id: str,
        session_id: str,
        node_name: str,
        status: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        event = TraceEvent(
            trace_id=trace_id,
            request_id=request_id,
            session_id=session_id,
            node_name=node_name,
            status=status,
            payload=payload or {},
            created_at=datetime.now(timezone.utc),
        )
        self.repository.append_event(event)

    def get(self, trace_id: str) -> list[TraceEvent]:
        return self.repository.fetch_events(trace_id)
