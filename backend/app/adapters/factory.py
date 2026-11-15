"""根据配置选择数据源，默认保持为模拟数据。"""

from .contracts import PlantDataAdapter
from .http_plant import HttpPlantDataAdapter
from .scenario_plant import ScenarioPlantDataAdapter
from ..core.config import Settings, settings
from ..database.postgres import PostgresPlantDataAdapter, PostgresPlantDataRepository


def create_plant_data_adapter(config: Settings = settings) -> PlantDataAdapter:
    if config.data_source == "mock":
        return ScenarioPlantDataAdapter(config.mock_scenario)
    if config.data_source == "http":
        if not config.plant_api_base_url:
            raise ValueError("POWERAGENT_DATA_SOURCE=http 时必须配置 POWERAGENT_PLANT_API_BASE_URL")
        return HttpPlantDataAdapter(
            base_url=config.plant_api_base_url,
            token=config.plant_api_token,
            timeout_seconds=config.plant_api_timeout_seconds,
        )
    if config.data_source == "postgres":
        if not config.database_configured:
            raise ValueError("POWERAGENT_DATA_SOURCE=postgres 时必须配置有效的 POWERAGENT_DATABASE_URL")
        return PostgresPlantDataAdapter(PostgresPlantDataRepository(config.database_url or ""))
    raise ValueError("POWERAGENT_DATA_SOURCE 仅支持 mock、http 或 postgres")
