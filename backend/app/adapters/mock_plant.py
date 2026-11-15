"""确定性的模拟电厂数据适配器。

数据只用于首期开发与测试。真实项目应以 HttpPlantDataAdapter 通过 DMZ 内
只读数据网关替换本适配器，Agent 不应直接访问工业控制协议。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math

from ..domain.schemas import AlertEvent, TelemetryPoint, UnitSummary


SHANGHAI_TZ = timezone(timedelta(hours=8))
REFERENCE_END = datetime(2026, 9, 22, 10, 0, tzinfo=SHANGHAI_TZ)

TAG_DEFINITIONS: dict[str, dict[str, object]] = {
    "COND_ABS_PRESSURE": {
        "name": "凝汽器绝对压力",
        "unit": "kPa(a)",
        "warning_high": 5.55,
    },
    "COND_VACUUM_DEGREE": {"name": "凝汽器真空度", "unit": "kPa", "warning_low": 94.20},
    "UNIT_LOAD_MW": {"name": "机组负荷", "unit": "MW"},
    "CW_INLET_TEMP": {"name": "循环水入口温度", "unit": "摄氏度"},
    "CW_OUTLET_TEMP": {"name": "循环水出口温度", "unit": "摄氏度"},
    "CW_FLOW": {"name": "循环水流量", "unit": "t/h", "warning_low": 30000.0},
    "CW_PUMP_A_CURRENT": {"name": "循环水泵 A 电流", "unit": "A"},
    "CW_PUMP_A_FREQ": {"name": "循环水泵 A 频率", "unit": "Hz"},
    "AIR_EXTRACTOR_PRESSURE": {"name": "抽气器压力", "unit": "kPa(a)"},
    "TURBINE_EXHAUST_TEMP": {"name": "汽轮机排汽温度", "unit": "摄氏度"},
    "COND_HEAT_TRANSFER_INDEX": {"name": "凝汽器换热效率指标", "unit": "百分比"},
}


class MockPlantDataAdapter:
    """返回 U01 凝汽器真空下降场景的模拟数据。"""

    unit = UnitSummary(
        unit_id="U01",
        unit_name="1 号机组",
        rated_capacity_mw=660.0,
        status="running",
    )

    def list_units(self) -> list[UnitSummary]:
        return [self.unit]

    def default_end_time(self) -> datetime:
        return REFERENCE_END

    def list_tag_codes(self) -> list[str]:
        return list(TAG_DEFINITIONS)

    def tag_definition(self, tag_code: str) -> dict[str, object]:
        return TAG_DEFINITIONS[tag_code]

    def get_telemetry(
        self,
        unit_id: str,
        tag_codes: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[TelemetryPoint]]:
        if unit_id != self.unit.unit_id:
            raise ValueError(f"未知机组：{unit_id}")
        unknown = [tag for tag in tag_codes if tag not in TAG_DEFINITIONS]
        if unknown:
            raise ValueError(f"未知测点：{', '.join(unknown)}")

        points: dict[str, list[TelemetryPoint]] = defaultdict(list)
        current = start_time.astimezone(SHANGHAI_TZ).replace(second=0, microsecond=0)
        end = end_time.astimezone(SHANGHAI_TZ).replace(second=0, microsecond=0)
        while current <= end:
            minute_from_end = int((current - REFERENCE_END).total_seconds() // 60)
            values = self._values_for(minute_from_end)
            for tag in tag_codes:
                points[tag].append(TelemetryPoint(timestamp=current, value=round(values[tag], 3)))
            current += timedelta(minutes=1)
        return points

    def get_alerts(self, unit_id: str, start_time: datetime, end_time: datetime) -> list[AlertEvent]:
        if unit_id != self.unit.unit_id:
            raise ValueError(f"未知机组：{unit_id}")
        alerts = [
            AlertEvent(
                alert_id="A-20260922-001",
                unit_id="U01",
                tag_code="COND_ABS_PRESSURE",
                alert_type="threshold",
                severity="major",
                start_time=REFERENCE_END - timedelta(minutes=85),
                message="凝汽器绝对压力高，真空存在恶化趋势。",
                observed_value=5.86,
                threshold_value=5.55,
                related_alert_ids=["A-20260922-002", "A-20260922-003"],
            ),
            AlertEvent(
                alert_id="A-20260922-002",
                unit_id="U01",
                tag_code="CW_FLOW",
                alert_type="threshold",
                severity="major",
                start_time=REFERENCE_END - timedelta(minutes=78),
                message="循环水流量低于同负荷基线。",
                observed_value=28450.0,
                threshold_value=30000.0,
                related_alert_ids=["A-20260922-001"],
            ),
            AlertEvent(
                alert_id="A-20260922-003",
                unit_id="U01",
                tag_code="CW_PUMP_A_CURRENT",
                alert_type="equipment",
                severity="minor",
                start_time=REFERENCE_END - timedelta(minutes=70),
                message="循环水泵 A 电流偏低，建议核查运行状态。",
                observed_value=143.0,
                related_alert_ids=["A-20260922-001"],
            ),
        ]
        return [alert for alert in alerts if alert.start_time <= end_time and (alert.end_time is None or alert.end_time >= start_time)]

    @staticmethod
    def _values_for(minute_from_end: int) -> dict[str, float]:
        """生成固定场景：最近 120 分钟循环水流量降低并引发真空恶化。"""
        position = min(0, minute_from_end)
        elapsed = max(0, min(120, 120 + position))
        wave = math.sin(elapsed / 7.0)
        pressure = 5.08 + elapsed * 0.0065 + wave * 0.012
        flow = 32900 - elapsed * 36.5 + wave * 45
        pump_current = 166 - elapsed * 0.19 + math.sin(elapsed / 9.0) * 1.2
        pump_freq = 49.8 - elapsed * 0.012 + math.sin(elapsed / 10.0) * 0.05
        return {
            "COND_ABS_PRESSURE": pressure,
            "COND_VACUUM_DEGREE": 95.15 - (pressure - 5.08) * 1.25,
            "UNIT_LOAD_MW": 603 + math.sin(elapsed / 15.0) * 4.5,
            "CW_INLET_TEMP": 25.8 + math.sin(elapsed / 20.0) * 0.18,
            "CW_OUTLET_TEMP": 33.1 + elapsed * 0.002 + math.sin(elapsed / 17.0) * 0.20,
            "CW_FLOW": flow,
            "CW_PUMP_A_CURRENT": pump_current,
            "CW_PUMP_A_FREQ": pump_freq,
            "AIR_EXTRACTOR_PRESSURE": 2.14 + math.sin(elapsed / 12.0) * 0.03,
            "TURBINE_EXHAUST_TEMP": 39.7 + elapsed * 0.007 + math.sin(elapsed / 8.0) * 0.08,
            "COND_HEAT_TRANSFER_INDEX": 92.8 - elapsed * 0.002,
        }
