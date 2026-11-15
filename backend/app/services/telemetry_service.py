"""受控时序查询与基础趋势计算。"""

from __future__ import annotations

from datetime import datetime
import math
from statistics import fmean
from uuid import uuid4

from ..adapters.contracts import PlantDataAdapter
from ..domain.schemas import TrendAnalysis, TrendResponse, TrendSeries


class DataQualityError(ValueError):
    """测点缺失、坏质量、非有限值或无效时间戳，不能用于规则推理。"""


class TelemetryService:
    def __init__(self, adapter: PlantDataAdapter) -> None:
        self.adapter = adapter

    def trend(
        self,
        unit_id: str,
        tag_codes: list[str],
        start_time: datetime,
        end_time: datetime,
        aggregation: str = "1m",
    ) -> TrendResponse:
        if start_time >= end_time:
            raise ValueError("start_time 必须早于 end_time")
        if aggregation != "1m":
            raise ValueError("当前仅支持 aggregation=1m 原始采样返回，尚未实现重采样聚合")
        raw = self.adapter.get_telemetry(unit_id, tag_codes, start_time, end_time)
        series: list[TrendSeries] = []
        analysis: dict[str, TrendAnalysis] = {}
        for tag_code in tag_codes:
            points = raw.get(tag_code, [])
            if not points:
                raise DataQualityError(f"{tag_code} 没有可用数据")
            if any(point.quality_code != "GOOD" or not math.isfinite(point.value) for point in points):
                raise DataQualityError(f"{tag_code} 包含坏质量、不确定质量或非有限数值")
            if any(point.timestamp.tzinfo is None for point in points):
                raise DataQualityError(f"{tag_code} 时间戳缺少时区")
            if any(not start_time <= point.timestamp <= end_time for point in points):
                raise DataQualityError(f"{tag_code} 时间戳越过查询窗口")
            if any(a.timestamp >= b.timestamp for a, b in zip(points, points[1:])):
                raise DataQualityError(f"{tag_code} 时间戳重复或未递增")
            values = [point.value for point in points]
            definition = self.adapter.tag_definition(tag_code)
            elapsed_minutes = (points[-1].timestamp - points[0].timestamp).total_seconds() / 60
            slope = 0.0 if elapsed_minutes <= 0 else (values[-1] - values[0]) / elapsed_minutes
            threshold = definition.get("warning_high") or definition.get("warning_low")
            if "warning_high" in definition:
                anomaly_count = sum(value > float(threshold) for value in values)
            elif "warning_low" in definition:
                anomaly_count = sum(value < float(threshold) for value in values)
            else:
                anomaly_count = 0
            series.append(
                TrendSeries(
                    tag_code=tag_code,
                    tag_name=str(definition["name"]),
                    unit=str(definition["unit"]),
                    points=points,
                )
            )
            analysis[tag_code] = TrendAnalysis(
                point_count=len(values),
                min_value=min(values),
                max_value=max(values),
                average_value=fmean(values),
                slope_per_minute=slope,
                anomaly_points=anomaly_count,
            )
        return TrendResponse(
            request_id=str(uuid4()),
            unit_id=unit_id,
            start_time=start_time,
            end_time=end_time,
            aggregation=aggregation,
            series=series,
            analysis=analysis,
        )
