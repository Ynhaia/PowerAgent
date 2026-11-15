import unittest

from backend.app.adapters.mock_plant import MockPlantDataAdapter
from backend.app.services.diagnosis_service import CondenserVacuumDiagnosisService


class CondenserVacuumDiagnosisTests(unittest.TestCase):
    def test_circulating_water_flow_is_primary_candidate(self) -> None:
        result = CondenserVacuumDiagnosisService(MockPlantDataAdapter()).diagnose("U01", 120)
        primary = result.candidates[0]
        self.assertEqual(primary.cause_code, "cw_flow_insufficient")
        self.assertGreaterEqual(primary.rule_score, 60)
        self.assertEqual(result.risk_level, "high")

    def test_safety_boundary_is_explicit(self) -> None:
        result = CondenserVacuumDiagnosisService(MockPlantDataAdapter()).diagnose("U01", 120)
        self.assertTrue(result.requires_human_confirmation)
        self.assertIn("禁止自动下发", result.safety_notice)


if __name__ == "__main__":
    unittest.main()
