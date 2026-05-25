"""状态轮询服务 —— 定期查询 LinkFox 任务状态并更新本地数据库。"""

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.task import Task
from app.services.linkfox import is_retryable_error, map_status, query_task

logger = logging.getLogger(__name__)
settings = get_settings()


def _poll_session_factory() -> async_sessionmaker[AsyncSession]:
    """为 poll 线程创建独立的数据库引擎和会话工厂。

    poll 线程运行在独立事件循环中，必须创建绑定到该循环的新引擎，
    否则 asyncpg 连接会因事件循环不匹配而抛出 RuntimeError。
    """
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=1,
        max_overflow=0,
    )
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


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
    async with _poll_session_factory()() as session:

        while datetime.now(timezone.utc).timestamp() < deadline:
            task = await _refresh_task(session, task_id)
            if task is None or task.status == "cancelled":
                logger.info("Polling stopped: task %s is %s", task_id, task.status if task else "missing")
                if task and task.status == "cancelled":
                    await _invoke_callback(task)
                return

            try:
                resp = query_task(task.linkfox_task_id)
                logger.info("LinkFox query response for %s: %s", task_id, resp)
                inner = resp.get("data") or resp
                inner_data = inner.get("data") or {}
                raw_status = inner_data.get("status") or inner.get("status")
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
                raw_results = inner_data.get("results") or inner_data.get("resultList") or inner.get("results") or []
                task.results = _normalize_results(raw_results)
                task.completed_at = now
                logger.info("Task %s completed", task_id)
                await session.commit()
                await _invoke_callback(task)
                return

            if remote_status == "failed":
                task.status = "failed"
                task.error_code = str(inner_data.get("errorCode") or inner.get("errorCode", ""))
                task.error_message = inner_data.get("errorMsg") or inner_data.get("message") or inner.get("errorMsg") or ""
                task.completed_at = now
                logger.info("Task %s failed: %s", task_id, task.error_message)
                await session.commit()
                await _invoke_callback(task)
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
            await _invoke_callback(task)
            logger.warning("Task %s timed out", task_id)


async def _refresh_task(session: AsyncSession, task_id: UUID) -> Task | None:
    session.expire_all()
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
            "error_code": task.error_code,
            "error_message": task.error_message,
        }
        # 用 asyncio.to_thread 避免阻塞事件循环
        resp = await asyncio.to_thread(
            requests.post, url, json=payload, timeout=10
        )
        resp.raise_for_status()
        logger.info("Callback to %s succeeded for task %s", url, task.id)
    except Exception:
        logger.exception("Callback to %s failed for task %s", url, task.id)


def _normalize_results(raw: list[dict]) -> list[dict]:
    """将 LinkFox 返回的结果列表标准化为 ImageResult Schema 格式。

    LinkFox 返回格式：
      {"id": "...", "status": 1, "url": "...", "width": 2048, "height": 2048,
       "format": "png", "extendField": {"type": "A+", "sellPoint": "..."}}

    Schema 格式：
      {"type": "A+", "url": "...", "width": 2048, "height": 2048, "format": "png"}
    """
    normalized = []
    for item in raw:
        ext = item.get("extendField") or {}
        normalized.append({
            "type": ext.get("type", ""),
            "url": item.get("url", ""),
            "width": item.get("width"),
            "height": item.get("height"),
            "format": item.get("format"),
        })
    return normalized
