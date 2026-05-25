"""LinkFox AI 后端服务 —— FastAPI 入口。"""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware

from app.api.stats import router as stats_router
from app.api.tasks import router as tasks_router
from app.core.config import get_settings
from app.core.database import engine
from app.models.task import Base  # noqa: F401  确保模型注册

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    # 启动时创建表（生产环境应使用 Alembic 迁移）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured")
    yield
    # 关闭时释放连接
    await engine.dispose()
    logger.info("Database connections closed")


app = FastAPI(
    title="LinkFox AI Backend",
    description="智能商品套图生成系统 API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(",") if settings.cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
app.include_router(tasks_router)
app.include_router(stats_router)

# 静态文件 & 管理端
_DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
if _DOCS_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_DOCS_DIR)), name="static")

    from fastapi.responses import FileResponse

    @app.get("/manager")
    async def manager():
        return FileResponse(_DOCS_DIR / "manager.html")


@app.get("/health")
async def health_check():
    """健康检查端点。"""
    return {"status": "ok"}


# ─── 直接运行 ─────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
