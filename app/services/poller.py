"""状态轮询服务 —— 定期查询 LinkFox 任务状态并更新本地数据库。"""

import asyncio
import ipaddress
import logging
import urllib3
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.task import Task
from app.services.linkfox import is_retryable_error, map_status, query_task
from app.services.task_queue import release_slot

logger = logging.getLogger(__name__)
settings = get_settings()

_poll_factory: async_sessionmaker[AsyncSession] | None = None

_SSRF_BLOCKED = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _get_poll_session_factory() -> async_sessionmaker[AsyncSession]:
    global _poll_factory
    if _poll_factory is None:
        engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=1,
            max_overflow=0,
        )
        _poll_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return _poll_factory


async def poll_task(task_id: UUID) -> None:
    """轮询单个任务直到终态或超时。"""
    await asyncio.sleep(10)

    deadline = datetime.now(timezone.utc).timestamp() + settings.task_timeout_minutes * 60
    async with _get_poll_session_factory()() as session:

        while datetime.now(timezone.utc).timestamp() < deadline:
            task = await _refresh_task(session, task_id)
            if task is None or task.status == "cancelled":
                logger.info("Polling stopped: task %s is %s", task_id, task.status if task else "missing")
                if task and task.status == "cancelled":
                    await _invoke_callback(task)
                await release_slot()
                return

            try:
                resp = query_task(task.linkfox_task_id)
                logger.debug("LinkFox query response for %s: %s", task_id, resp)
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
                await release_slot()
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
                await release_slot()
                return

            if remote_status == "failed":
                task.status = "failed"
                task.error_code = str(inner_data.get("errorCode") or inner.get("errorCode", ""))
                task.error_message = inner_data.get("errorMsg") or inner_data.get("message") or inner.get("errorMsg") or ""
                task.completed_at = now
                logger.info("Task %s failed: %s", task_id, task.error_message)
                await session.commit()
                await _invoke_callback(task)
                await release_slot()
                return

            if remote_status == "processing" and task.status != "processing":
                task.status = "processing"
                task.started_at = task.started_at or now
            elif remote_status == "queued" and task.status not in ("processing", "queued"):
                task.status = "queued"

            await session.commit()
            await asyncio.sleep(settings.poll_interval)

        task = await _refresh_task(session, task_id)
        if task and task.status not in ("completed", "failed", "cancelled"):
            task.status = "failed"
            task.error_message = f"Task timed out after {settings.task_timeout_minutes} minutes"
            task.completed_at = datetime.now(timezone.utc)
            await session.commit()
            await _invoke_callback(task)
            logger.warning("Task %s timed out", task_id)
        await release_slot()


async def _refresh_task(session: AsyncSession, task_id: UUID) -> Task | None:
    session.expire_all()
    return await session.get(Task, task_id)


async def _invoke_callback(task: Task) -> None:
    if not task.params:
        return
    url = (task.params or {}).get("callback_url")
    if not url:
        return
    if not _is_safe_url(url):
        logger.warning("Callback URL %s blocked by SSRF check for task %s", url, task.id)
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
            requests.post, url, json=payload, timeout=10, verify=False
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


def _is_safe_url(url: str) -> bool:
    """校验回调 URL 不指向内网/回环地址，防止 SSRF 攻击。"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        ip = ipaddress.ip_address(hostname)
        return not any(ip in net for net in _SSRF_BLOCKED)
    except ValueError:
        return True
