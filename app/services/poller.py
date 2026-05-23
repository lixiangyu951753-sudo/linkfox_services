"""状态轮询服务 —— 定期查询 LinkFox 任务状态并更新本地数据库。"""

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import async_session_factory
from app.models.task import Task
from app.services.linkfox import is_retryable_error, query_task

logger = logging.getLogger(__name__)
settings = get_settings()


async def poll_task(task_id: UUID) -> None:
    """轮询单个任务直到终态或超时。

    策略：
      - 初始延迟 10 秒后开始查询
      - 按配置的 poll_interval 间隔轮询
      - 总超时按 task_timeout_minutes 计算
      - 将本地 status 同步为 LinkFox 返回的状态
      - 如果 LinkFox 已返回终态，写入 results
    """
    await asyncio.sleep(10)  # 初始延迟

    deadline = datetime.now(timezone.utc).timestamp() + settings.task_timeout_minutes * 60
    async with async_session_factory() as session:

        while datetime.now(timezone.utc).timestamp() < deadline:
            # 先刷新本地记录，确认没有被取消
            task = await _refresh_task(session, task_id)
            if task is None or task.status in ("cancelled",):
                logger.info("Polling stopped: task %s is %s", task_id, task.status if task else "missing")
                return

            try:
                data = query_task(task.linkfox_task_id)
                remote_status = data.get("status", "").lower()
            except Exception as exc:
                if is_retryable_error(exc):
                    logger.warning("Poll task %s retryable error: %s", task_id, exc)
                    await asyncio.sleep(settings.poll_interval)
                    continue
                logger.error("Poll task %s unrecoverable error: %s", task_id, exc)
                break

            # 状态映射 & 本地更新
            now = datetime.now(timezone.utc)
            if remote_status in ("completed", "success", "done"):
                task.status = "completed"
                task.results = data.get("results", [])
                task.completed_at = now
                logger.info("Task %s completed", task_id)
                await session.commit()

                # 调用回调
                await _invoke_callback(task)
                return

            if remote_status in ("failed", "error"):
                task.status = "failed"
                task.error_code = data.get("error_code")
                task.error_message = data.get("error_message")
                task.completed_at = now
                logger.info("Task %s failed: %s", task_id, task.error_message)
                await session.commit()
                return

            # processing / queued 等中间状态，继续轮询
            if task.status != "processing" and remote_status in (
                "processing", "running", "in_progress"
            ):
                task.status = "processing"
                task.started_at = task.started_at or now

            await session.commit()
            await asyncio.sleep(settings.poll_interval)

        # 超时
        task = await _refresh_task(session, task_id)
        if task and task.status not in ("completed", "failed", "cancelled"):
            task.status = "failed"
            task.error_message = f"Task timed out after {settings.task_timeout_minutes} minutes"
            task.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.warning("Task %s timed out", task_id)


async def _refresh_task(session: AsyncSession, task_id: UUID) -> Task | None:
    """刷新 task 对象使其脱离过期态。"""
    await session.expire_all()
    return await session.get(Task, task_id)


async def _invoke_callback(task: Task) -> None:
    """调用业务方配置的 callback_url（异步发起，不阻塞轮询）。"""
    if not task.params:
        return
    url = (task.params or {}).get("callback_url")
    if not url:
        return

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "task_id": str(task.id),
                "status": task.status,
                "results": task.results,
            }
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info("Callback to %s succeeded for task %s", url, task.id)
    except Exception:
        logger.exception("Callback to %s failed for task %s", url, task.id)
