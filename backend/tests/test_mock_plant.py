from datetime import timedelta
import unittest

from backend.app.adapters.mock_plant import MockPlantDataAdapter, REFERENCE_END
from backend.app.services.telemetry_service import TelemetryService


class MockPlantAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = MockPlantDataAdapter()

    def test_core_tags_are_available(self) -> None:
        tags = self.adapter.list_tag_codes()
        self.assertIn("COND_ABS_PRESSURE", tags)
        self.assertIn("CW_FLOW", tags)
        self.assertIn("CW_PUMP_A_CURRENT", tags)

    def test_pressure_rises_while_circulating_water_flow_falls(self) -> None:
        points = self.adapter.get_telemetry(
            "U01",
            ["COND_ABS_PRESSURE", "CW_FLOW"],
            REFERENCE_END - timedelta(minutes=120),
            REFERENCE_END,
        )
        self.assertGreater(points["COND_ABS_PRESSURE"][-1].value, points["COND_ABS_PRESSURE"][0].value)
        self.assertLess(points["CW_FLOW"][-1].value, points["CW_FLOW"][0].value)

    def test_trend_service_returns_analysis(self) -> None:
        response = TelemetryService(self.adapter).trend(
            "U01",
            ["COND_ABS_PRESSURE"],
            REFERENCE_END - timedelta(minutes=30),
            REFERENCE_END,
        )
        self.assertEqual(response.analysis["COND_ABS_PRESSURE"].point_count, 31)
        self.assertGreater(response.analysis["COND_ABS_PRESSURE"].slope_per_minute, 0)


if __name__ == "__main__":
    unittest.main()

