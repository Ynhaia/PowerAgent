"""DMZ 只读数据网关的 HTTP 适配器。

本模块只允许 GET 请求，且不包含 OPC UA、DCS、SIS、PLC 或其他控制协议。
接口字段和错误处理在这里集中适配，防止领域服务绑定具体厂内系统。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urljoin

import requests

from ..domain.schemas import AlertEvent, TelemetryPoint, UnitSummary


class GatewayResponse(Protocol):
    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


class GatewaySession(Protocol):
    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> GatewayResponse: ...


class PlantGatewayError(RuntimeError):
    """只读数据网关不可用或返回不符合契约的数据。"""


class HttpPlantDataAdapter:
    """通过只读 HTTP 网关获取电厂数据。

网关契约：
  GET /v1/units
  GET /v1/tag-definitions/{tag_code}
  GET /v1/telemetry?unit_id=...&tag_codes=A,B&start=...&end=...
  GET /v1/alerts?unit_id=...&start=...&end=...

每个响应可为 JSON 列表，也可为 {"data": ...}。遥测响应 data 的标准格式为：
{"series": [{"tag_code", "tag_name", "unit", "points": [{"timestamp", "value", "quality_code"}]}]}。
"""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout_seconds: float = 5.0,
        session: GatewaySession | None = None,
    ) -> None:
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("只读数据网关地址必须以 http:// 或 https:// 开头")
        if timeout_seconds <= 0:
            raise ValueError("网关超时时间必须大于 0")
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.headers = {"Accept": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self._tag_cache: dict[str, dict[str, object]] = {}

    def list_units(self) -> list[UnitSummary]:
        data = self._get("v1/units")
        if not isinstance(data, list):
            raise PlantGatewayError("网关 /v1/units 响应必须为列表")
        try:
            return [UnitSummary.model_validate(item) for item in data]
        except Exception as exc:
            raise PlantGatewayError("网关机组数据不符合 PowerAgent 契约") from exc

    def list_tag_codes(self) -> list[str]:
        data = self._get("v1/tag-definitions")
        if not isinstance(data, list):
            raise PlantGatewayError("网关 /v1/tag-definitions 响应必须为列表")
        codes: list[str] = []
        for item in data:
            if not isinstance(item, dict) or not item.get("tag_code"):
                raise PlantGatewayError("网关测点字典缺少 tag_code")
            code = str(item["tag_code"]).upper()
            self._tag_cache[code] = self._normalize_tag_definition(item)
            codes.append(code)
        return codes

    def tag_definition(self, tag_code: str) -> dict[str, object]:
        code = tag_code.upper()
        if code not in self._tag_cache:
            data = self._get(f"v1/tag-definitions/{code}")
            if not isinstance(data, dict):
                raise PlantGatewayError("网关测点定义响应必须为对象")
            self._tag_cache[code] = self._normalize_tag_definition(data)
        return self._tag_cache[code]

    def get_telemetry(
        self,
        unit_id: str,
        tag_codes: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[TelemetryPoint]]:
        data = self._get(
            "v1/telemetry",
            params={
                "unit_id": unit_id,
                "tag_codes": ",".join(tag.upper() for tag in tag_codes),
                "start": self._iso8601(start_time),
                "end": self._iso8601(end_time),
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("series"), list):
            raise PlantGatewayError("网关遥测响应必须包含 series 列表")
        result: dict[str, list[TelemetryPoint]] = {}
        for series in data["series"]:
            if not isinstance(series, dict):
                raise PlantGatewayError("网关遥测 series 格式错误")
            code = str(series.get("tag_code", "")).upper()
            if not code or not isinstance(series.get("points"), list):
                raise PlantGatewayError("网关遥测 series 缺少 tag_code 或 points")
            try:
                result[code] = [TelemetryPoint.model_validate(point) for point in series["points"]]
            except Exception as exc:
                raise PlantGatewayError(f"测点 {code} 的遥测点不符合契约") from exc
        missing = set(tag.upper() for tag in tag_codes) - set(result)
        if missing:
            raise PlantGatewayError(f"网关未返回请求的测点：{', '.join(sorted(missing))}")
        return result

    def get_alerts(self, unit_id: str, start_time: datetime, end_time: datetime) -> list[AlertEvent]:
        data = self._get(
            "v1/alerts",
            params={"unit_id": unit_id, "start": self._iso8601(start_time), "end": self._iso8601(end_time)},
        )
        if not isinstance(data, list):
            raise PlantGatewayError("网关预警响应必须为列表")
        try:
            return [AlertEvent.model_validate(item) for item in data]
        except Exception as exc:
            raise PlantGatewayError("网关预警数据不符合 PowerAgent 契约") from exc

    def default_end_time(self) -> datetime:
        return datetime.now(timezone.utc).astimezone()

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = urljoin(self.base_url, path)
        try:
            response = self.session.get(url, params=params, headers=self.headers, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise PlantGatewayError("只读数据网关暂不可用") from exc
        except (TypeError, ValueError) as exc:
            raise PlantGatewayError("只读数据网关返回了无法解析的 JSON") from exc
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    @staticmethod
    def _iso8601(value: datetime) -> str:
        if value.tzinfo is None:
            raise ValueError("查询时间必须包含时区")
        return value.isoformat()

    @staticmethod
    def _normalize_tag_definition(value: dict[str, Any]) -> dict[str, object]:
        tag_name = value.get("tag_name") or value.get("name")
        if not tag_name or not value.get("unit"):
            raise PlantGatewayError("网关测点字典必须包含 tag_name 和 unit")
        normalized: dict[str, object] = {"name": str(tag_name), "unit": str(value["unit"])}
        for key in ("warning_low", "warning_high", "alarm_low", "alarm_high", "setpoint_value"):
            if value.get(key) is not None:
                normalized[key] = float(value[key])
        return normalized

