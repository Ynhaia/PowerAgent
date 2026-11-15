"""凝汽器真空下降专题的确定性诊断规则。

后续 RAG 与 LangGraph 只能补充证据和解释，不应绕过本服务的安全约束。
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from ..adapters.contracts import PlantDataAdapter
from ..domain.schemas import DiagnosisCandidate, DiagnosisResponse, EvidenceItem
from .telemetry_service import DataQualityError, TelemetryService


class CondenserVacuumDiagnosisService:
    CORE_TAGS = ["COND_ABS_PRESSURE", "CW_FLOW", "CW_PUMP_A_CURRENT", "CW_PUMP_A_FREQ"]

    def __init__(self, adapter: PlantDataAdapter) -> None:
        self.adapter = adapter
        self.telemetry = TelemetryService(self.adapter)

    def diagnose(self, unit_id: str = "U01", window_minutes: int = 120) -> DiagnosisResponse:
        end_time = self.adapter.default_end_time()
        start_time = end_time - timedelta(minutes=window_minutes)
        if not 30 <= window_minutes <= 1440:
            raise ValueError("诊断时间窗口必须为 30 至 1440 分钟")
        try:
            trend = self.telemetry.trend(unit_id, self.CORE_TAGS, start_time, end_time)
        except DataQualityError as exc:
            return self._unresolved(unit_id, "insufficient_data", str(exc))
        for series in trend.series:
            points = series.points
            coverage = (points[-1].timestamp - points[0].timestamp).total_seconds() / 60
            gaps = [(b.timestamp - a.timestamp).total_seconds() / 60 for a, b in zip(points, points[1:])]
            if len(points) < int(window_minutes * 0.8) + 1 or coverage < window_minutes * 0.8 or any(gap > 5 for gap in gaps):
                return self._unresolved(unit_id, "insufficient_data", f"{series.tag_code} 未满足一分钟采样下的点数、覆盖率或最大间隔要求")
        alerts = self.adapter.get_alerts(unit_id, start_time, end_time)
        pressure = trend.analysis["COND_ABS_PRESSURE"]
        flow = trend.analysis["CW_FLOW"]
        current = trend.analysis["CW_PUMP_A_CURRENT"]
        frequency = trend.analysis["CW_PUMP_A_FREQ"]

        flow_deviation_pct = (flow.min_value - 32900.0) / 32900.0 * 100
        score = 0
        if flow_deviation_pct <= -10:
            score += 35
        if current.slope_per_minute < -0.12:
            score += 20
        if frequency.slope_per_minute < -0.008:
            score += 15
        if pressure.slope_per_minute > 0.004:
            score += 15
        score = min(score, 100)
        confidence = round(score / 100, 2)
        pressure_abnormal = pressure.max_value > 5.55 or pressure.slope_per_minute > 0.004
        if not pressure_abnormal:
            return self._unresolved(unit_id, "normal", "当前窗口未触发压力越限或持续上升规则，不确认凝汽器真空异常")
        if flow_deviation_pct > -10 or score < 60:
            return self._unresolved(unit_id, "inconclusive", "压力存在异常，但循环水及泵特征证据不足，暂不判定根因；需补充抽气、泄漏、换热等证据")

        primary = DiagnosisCandidate(
            cause_code="cw_flow_insufficient",
            cause_name="循环水流量不足",
            confidence=confidence,
            rule_score=score,
            evidence=[
                EvidenceItem(
                    evidence_type="trend",
                    label="循环水流量下降",
                    detail=f"窗口内最低流量 {flow.min_value:.0f} t/h，较同负荷基线低 {abs(flow_deviation_pct):.1f}%。",
                    reference="trend:CW_FLOW",
                ),
                EvidenceItem(
                    evidence_type="trend",
                    label="凝汽器绝对压力持续升高",
                    detail=f"绝对压力从 {pressure.min_value:.2f} 升至 {pressure.max_value:.2f} kPa(a)，斜率 {pressure.slope_per_minute:.4f} kPa(a)/min。",
                    reference="trend:COND_ABS_PRESSURE",
                ),
                EvidenceItem(
                    evidence_type="alert",
                    label="关联预警",
                    detail=f"检出 {len(alerts)} 条与窗口重叠的专题预警。预警数量仅作为佐证，不直接参与规则评分。",
                    reference="alerts:" + ",".join(alert.alert_id for alert in alerts),
                ),
                EvidenceItem(
                    evidence_type="rule",
                    label="泵运行特征异常",
                    detail=f"泵电流斜率 {current.slope_per_minute:.3f} A/min，频率斜率 {frequency.slope_per_minute:.3f} Hz/min。",
                    reference="rule:COND-CW-FLOW-001",
                ),
            ],
            verification_steps=[
                "核查循环水泵 A 的运行状态、电流、频率和就地指示是否一致。",
                "核查循环水泵进出口压差、阀位、滤网压差和取水条件。",
                "由具备权限的运行人员依据现场规程确认后执行后续处置。",
            ],
        )
        risk_level = "high" if score >= 60 else "medium" if score >= 30 else "low"
        return DiagnosisResponse(
            request_id=str(uuid4()),
            trace_id=str(uuid4()),
            unit_id=unit_id,
            risk_level=risk_level,
            summary=(
                f"凝汽器绝对压力在最近 {window_minutes} 分钟内持续升高，"
                f"同时循环水流量显著下降。当前首要候选原因为循环水流量不足。"
            ),
            candidates=[primary],
            safety_notice="本系统仅提供数据分析和核查建议，禁止自动下发 DCS、SIS、PLC 或现场设备控制命令。",
        )

    @staticmethod
    def _unresolved(unit_id: str, status: str, detail: str) -> DiagnosisResponse:
        names = {"normal": "未发现当前专题异常", "inconclusive": "根因证据不足", "insufficient_data": "数据不足，暂停诊断"}
        return DiagnosisResponse(
            request_id=str(uuid4()), trace_id=str(uuid4()), unit_id=unit_id,
            risk_level="low" if status == "normal" else "medium",
            result_status=status, summary=detail,
            candidates=[DiagnosisCandidate(
                cause_code=status, cause_name=names[status], confidence=0, rule_score=0,
                evidence=[EvidenceItem(evidence_type="rule", label=names[status], detail=detail, reference="rule:COND-GATE-002")],
                verification_steps=["先核实数据质量及现场状态，再由授权人员决定是否补充测点和人工核查。"],
            )],
            safety_notice="本系统仅提供数据分析和核查建议，禁止自动下发 DCS、SIS、PLC 或现场设备控制命令。数据或证据不足不能证明设备安全。",
        )
