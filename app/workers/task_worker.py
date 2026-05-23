"""Celery Worker —— 从 Redis 队列消费任务，调用 LinkFox API 并启动轮询。"""

import asyncio
import logging
import threading
from datetime import datetime, timezone
from uuid import UUID

from celery import Celery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import async_session_factory
from app.models.task import Task, TaskLog
from app.services.linkfox import is_retryable_error, submit_task
from app.services.poller import poll_task
from app.services.task_queue import acquire_slot, dequeue, release_slot

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery(
    "linkfox_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
)

# 重试退避（秒）
_RETRY_BACKOFF = [10, 30, 60]


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def process_next(self, task_id: str | None = None) -> None:
    """从 Redis 队列取出一个任务并处理。

    首次调用不传 task_id（从队列取）；重试时通过 Celery kwargs 传递，
    避免重新入队导致的并发冲突。
    """
    return asyncio.run(_process_next_async(
        retries=self.request.retries,
        task_id=UUID(task_id) if task_id else None,
    ))


async def _process_next_async(retries: int, task_id: UUID | None = None) -> None:
    # 1. 尝试获取并发槽位
    if not await acquire_slot():
        logger.debug("No concurrency slot available, requeueing")
        process_next.apply_async(countdown=settings.poll_interval)
        return

    try:
        # 2. 确定 task_id：重试时用传入的，否则从队列取
        if task_id is None:
            task_id = await dequeue()
            if task_id is None:
                logger.debug("Queue empty, nothing to process")
                return

        # 3. 从数据库加载任务
        async with async_session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None or task.status == "cancelled":
                logger.info("Task %s not found or cancelled, skip", task_id)
                return

            # 检查是否超过最大重试次数
            if retries >= task.max_retries:
                task.status = "failed"
                task.error_message = "Exceeded max submission retries"
                task.completed_at = datetime.now(timezone.utc)
                _add_log(session, task, "error", "Exceeded max submission retries")
                await session.commit()
                return

            # 构建 LinkFox 请求参数
            request_params = _build_linkfox_params(task)

            try:
                # 4. 提交到 LinkFox API
                resp = submit_task(request_params)
                inner = resp.get("data") or resp
                task.linkfox_task_id = str(inner.get("id", ""))
                task.status = "processing"
                task.started_at = datetime.now(timezone.utc)
                _add_log(
                    session,
                    task,
                    "info",
                    f"Submitted to LinkFox, linkfox_task_id={task.linkfox_task_id}",
                )
                await session.commit()

            except Exception as exc:
                if is_retryable_error(exc):
                    _add_log(session, task, "warning", f"Submission retryable error: {exc}")
                    task.status = "queued"
                    await session.commit()
                    # 通过 Celery kwargs 传递 task_id，避免重新入队造成并发冲突
                    delay = _RETRY_BACKOFF[min(retries, len(_RETRY_BACKOFF) - 1)]
                    raise process_next.retry(
                        kwargs={"task_id": str(task_id)},
                        countdown=delay,
                    ) from exc

                # 不可重试的错误
                task.status = "failed"
                task.error_message = str(exc)
                task.completed_at = datetime.now(timezone.utc)
                _add_log(session, task, "error", f"Submission failed: {exc}")
                await session.commit()
                return

        # 5. 在独立 daemon 线程中启动轮询
        #    避免 asyncio.create_task 在 asyncio.run() 退出时被静默取消。
        #    线程内调用 asyncio.run(poll_task(...)) 创建独立事件循环，
        #    同步 HTTP 调用在线程中阻塞不影响主 worker。
        threading.Thread(
            target=_run_poll_in_thread,
            args=(task_id,),
            name=f"poll-{task_id}",
            daemon=True,
        ).start()

    finally:
        await release_slot()


def _run_poll_in_thread(task_id: UUID) -> None:
    """在独立线程中运行异步轮询。"""
    try:
        asyncio.run(poll_task(task_id))
    except Exception:
        logger.exception("Poll thread for task %s crashed", task_id)


# ─── 辅助 ──────────────────────────────────────────────

def _build_linkfox_params(task: Task) -> dict:
    """将 Task 的 params 字段展平为 LinkFox API 所需的请求体。"""
    merged = {
        "image_list": task.image_list or [],
        "seller_point": task.seller_point or "",
        "provider": task.provider or "GPT_2_IMAGE",
    }
    if task.params:
        for k, v in task.params.items():
            if k not in ("image_list", "seller_point", "provider"):
                merged[k] = v
    return merged


def _add_log(session: AsyncSession, task: Task, level: str, message: str) -> None:
    """添加任务日志（同步 ORM 操作，仅用于已有 session 场景）。"""
    entry = TaskLog(task_id=task.id, level=level, message=message)
    session.add(entry)
