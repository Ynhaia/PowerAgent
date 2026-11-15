"""毕业设计核心反例：正常、未知识别、采样质量与真实时间斜率。"""

from datetime import timedelta
from io import BytesIO

from docx import Document
import pytest

from backend.app.adapters.mock_plant import REFERENCE_END
from backend.app.adapters.scenario_plant import ScenarioPlantDataAdapter
from backend.app.agent.graph import PowerAgentGraph
from backend.app.core.config import Settings
from backend.app.domain.schemas import ChatRequest
from backend.app.services.diagnosis_service import CondenserVacuumDiagnosisService
from backend.app.services.knowledge_service import KnowledgeService
from backend.app.services.report_service import CondenserVacuumReportService
from backend.app.services.telemetry_service import TelemetryService
from backend.app.services.trace_service import InMemoryTraceService


@pytest.mark.parametrize("scenario,expected", [
    ("cw_flow_insufficient", "candidate_found"), ("normal", "normal"),
    ("inconclusive", "inconclusive"), ("bad_quality", "insufficient_data"),
    ("sparse", "insufficient_data"),
])
def test_distinguishes_scenarios_without_receiving_label(scenario, expected):
    response = CondenserVacuumDiagnosisService(ScenarioPlantDataAdapter(scenario, seed=7)).diagnose("U01", 60)
    assert response.result_status == expected
    if expected != "candidate_found":
        assert response.candidates[0].cause_code != "cw_flow_insufficient"
        assert response.candidates[0].rule_score == 0


def test_time_slope_does_not_assume_one_minute_sampling():
    class FiveMinuteAdapter(ScenarioPlantDataAdapter):
        def get_telemetry(self, *args, **kwargs):
            raw = super().get_telemetry(*args, **kwargs)
            return {tag: points[::5] for tag, points in raw.items()}

    adapter = FiveMinuteAdapter()
    result = TelemetryService(adapter).trend("U01", ["COND_ABS_PRESSURE"], REFERENCE_END - timedelta(minutes=60), REFERENCE_END)
    assert 0.005 < result.analysis["COND_ABS_PRESSURE"].slope_per_minute < 0.008


def test_active_alert_before_window_is_included():
    adapter = ScenarioPlantDataAdapter()
    assert len(adapter.get_alerts("U01", REFERENCE_END - timedelta(minutes=30), REFERENCE_END)) == 3


@pytest.mark.parametrize("scenario", ["normal", "inconclusive", "bad_quality", "sparse"])
def test_unresolved_graph_skips_causal_rag_and_keeps_trace_identity(scenario):
    service = InMemoryTraceService()
    graph = PowerAgentGraph(ScenarioPlantDataAdapter(scenario), service, KnowledgeService.default(), Settings(llm_api_key=None))
    result = graph.invoke(ChatRequest(message="分析凝汽器真空异常原因", window_minutes=60))
    assert result.data["diagnosis"]["knowledge_citations"] == []
    assert result.data["diagnosis"]["trace_id"] == result.trace_id
    assert result.planner_source == "rule_fallback"
    assert service.get(result.trace_id)[-1].node_name == "graph_end"


@pytest.mark.parametrize("corruption", ["non_finite", "duplicate", "missing", "uncertain"])
def test_corrupted_measurements_stop_diagnosis(corruption):
    class CorruptedAdapter(ScenarioPlantDataAdapter):
        def get_telemetry(self, *args, **kwargs):
            raw = super().get_telemetry(*args, **kwargs)
            if corruption == "non_finite":
                raw["CW_FLOW"][0].value = float("nan")
            elif corruption == "duplicate":
                raw["CW_FLOW"][1].timestamp = raw["CW_FLOW"][0].timestamp
            elif corruption == "missing":
                raw.pop("CW_FLOW")
            else:
                raw["CW_FLOW"][0].quality_code = "UNCERTAIN"
            return raw

    result = CondenserVacuumDiagnosisService(CorruptedAdapter()).diagnose("U01", 60)
    assert result.result_status == "insufficient_data"


def test_unrelated_query_has_no_knowledge_citation():
    assert KnowledgeService.default().search("量子纠缠与恒星光谱", "U01", "condenser") == []


def test_unimplemented_aggregation_is_rejected_instead_of_mislabeled():
    with pytest.raises(ValueError, match="尚未实现重采样"):
        TelemetryService(ScenarioPlantDataAdapter()).trend(
            "U01", ["COND_ABS_PRESSURE"], REFERENCE_END - timedelta(minutes=60), REFERENCE_END, "5m"
        )


@pytest.mark.parametrize("scenario", ["bad_quality", "sparse"])
def test_report_rejects_unusable_data(scenario):
    service = CondenserVacuumReportService(ScenarioPlantDataAdapter(scenario), KnowledgeService.default())
    with pytest.raises(ValueError, match="数据质量不满足报告要求"):
        service.generate("U01", 60)


@pytest.mark.parametrize("scenario", ["normal", "inconclusive"])
def test_report_uses_actual_decision_without_causal_knowledge(scenario):
    adapter = ScenarioPlantDataAdapter(scenario)
    diagnosis = CondenserVacuumDiagnosisService(adapter).diagnose("U01", 60)
    report = CondenserVacuumReportService(adapter, KnowledgeService.default()).generate("U01", 60)
    document = Document(BytesIO(report.content))
    text = "\n".join(p.text for p in document.paragraphs)
    assert diagnosis.summary in text
    assert "当前首要候选原因为循环水流量不足" not in text
    assert "不提供规程性处置结论" in text
