"""毕业设计合成场景：固定种子、时间点和幅度，标签不传入诊断算法。"""

from datetime import datetime
import random

from .mock_plant import MockPlantDataAdapter
from ..domain.schemas import TelemetryPoint


SCENARIOS = {
    "cw_flow_insufficient": "循环水流量不足",
    "normal": "正常工况",
    "inconclusive": "压力异常但根因证据不足",
    "bad_quality": "坏质量测点",
    "sparse": "采样严重缺失",
}


class ScenarioPlantDataAdapter(MockPlantDataAdapter):
    def __init__(self, scenario: str = "cw_flow_insufficient", seed: int = 0) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"模拟场景仅支持：{', '.join(SCENARIOS)}")
        self.scenario = scenario
        self.seed = seed

    def get_telemetry(self, unit_id: str, tag_codes: list[str], start_time: datetime, end_time: datetime) -> dict[str, list[TelemetryPoint]]:
        raw = super().get_telemetry(unit_id, tag_codes, start_time, end_time)
        result = {}
        for tag in tag_codes:
            # 每个测点和种子独立，查询顺序不会影响序列；扰动不能视为独立真实事故。
            rng = random.Random(f"{self.seed}:{tag}")
            points = []
            for point in raw[tag]:
                value = point.value
                if self.scenario in {"normal", "inconclusive"}:
                    stable = {"COND_ABS_PRESSURE": 5.10, "CW_FLOW": 32900, "CW_PUMP_A_CURRENT": 166, "CW_PUMP_A_FREQ": 49.8}
                    if tag in stable and not (self.scenario == "inconclusive" and tag == "COND_ABS_PRESSURE"):
                        value = stable[tag]
                amplitude = {"COND_ABS_PRESSURE": 0.003, "CW_FLOW": 15, "CW_PUMP_A_CURRENT": 0.1, "CW_PUMP_A_FREQ": 0.002}.get(tag, 0)
                quality = "BAD" if self.scenario == "bad_quality" and tag == "CW_FLOW" else "GOOD"
                points.append(point.model_copy(update={"value": round(value + rng.uniform(-amplitude, amplitude), 5), "quality_code": quality}))
            result[tag] = points[::30] if self.scenario == "sparse" else points
        return result

    def get_alerts(self, unit_id: str, start_time: datetime, end_time: datetime):
        alerts = super().get_alerts(unit_id, start_time, end_time)
        if self.scenario == "normal":
            return []
        if self.scenario == "inconclusive":
            return [alert for alert in alerts if alert.tag_code == "COND_ABS_PRESSURE"]
        return alerts
