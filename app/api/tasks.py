"""任务管理 API 路由。"""

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.schemas import (
    ApiResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDetailResponse,
    TaskListItem,
    TaskListResponse,
)
from app.models.task import Task
from app.services.task_queue import enqueue, queue_length, queue_position, remove_from_queue
from app.workers.task_worker import process_next

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("", response_model=ApiResponse)
async def create_task(
    body: TaskCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """创建新的作图任务，写入数据库并加入 Redis 队列。"""
    task = Task(
        seller_point=body.seller_point,
        provider=body.provider,
        priority=body.priority,
        image_list=body.image_list,
        params=body.model_dump(exclude={"image_list", "seller_point", "provider", "priority"}),
    )
    db.add(task)
    await db.flush()  # 获取 task.id

    await enqueue(task.id)
    await db.commit()

    pos = await queue_position(task.id)
    qlen = await queue_length()

    # 估算等待时间
    estimated = "pending"
    if pos >= 0:
        # 简化：假设每个任务平均 3 分钟
        mins = max(0, pos * 3)
        estimated = f"{mins}min" if mins >= 1 else "<1min"

    # 触发 worker
    process_next.delay()

    return ApiResponse(
        data=TaskCreateResponse(
            task_id=task.id,
            status=task.status,
            queue_position=pos + 1,  # 人类可读：第 1 位
            estimated_wait=estimated,
        ).model_dump()
    )


@router.get("/{task_id}", response_model=ApiResponse)
async def get_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """查询单个任务详情。"""
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    return ApiResponse(data=TaskDetailResponse.model_validate(task).model_dump())


@router.get("", response_model=ApiResponse)
async def list_tasks(
    status: str | None = Query(None, description="状态筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """分页查询任务列表，支持状态筛选。"""
    # count
    count_q = select(func.count(Task.id))
    if status:
        count_q = count_q.where(Task.status == status)
    total = (await db.execute(count_q)).scalar_one()

    # items
    q = select(Task).order_by(Task.created_at.desc())
    if status:
        q = q.where(Task.status == status)
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()

    items = [TaskListItem.model_validate(r) for r in rows]

    return ApiResponse(
        data=TaskListResponse(
            total=total,
            page=page,
            page_size=page_size,
            items=items,
        ).model_dump()
    )


@router.delete("/{task_id}", response_model=ApiResponse)
async def cancel_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """取消任务。只有 pending/queued 状态的任务可以取消。"""
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in ("pending", "queued"):
        raise HTTPException(status_code=400, detail="Only pending/queued tasks can be cancelled")

    task.status = "cancelled"
    task.completed_at = datetime.now(task.completed_at.tzinfo) if task.completed_at else datetime.utcnow()
    await remove_from_queue(task.id)
    await db.commit()

    return ApiResponse(data={"task_id": str(task.id), "status": "cancelled"})
