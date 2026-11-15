from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.app.services.knowledge_service import KnowledgeService, KnowledgeValidationError


class KnowledgeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = KnowledgeService.default()

    def test_loads_versioned_demo_documents(self) -> None:
        self.assertEqual(self.service.document_count(), 3)
        self.assertGreaterEqual(self.service.chunk_count(), 8)

    def test_retrieves_citations_with_unit_and_system_filter(self) -> None:
        citations = self.service.search(
            "凝汽器真空下降，循环水流量不足时应如何核查",
            unit_id="U01",
            system="condenser",
        )
        self.assertTrue(citations)
        self.assertEqual(citations[0].document_id, "SOP-COND-001")
        self.assertEqual(citations[0].source_status, "demo_verified")
        self.assertIn("循环水", citations[0].snippet)

    def test_scope_filter_prevents_cross_unit_retrieval(self) -> None:
        citations = self.service.search("凝汽器真空下降", unit_id="U02", system="condenser")
        self.assertEqual(citations, [])

    def test_rejects_document_without_required_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "bad.md").write_text("# 没有 Front Matter", encoding="utf-8")
            with self.assertRaises(KnowledgeValidationError):
                KnowledgeService(root)


if __name__ == "__main__":
    unittest.main()
