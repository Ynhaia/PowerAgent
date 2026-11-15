"""电厂运行数据的只读适配器契约。

业务服务只依赖本协议。任何具体实现都不得包含写入、控制、设备操作或工业协议
直连能力；真实数据应由 DMZ 内的只读 HTTP 网关统一提供。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..domain.schemas import AlertEvent, TelemetryPoint, UnitSummary


class PlantDataAdapter(Protocol):
    """面向趋势、预警和诊断服务的最小只读数据接口。"""

    def list_units(self) -> list[UnitSummary]:
        """返回当前调用方可读取的机组。"""

    def list_tag_codes(self) -> list[str]:
        """返回已知的测点编码。"""

    def tag_definition(self, tag_code: str) -> dict[str, object]:
        """返回测点元数据、单位和可选预警阈值。"""

    def get_telemetry(
        self,
        unit_id: str,
        tag_codes: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[TelemetryPoint]]:
        """查询指定时间窗口的历史或实时测点数据。"""

    def get_alerts(self, unit_id: str, start_time: datetime, end_time: datetime) -> list[AlertEvent]:
        """查询指定时间窗口的预警事件。"""

    def default_end_time(self) -> datetime:
        """返回未指定查询终止时间时使用的时间。"""

