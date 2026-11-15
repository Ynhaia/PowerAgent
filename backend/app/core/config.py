"""集中管理 PowerAgent 的运行配置。

默认运行于 mock 模式。只有显式配置 http 或 postgres 数据源时，应用才会访问外部数据。
"""

from dataclasses import dataclass
import os

from dotenv import load_dotenv


# 只读取 PowerAgent 项目运行目录内的 .env；实际密钥只存在于本机后端环境中。
load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = "PowerAgent"
    api_prefix: str = "/api/v1"
    environment: str = os.getenv("POWERAGENT_ENV", "development")
    llm_base_url: str = os.getenv("POWERAGENT_LLM_BASE_URL", "https://api.deepseek.com")
    llm_model: str = os.getenv("POWERAGENT_LLM_MODEL", "deepseek-chat")
    llm_api_key: str | None = os.getenv("POWERAGENT_LLM_API_KEY")
    data_source: str = os.getenv("POWERAGENT_DATA_SOURCE", "mock").lower()
    mock_scenario: str = os.getenv("POWERAGENT_MOCK_SCENARIO", "cw_flow_insufficient")
    plant_api_base_url: str | None = os.getenv("POWERAGENT_PLANT_API_BASE_URL")
    plant_api_token: str | None = os.getenv("POWERAGENT_PLANT_API_TOKEN")
    plant_api_timeout_seconds: float = float(os.getenv("POWERAGENT_PLANT_API_TIMEOUT_SECONDS", "5"))
    database_url: str | None = os.getenv("POWERAGENT_DATABASE_URL")
    trace_store: str = os.getenv("POWERAGENT_TRACE_STORE", "memory").lower()

    @property
    def run_mode(self) -> str:
        return self.data_source

    @property
    def plant_gateway_enabled(self) -> bool:
        return self.data_source == "http" and bool(self.plant_api_base_url)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url and "change-me" not in self.database_url)

    @property
    def persistent_trace_enabled(self) -> bool:
        return self.trace_store == "postgres" and self.database_configured


settings = Settings()
