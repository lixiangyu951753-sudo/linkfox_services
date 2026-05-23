"""LinkFox AI 作图 API 封装。

提供任务提交和状态查询两个核心接口。
"""

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# ─── 可重试的 HTTP 状态码 ──────────────────────────────

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


# ─── 客户端工厂 ────────────────────────────────────────

def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.linkfox_api_base,
        headers={
            "Authorization": f"Bearer {settings.linkfox_api_key}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(30.0),
    )


# ─── API 操作 ──────────────────────────────────────────

def submit_task(params: dict[str, Any]) -> dict[str, Any]:
    """提交作图任务到 LinkFox API。

    Args:
        params: 作图参数（含 image_list、seller_point、provider 等）。

    Returns:
        {"task_id": "2008750323222487040", ...}

    Raises:
        httpx.HTTPStatusError: 非重试状态码。
    """
    with _client() as client:
        resp = client.post("/api/v1/tasks", json=params)
        resp.raise_for_status()
        data = resp.json()
        logger.info("LinkFox submit_task succeeded, response=%s", data)
        return data


def query_task(linkfox_task_id: str) -> dict[str, Any]:
    """查询 LinkFox 任务状态。

    Args:
        linkfox_task_id: LinkFox 端任务 ID。

    Returns:
        {"status": "completed", "results": [...], ...}

    Raises:
        httpx.HTTPStatusError: 非重试状态码。
    """
    with _client() as client:
        resp = client.get(f"/api/v1/tasks/{linkfox_task_id}")
        resp.raise_for_status()
        data = resp.json()
        return data


def is_retryable_error(exc: Exception) -> bool:
    """判断异常是否可重试。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUSES
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    return False
