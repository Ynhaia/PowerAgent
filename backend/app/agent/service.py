"""给 API 层使用的 Agent 服务装配。"""

from ..adapters.contracts import PlantDataAdapter
from ..core.config import Settings, settings
from ..database.postgres import PostgresTraceRepository
from ..services.knowledge_service import KnowledgeService
from ..services.trace_service import InMemoryTraceService, PostgresTraceService, TraceService
from .graph import PowerAgentGraph


class AgentService:
    def __init__(self, adapter: PlantDataAdapter, config: Settings = settings) -> None:
        self.trace_service: TraceService = self._create_trace_service(config)
        self.knowledge_service = KnowledgeService.default()
        self.graph = PowerAgentGraph(adapter, self.trace_service, self.knowledge_service, config)

    @staticmethod
    def _create_trace_service(config: Settings) -> TraceService:
        if config.trace_store == "memory":
            return InMemoryTraceService()
        if config.trace_store == "postgres":
            if not config.database_configured:
                raise ValueError("POWERAGENT_TRACE_STORE=postgres 时必须配置有效的 POWERAGENT_DATABASE_URL")
            return PostgresTraceService(PostgresTraceRepository(config.database_url or ""))
        raise ValueError("POWERAGENT_TRACE_STORE 仅支持 memory 或 postgres")
