"""PowerAgent FastAPI 应用入口。"""

from fastapi import FastAPI

from .api.routes import router
from .core.config import settings


app = FastAPI(
    title="PowerAgent 电厂智能预警与故障诊断系统",
    version="0.1.0",
    description="1 号机凝汽器真空下降专题的基础服务骨架。",
)
app.include_router(router, prefix=settings.api_prefix, tags=["PowerAgent"])

