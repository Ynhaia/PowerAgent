"""受控诊断报告生成服务。

报告只使用后端重新计算的趋势、预警、确定性规则诊断与版本化知识引用；客户端不能注入
根因、处置建议或规程正文。生成行为由显式 API 请求触发，且报告不包含控制命令。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Iterable

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from ..adapters.contracts import PlantDataAdapter
from ..domain.schemas import AlertEvent, DiagnosisResponse, KnowledgeCitation, TrendResponse
from .diagnosis_service import CondenserVacuumDiagnosisService
from .knowledge_service import KnowledgeService
from .telemetry_service import TelemetryService


@dataclass(frozen=True)
class GeneratedReport:
    filename: str
    content: bytes


class CondenserVacuumReportService:
    """生成 U01 凝汽器真空下降专题的 DOCX 报告。"""

    CORE_TAGS = ["COND_ABS_PRESSURE", "CW_FLOW", "CW_PUMP_A_CURRENT", "CW_PUMP_A_FREQ"]

    def __init__(self, adapter: PlantDataAdapter, knowledge_service: KnowledgeService) -> None:
        self.adapter = adapter
        self.knowledge_service = knowledge_service
        self.diagnosis_service = CondenserVacuumDiagnosisService(adapter)
        self.telemetry_service = TelemetryService(adapter)

    def generate(self, unit_id: str, window_minutes: int, source_trace_id: str | None = None) -> GeneratedReport:
        diagnosis = self.diagnosis_service.diagnose(unit_id, window_minutes)
        if diagnosis.result_status == "insufficient_data":
            raise ValueError("数据质量不满足报告要求：" + diagnosis.summary)
        end_time = self.adapter.default_end_time()
        start_time = end_time - self._minutes(window_minutes)
        trend = self.telemetry_service.trend(unit_id, self.CORE_TAGS, start_time, end_time)
        alerts = self.adapter.get_alerts(unit_id, start_time, end_time)
        primary = diagnosis.candidates[0]
        citations = self.knowledge_service.search(
            query=f"凝汽器真空下降 {primary.cause_name} {primary.cause_code}",
            unit_id=unit_id,
            system="condenser",
            top_k=3,
        ) if diagnosis.result_status == "candidate_found" else []
        report = self._build_document(
            diagnosis=diagnosis,
            trend=trend,
            alerts=alerts,
            citations=citations,
            window_minutes=window_minutes,
            start_time=start_time,
            end_time=end_time,
            source_trace_id=source_trace_id,
        )
        buffer = BytesIO()
        report.save(buffer)
        timestamp = end_time.strftime("%Y%m%d%H%M")
        return GeneratedReport(filename=f"PowerAgent_{unit_id}_凝汽器真空下降诊断报告_{timestamp}.docx", content=buffer.getvalue())

    @staticmethod
    def _minutes(value: int):
        from datetime import timedelta

        return timedelta(minutes=value)

    def _build_document(
        self,
        *,
        diagnosis: DiagnosisResponse,
        trend: TrendResponse,
        alerts: list[AlertEvent],
        citations: list[KnowledgeCitation],
        window_minutes: int,
        start_time: datetime,
        end_time: datetime,
        source_trace_id: str | None,
    ) -> Document:
        document = Document()
        section = document.sections[0]
        section.top_margin = Cm(2.1)
        section.bottom_margin = Cm(2.1)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.2)
        document.core_properties.title = "凝汽器真空下降诊断报告"
        document.core_properties.author = "PowerAgent"
        self._configure_styles(document)

        title = document.add_paragraph(style="Title")
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title.add_run("1 号机凝汽器真空下降诊断报告")
        subtitle = document.add_paragraph()
        subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
        subtitle_run = subtitle.add_run("PowerAgent 电厂智能预警与故障诊断系统")
        subtitle_run.font.size = Pt(10.5)
        subtitle_run.font.color.rgb = RGBColor(89, 89, 89)
        document.add_paragraph(
            f"结论：{diagnosis.summary}该结论仅用于辅助运行人员核查，"
            "不构成控制指令；任何现场处置均须由具备权限的人员依据批准规程确认后执行。"
        )

        self._heading(document, "报告范围")
        scope = self._table(document, ["项目", "内容"], [
            ["机组", diagnosis.unit_id],
            ["专题", "凝汽器真空下降"],
            ["分析窗口", f"{window_minutes} 分钟（{self._format_time(start_time)} 至 {self._format_time(end_time)}）"],
            ["风险等级", self._risk_label(diagnosis.risk_level)],
            ["报告生成时间", self._format_time(end_time)],
            ["关联 Agent Trace", source_trace_id or "未关联 Agent Trace，本报告由后端受控规则链路生成"],
        ])
        self._set_widths(scope, (Cm(4.2), Cm(12.2)))

        primary = diagnosis.candidates[0]
        self._heading(document, "受控诊断结论")
        document.add_paragraph(diagnosis.summary)
        conclusion = self._table(document, ["诊断结果", "规则评分", "规则支持度", "人工确认"], [[
            primary.cause_name,
            str(primary.rule_score),
            f"{primary.confidence:.0%}",
            "必须",
        ]])
        document.add_paragraph("规则支持度是规则评分的归一化表示，不是经过统计校准的故障发生概率。")
        self._set_widths(conclusion, (Cm(6.0), Cm(3.0), Cm(3.0), Cm(4.4)))

        self._heading(document, "趋势与预警证据")
        trend_rows = []
        for tag_code in ("COND_ABS_PRESSURE", "CW_FLOW", "CW_PUMP_A_CURRENT", "CW_PUMP_A_FREQ"):
            analysis = trend.analysis[tag_code]
            series = next(item for item in trend.series if item.tag_code == tag_code)
            trend_rows.append([
                series.tag_name,
                series.unit,
                f"{analysis.min_value:.3f}",
                f"{analysis.max_value:.3f}",
                f"{analysis.slope_per_minute:.4f}",
                str(analysis.anomaly_points),
            ])
        table = self._table(document, ["测点", "单位", "最小值", "最大值", "斜率 每分钟", "异常点"], trend_rows)
        self._set_widths(table, (Cm(4.1), Cm(2.0), Cm(2.3), Cm(2.3), Cm(3.0), Cm(2.0)))
        document.add_paragraph("关联预警：", style="Heading 2")
        alert_rows = [
            [self._format_time(item.start_time), item.severity, item.tag_code, item.message]
            for item in alerts
        ] or [["--", "--", "--", "当前窗口没有关联预警"]]
        alert_table = self._table(document, ["发生时间", "等级", "测点", "事件"], alert_rows)
        self._set_widths(alert_table, (Cm(3.1), Cm(2.0), Cm(3.0), Cm(7.6)))

        self._heading(document, "规则证据与人工核查")
        for index, evidence in enumerate(primary.evidence, start=1):
            paragraph = document.add_paragraph(style="List Bullet")
            paragraph.add_run(f"{evidence.label}：").bold = True
            paragraph.add_run(evidence.detail)
            reference = paragraph.add_run(f"（{evidence.reference}）")
            reference.font.color.rgb = RGBColor(89, 89, 89)
        document.add_paragraph("人工核查步骤：", style="Heading 2")
        for step in primary.verification_steps:
            document.add_paragraph(step, style="List Number")
        safety = document.add_paragraph()
        safety.add_run("安全边界：").bold = True
        safety.add_run(diagnosis.safety_notice)

        self._heading(document, "知识依据")
        if citations:
            citation_rows = [
                [item.document_id, item.title, item.version, item.section_path, item.source_status]
                for item in citations
            ]
            citations_table = self._table(document, ["文档 ID", "标题", "版本", "章节", "来源状态"], citation_rows)
            self._set_widths(citations_table, (Cm(2.5), Cm(4.2), Cm(2.1), Cm(5.0), Cm(2.0)))
            for item in citations:
                p = document.add_paragraph(style="List Bullet")
                p.add_run(f"{item.document_id} {item.section_path}：").bold = True
                p.add_run(item.snippet)
        else:
            document.add_paragraph("未检索到适用的版本化知识依据，报告不提供规程性处置结论。")

        self._heading(document, "使用限制")
        document.add_paragraph(
            "本报告使用当前 PowerAgent 的专题模拟数据、确定性规则和知识库检索结果生成。"
            "当前引用资料的来源状态以表中标识为准；标记为 demo_verified 的资料仅供开发验证，"
            "不得替代经批准的现场规程。"
        )
        document.add_paragraph(
            "报告不向 DCS、SIS、PLC 或任何现场设备发送命令，也不应被作为自动控制或联锁依据。"
        )
        return document

    @staticmethod
    def _configure_styles(document: Document) -> None:
        normal = document.styles["Normal"]
        normal.font.name = "Microsoft YaHei"
        normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        normal.font.size = Pt(10.5)
        normal.paragraph_format.space_after = Pt(7)
        title = document.styles["Title"]
        title.font.name = "Microsoft YaHei"
        title._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        title.font.size = Pt(20)
        title.font.color.rgb = RGBColor(0, 0, 0)
        for style_name, size in (("Heading 1", 15), ("Heading 2", 11.5)):
            style = document.styles[style_name]
            style.font.name = "Microsoft YaHei"
            style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
            style.font.size = Pt(size)
            style.font.color.rgb = RGBColor(0, 0, 0)

    @staticmethod
    def _heading(document: Document, text: str) -> None:
        document.add_paragraph(text, style="Heading 1")

    def _table(self, document: Document, headers: list[str], rows: Iterable[list[str]]):
        table = document.add_table(rows=1, cols=len(headers))
        table.style = "Table Grid"
        table.autofit = False
        for cell, header in zip(table.rows[0].cells, headers):
            cell.text = header
            self._shade(cell, "1F4E78")
            for run in cell.paragraphs[0].runs:
                run.font.bold = True
                run.font.color.rgb = RGBColor(255, 255, 255)
                run.font.size = Pt(9.5)
        for index, values in enumerate(rows):
            cells = table.add_row().cells
            for cell, value in zip(cells, values):
                cell.text = str(value)
                if index % 2 == 1:
                    self._shade(cell, "F2F6FA")
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(9.2)
        document.add_paragraph()
        return table

    @staticmethod
    def _shade(cell, fill: str) -> None:
        properties = cell._tc.get_or_add_tcPr()
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), fill)
        properties.append(shading)

    @staticmethod
    def _set_widths(table, widths: tuple[Cm, ...]) -> None:
        for row in table.rows:
            for cell, width in zip(row.cells, widths):
                cell.width = width

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M %z")

    @staticmethod
    def _risk_label(value: str) -> str:
        return {"low": "低", "medium": "中", "high": "高", "critical": "紧急"}.get(value, value)
