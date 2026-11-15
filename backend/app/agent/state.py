"""第三阶段 LangGraph 状态定义。"""

from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    request_id: str
    trace_id: str
    session_id: str
    question: str
    input_mode: str
    requested_unit_id: str | None
    requested_window_minutes: int | None
    plan: dict[str, object]
    planner_source: str
    tool_calls: list[dict[str, str]]
    tool_data: dict[str, object]
    answer: str
    safety_notice: str | None
    error: str | None
