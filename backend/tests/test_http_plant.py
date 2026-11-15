from datetime import datetime, timedelta, timezone
import unittest

from backend.app.adapters.http_plant import HttpPlantDataAdapter, PlantGatewayError
from backend.app.adapters.factory import create_plant_data_adapter
from backend.app.core.config import Settings


TZ = timezone(timedelta(hours=8))


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class FakeGatewaySession:
    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, *, params=None, headers=None, timeout=None) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, "params": params, "headers": headers, "timeout": timeout})
        path = url.rsplit("/", 1)[-1]
        if "/tag-definitions/" in url:
            path = "tag-definition"
        return FakeResponse(self.payloads[path])


class HttpPlantDataAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = FakeGatewaySession(
            {
                "units": {"data": [{"unit_id": "U01", "unit_name": "1 号机组", "rated_capacity_mw": 660, "status": "running"}]},
                "tag-definition": {"data": {"tag_code": "COND_ABS_PRESSURE", "tag_name": "凝汽器绝对压力", "unit": "kPa(a)", "warning_high": 5.55}},
                "telemetry": {"data": {"series": [{"tag_code": "COND_ABS_PRESSURE", "points": [{"timestamp": "2026-09-22T10:00:00+08:00", "value": 5.86, "quality_code": "GOOD"}]}]}},
                "alerts": {"data": [{"alert_id": "A-1", "unit_id": "U01", "tag_code": "COND_ABS_PRESSURE", "alert_type": "threshold", "severity": "major", "start_time": "2026-09-22T09:00:00+08:00", "message": "凝汽器绝对压力高"}]},
            }
        )
        self.adapter = HttpPlantDataAdapter("https://gateway.example.internal", token="test-token", session=self.session)
        self.start = datetime(2026, 9, 22, 9, 0, tzinfo=TZ)
        self.end = datetime(2026, 9, 22, 10, 0, tzinfo=TZ)

    def test_reads_gateway_contract_with_get_only(self) -> None:
        units = self.adapter.list_units()
        definition = self.adapter.tag_definition("COND_ABS_PRESSURE")
        telemetry = self.adapter.get_telemetry("U01", ["COND_ABS_PRESSURE"], self.start, self.end)
        alerts = self.adapter.get_alerts("U01", self.start, self.end)

        self.assertEqual(units[0].unit_id, "U01")
        self.assertEqual(definition["name"], "凝汽器绝对压力")
        self.assertEqual(telemetry["COND_ABS_PRESSURE"][0].value, 5.86)
        self.assertEqual(alerts[0].severity, "major")
        self.assertTrue(all(call["method"] == "GET" for call in self.session.calls))
        self.assertEqual(self.session.calls[0]["headers"]["Authorization"], "Bearer test-token")

    def test_rejects_incomplete_telemetry_response(self) -> None:
        session = FakeGatewaySession({"telemetry": {"data": {"series": []}}})
        adapter = HttpPlantDataAdapter("https://gateway.example.internal", session=session)
        with self.assertRaises(PlantGatewayError):
            adapter.get_telemetry("U01", ["COND_ABS_PRESSURE"], self.start, self.end)

    def test_rejects_non_http_gateway_address(self) -> None:
        with self.assertRaises(ValueError):
            HttpPlantDataAdapter("opc.tcp://10.0.0.1")

    def test_http_source_requires_gateway_url(self) -> None:
        with self.assertRaises(ValueError):
            create_plant_data_adapter(Settings(data_source="http", plant_api_base_url=None))


if __name__ == "__main__":
    unittest.main()
