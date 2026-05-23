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
from app.services.linkfox import is_retryable_error, map_status, query_task

logger = logging.getLogger(__name__)
settings = get_settings()


async def poll_task(task_id: UUID) -> None:
    """轮询单个任务直到终态或超时。

    策略：
      - 初始延迟 10 秒后开始查询
      - 按配置的 poll_interval 间隔轮询
      - 总超时按 task_timeout_minutes 计算
      - LinkFox 状态码：1=排队中 2=生成中 3=成功 4=失败
    """
    await asyncio.sleep(10)  # 初始延迟

    deadline = datetime.now(timezone.utc).timestamp() + settings.task_timeout_minutes * 60
    async with async_session_factory() as session:

        while datetime.now(timezone.utc).timestamp() < deadline:
            task = await _refresh_task(session, task_id)
            if task is None or task.status == "cancelled":
                logger.info("Polling stopped: task %s is %s", task_id, task.status if task else "missing")
                return

            try:
                resp = query_task(task.linkfox_task_id)
                inner = resp.get("data") or resp
                raw_status = inner.get("status")
                remote_status = map_status(raw_status) if raw_status is not None else "unknown"
            except Exception as exc:
                if is_retryable_error(exc):
                    logger.warning("Poll task %s retryable error: %s", task_id, exc)
                    await asyncio.sleep(settings.poll_interval)
                    continue
                logger.error("Poll task %s unrecoverable error: %s", task_id, exc)
                break

            now = datetime.now(timezone.utc)

            if remote_status == "completed":
                task.status = "completed"
                task.results = inner.get("results") or inner.get("resultList", [])
                task.completed_at = now
                logger.info("Task %s completed", task_id)
                await session.commit()
                await _invoke_callback(task)
                return

            if remote_status == "failed":
                task.status = "failed"
                task.error_code = str(inner.get("errorCode", ""))
                task.error_message = inner.get("errorMsg") or inner.get("message", "")
                task.completed_at = now
                logger.info("Task %s failed: %s", task_id, task.error_message)
                await session.commit()
                return

            # queued / processing 中间状态
            if remote_status == "processing" and task.status != "processing":
                task.status = "processing"
                task.started_at = task.started_at or now
            elif remote_status == "queued" and task.status not in ("processing", "queued"):
                task.status = "queued"

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
    await session.expire_all()
    return await session.get(Task, task_id)


async def _invoke_callback(task: Task) -> None:
    if not task.params:
        return
    url = (task.params or {}).get("callback_url")
    if not url:
        return

    try:
        import requests

        payload = {
            "task_id": str(task.id),
            "status": task.status,
            "results": task.results,
        }
        # 用 asyncio.to_thread 避免阻塞事件循环
        resp = await asyncio.to_thread(
            requests.post, url, json=payload, timeout=10
        )
        resp.raise_for_status()
        logger.info("Callback to %s succeeded for task %s", url, task.id)
    except Exception:
        logger.exception("Callback to %s failed for task %s", url, task.id)
