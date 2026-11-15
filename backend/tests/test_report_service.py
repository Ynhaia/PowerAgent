"""诊断报告生成与下载接口测试。"""

from io import BytesIO
import unittest

from docx import Document
from fastapi.testclient import TestClient

from backend.app.adapters.mock_plant import MockPlantDataAdapter
from backend.app.main import app
from backend.app.services.knowledge_service import KnowledgeService
from backend.app.services.report_service import CondenserVacuumReportService


class ReportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CondenserVacuumReportService(MockPlantDataAdapter(), KnowledgeService.default())
        self.client = TestClient(app)

    def test_generates_docx_with_safe_diagnosis_and_versioned_citation(self) -> None:
        report = self.service.generate("U01", 120, "5f13dd5d-cfbf-4c2e-b6e6-a73cf5ec164e")
        document = Document(BytesIO(report.content))
        text = "\n".join(
            [paragraph.text for paragraph in document.paragraphs]
            + [cell.text for table in document.tables for row in table.rows for cell in row.cells]
        )

        self.assertTrue(report.filename.endswith(".docx"))
        self.assertGreater(len(report.content), 10_000)
        self.assertIn("循环水流量不足", text)
        self.assertIn("禁止自动下发", text)
        self.assertIn("demo_verified", text)
        self.assertIn("5f13dd5d-cfbf-4c2e-b6e6-a73cf5ec164e", text)

    def test_report_download_endpoint_returns_docx_only_after_explicit_post(self) -> None:
        response = self.client.post(
            "/api/v1/reports/condenser-vacuum.docx",
            json={"unit_id": "U01", "window_minutes": 60},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["content-type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertGreater(len(response.content), 10_000)

    def test_invalid_window_is_rejected_before_report_generation(self) -> None:
        response = self.client.post(
            "/api/v1/reports/condenser-vacuum.docx",
            json={"unit_id": "U01", "window_minutes": 5},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
