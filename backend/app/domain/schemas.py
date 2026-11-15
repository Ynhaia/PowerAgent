"""API 与领域层共享的数据契约。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class UnitSummary(BaseModel):
    unit_id: str
    unit_name: str
    unit_type: str = "thermal"
    rated_capacity_mw: float
    status: Literal["running", "stopped", "maintenance"]


class TelemetryPoint(BaseModel):
    timestamp: datetime
    value: float
    quality_code: Literal["GOOD", "BAD", "UNCERTAIN"] = "GOOD"


class TrendSeries(BaseModel):
    tag_code: str
    tag_name: str
    unit: str
    points: list[TelemetryPoint]


class TrendAnalysis(BaseModel):
    point_count: int
    min_value: float
    max_value: float
    average_value: float
    slope_per_minute: float
    anomaly_points: int


class TrendResponse(BaseModel):
    request_id: str
    unit_id: str
    start_time: datetime
    end_time: datetime
    aggregation: str
    series: list[TrendSeries]
    analysis: dict[str, TrendAnalysis]


class AlertEvent(BaseModel):
    alert_id: str
    unit_id: str
    tag_code: str
    alert_type: Literal["threshold", "residual", "rate_of_change", "equipment"]
    severity: Literal["minor", "major", "critical"]
    start_time: datetime
    end_time: datetime | None = None
    message: str
    observed_value: float | None = None
    threshold_value: float | None = None
    related_alert_ids: list[str] = Field(default_factory=list)


class DiagnosisRequest(BaseModel):
    unit_id: str = "U01"
    window_minutes: int = Field(default=120, ge=30, le=1440)
    include_report: bool = False


class ReportRequest(BaseModel):
    """显式生成报告的请求。

    报告内容由后端受控的数据、规则诊断和知识检索重新生成，浏览器不得提交候选根因、
    处置建议或任意文档内容。
    """

    unit_id: str = "U01"
    window_minutes: int = Field(default=120, ge=30, le=1440)
    source_trace_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")


class EvidenceItem(BaseModel):
    evidence_type: Literal["trend", "alert", "rule", "knowledge"]
    label: str
    detail: str
    reference: str


class DiagnosisCandidate(BaseModel):
    cause_code: str
    cause_name: str
    confidence: float = Field(ge=0, le=1, description="兼容字段，等于规则分数/100，未校准为故障概率")
    rule_score: int = Field(ge=0, le=100)
    evidence: list[EvidenceItem]
    verification_steps: list[str]


class DiagnosisResponse(BaseModel):
    request_id: str
    trace_id: str
    unit_id: str
    risk_level: Literal["low", "medium", "high", "critical"]
    summary: str
    candidates: list[DiagnosisCandidate]
    safety_notice: str
    requires_human_confirmation: bool = True
    report_id: str | None = None
    result_status: Literal["candidate_found", "normal", "inconclusive", "insufficient_data"] = "candidate_found"
    rule_version: str = "COND-RULE-2.0"
    score_semantics: str = "规则支持度，非统计校准的故障概率"


TaskType = Literal["query", "trend", "alert", "diagnose"]
InputMode = Literal["text", "voice", "shortcut"]


class TaskPlan(BaseModel):
    """LLM 或规则规划器生成，并在调用工具前由后端校验的执行计划。"""

    task_type: TaskType
    unit_id: str = "U01"
    tag_codes: list[str] = Field(default_factory=list)
    window_minutes: int = Field(default=120, ge=30, le=1440)
    include_alerts: bool = False
    reason: str = ""


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    input_mode: InputMode = "text"
    unit_id: str | None = None
    window_minutes: int | None = Field(default=None, ge=30, le=1440)


class ToolCallSummary(BaseModel):
    tool_name: str
    status: Literal["succeeded", "failed", "skipped"]
    summary: str


class ChatResponse(BaseModel):
    request_id: str
    trace_id: str
    session_id: str
    planner_source: Literal["deepseek", "rule_fallback"]
    plan: TaskPlan
    answer: str
    tool_calls: list[ToolCallSummary]
    data: dict[str, object] = Field(default_factory=dict)
    safety_notice: str | None = None


class TraceEvent(BaseModel):
    trace_id: str
    request_id: str
    session_id: str
    node_name: str
    status: Literal["started", "succeeded", "failed", "blocked"]
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class TraceResponse(BaseModel):
    trace_id: str
    events: list[TraceEvent]


class KnowledgeCitation(BaseModel):
    document_id: str
    title: str
    version: str
    doc_type: Literal["operating_procedure", "technical_manual", "fault_case", "safety_notice"]
    source_status: Literal["approved", "demo_verified"]
    section_path: str
    snippet: str
    score: float = Field(ge=0)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    unit_id: str = "U01"
    system: str = "condenser"
    top_k: int = Field(default=3, ge=1, le=10)


class KnowledgeSearchResponse(BaseModel):
    query: str
    unit_id: str
    system: str
    citations: list[KnowledgeCitation]
