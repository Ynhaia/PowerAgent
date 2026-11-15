"""PowerAgent 基础 REST API。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..adapters.factory import create_plant_data_adapter
from ..adapters.http_plant import PlantGatewayError
from ..agent.service import AgentService
from ..core.config import settings
from ..database.postgres import PowerAgentDatabaseError
from ..domain.schemas import (
    AlertEvent,
    ChatRequest,
    ChatResponse,
    DiagnosisRequest,
    DiagnosisResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    ReportRequest,
    TraceResponse,
    TrendResponse,
    UnitSummary,
)
from ..services.diagnosis_service import CondenserVacuumDiagnosisService
from ..services.telemetry_service import TelemetryService
from ..services.report_service import CondenserVacuumReportService


router = APIRouter()
adapter = create_plant_data_adapter()
telemetry_service = TelemetryService(adapter)
diagnosis_service = CondenserVacuumDiagnosisService(adapter)
agent_service = AgentService(adapter)
report_service = CondenserVacuumReportService(adapter, agent_service.knowledge_service)


@router.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
        "run_mode": settings.run_mode,
        "llm_enabled": settings.llm_enabled,
        "plant_gateway_enabled": settings.plant_gateway_enabled,
        "database_configured": settings.database_configured,
        "trace_store": settings.trace_store,
        "mock_scenario": settings.mock_scenario if settings.data_source == "mock" else None,
    }


@router.get("/units", response_model=list[UnitSummary])
def units() -> list[UnitSummary]:
    try:
        return adapter.list_units()
    except PowerAgentDatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/telemetry/trend", response_model=TrendResponse)
def telemetry_trend(
    unit_id: str = "U01",
    tags: Annotated[str, Query(description="逗号分隔的测点编码")] = "COND_ABS_PRESSURE,CW_FLOW",
    start: datetime | None = None,
    end: datetime | None = None,
    aggregation: str = "1m",
    window_minutes: Annotated[int, Query(ge=30, le=1440)] = 120,
) -> TrendResponse:
    end_time = end or adapter.default_end_time()
    start_time = start or (end_time - timedelta(minutes=window_minutes))
    tag_codes = [tag.strip().upper() for tag in tags.split(",") if tag.strip()]
    if not tag_codes:
        raise HTTPException(status_code=422, detail="至少指定一个测点编码")
    try:
        return telemetry_service.trend(unit_id, tag_codes, start_time, end_time, aggregation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PlantGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PowerAgentDatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/alerts", response_model=list[AlertEvent])
def alerts(
    unit_id: str = "U01",
    start: datetime | None = None,
    end: datetime | None = None,
    window_minutes: Annotated[int, Query(ge=30, le=1440)] = 120,
) -> list[AlertEvent]:
    end_time = end or adapter.default_end_time()
    start_time = start or (end_time - timedelta(minutes=window_minutes))
    try:
        return adapter.get_alerts(unit_id, start_time, end_time)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PlantGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PowerAgentDatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/diagnoses/condenser-vacuum", response_model=DiagnosisResponse)
def diagnose_condenser_vacuum(request: DiagnosisRequest) -> DiagnosisResponse:
    try:
        return diagnosis_service.diagnose(request.unit_id, request.window_minutes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PlantGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PowerAgentDatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/reports/condenser-vacuum.docx")
def download_condenser_vacuum_report(request: ReportRequest) -> StreamingResponse:
    """按显式用户请求生成 DOCX 诊断报告。

    内容由受控后端重新计算；不会接受浏览器提供的诊断结论，也不会生成控制命令。
    """
    try:
        report = report_service.generate(request.unit_id, request.window_minutes, request.source_trace_id)
        return StreamingResponse(
            iter([report.content]),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": 'attachment; filename="poweragent-condenser-vacuum-report.docx"',
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PlantGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PowerAgentDatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """第三阶段统一 Agent 入口。

    未配置 DeepSeek Key 时仍可用，规划环节会标记为 rule_fallback；配置后会优先
    通过 DeepSeek API 获取结构化任务计划。
    """
    try:
        return agent_service.graph.invoke(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PlantGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PowerAgentDatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/traces/{trace_id}", response_model=TraceResponse)
def trace(trace_id: str) -> TraceResponse:
    try:
        events = agent_service.trace_service.get(trace_id)
    except PowerAgentDatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not events:
        storage_hint = "当前内存 Trace 会在服务重启后清空" if settings.trace_store == "memory" else "请确认 Trace ID 与 PostgreSQL 审计数据"
        raise HTTPException(status_code=404, detail=f"未找到 Trace，{storage_hint}")
    return TraceResponse(trace_id=trace_id, events=events)


@router.post("/knowledge/search", response_model=KnowledgeSearchResponse)
def knowledge_search(request: KnowledgeSearchRequest) -> KnowledgeSearchResponse:
    """检索版本化的项目内知识文档，返回可显示的来源和章节。"""
    citations = agent_service.knowledge_service.search(
        query=request.query,
        unit_id=request.unit_id,
        system=request.system,
        top_k=request.top_k,
    )
    return KnowledgeSearchResponse(
        query=request.query,
        unit_id=request.unit_id,
        system=request.system,
        citations=citations,
    )
