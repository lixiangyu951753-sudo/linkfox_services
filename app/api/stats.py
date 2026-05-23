"""统计数据 API 路由。"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.schemas import ApiResponse, StatsResponse
from app.models.task import Task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["stats"])


@router.get("/stats", response_model=ApiResponse)
async def get_stats(db: AsyncSession = Depends(get_db)) -> ApiResponse:
    """查询任务统计数据。"""
    # 各状态计数
    stmt = (
        select(Task.status, func.count(Task.id))
        .group_by(Task.status)
    )
    rows = (await db.execute(stmt)).all()
    counts = {row[0]: row[1] for row in rows}

    pending = counts.get("pending", 0)
    queued = counts.get("queued", 0)
    processing = counts.get("processing", 0)
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    cancelled = counts.get("cancelled", 0)

    total_finished = completed + failed
    success_rate = round(completed / total_finished * 100, 2) if total_finished > 0 else 0.0

    # 平均处理时间
    avg_seconds: float = 0.0
    avg_q = select(
        func.avg(
            func.extract("epoch", Task.completed_at) - func.extract("epoch", Task.started_at)
        )
    ).where(
        Task.status == "completed",
        Task.started_at.isnot(None),
        Task.completed_at.isnot(None),
    )
    row = (await db.execute(avg_q)).scalar_one_or_none()
    if row:
        avg_seconds = float(row)

    if avg_seconds < 60:
        avg_str = f"{int(avg_seconds)}s"
    else:
        mins = int(avg_seconds // 60)
        secs = int(avg_seconds % 60)
        avg_str = f"{mins}m {secs}s"

    return ApiResponse(
        data=StatsResponse(
            pending=pending,
            queued=queued,
            processing=processing,
            completed=completed,
            failed=failed,
            cancelled=cancelled,
            success_rate=success_rate,
            avg_process_time=avg_str,
        ).model_dump()
    )
