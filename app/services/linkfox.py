"""LinkFox AI 作图 API 封装。

- 提交任务：POST /linkfox-ai/image/v2/make/productMarketMaterialV3
- 查询任务：POST /linkfox-ai/image/v2/make/info
- 状态码：1=排队中  2=生成中  3=成功  4=失败
"""

import logging
from typing import Any

import requests

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# ─── 字段映射：snake_case → camelCase ──────────────────

_FIELD_MAP = {
    "image_list": "imageList",
    "seller_point": "sellerPoint",
    "a_plus_num": "aPlusNum",
    "scene_type_num": "sceneTypeNum",
    "seller_type_num": "sellerTypeNum",
    "close_up_type_num": "closeUpTypeNum",
    "white_bg_type_num": "whiteBgTypeNum",
    "aspect_ratio": "aspectRatio",
    "callback_url": "callbackUrl",
    "provider": "provider",
    "resolution": "resolution",
}

# ─── LinkFox 状态码 → 内部状态 ─────────────────────────

_STATUS_MAP = {
    1: "queued",
    2: "processing",
    3: "completed",
    4: "failed",
}


def _snake_to_camel(params: dict[str, Any]) -> dict[str, Any]:
    """将 snake_case 参数字典转为 camelCase。"""
    result: dict[str, Any] = {}
    for key, value in params.items():
        camel = _FIELD_MAP.get(key, key)
        # 过滤掉优先级等仅内部使用的字段
        if key in ("priority",):
            continue
        if value is not None:
            result[camel] = value
    return result


# ─── 客户端工厂 ────────────────────────────────────────

_SESSION: requests.Session | None = None


def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update({
            "Authorization": f"Bearer {settings.linkfox_api_key}",
            "Content-Type": "application/json",
        })
    return _SESSION


def _get_url(path: str) -> str:
    """拼接完整 URL。"""
    base = settings.linkfox_api_base.rstrip("/")
    return f"{base}{path}"


# ─── API 操作 ──────────────────────────────────────────

def submit_task(params: dict[str, Any]) -> dict[str, Any]:
    """提交作图任务到 LinkFox API。

    Args:
        params: snake_case 作图参数（image_list, seller_point, provider 等）。

    Returns:
        {"linkfox_task_id": "2057762288106647552", ...}
    """
    body = _snake_to_camel(params)
    resp = _get_session().post(
        _get_url("/linkfox-ai/image/v2/make/productMarketMaterialV3"),
        json=body,
        timeout=30,
    )
    if not resp.ok:
        error_detail = ""
        try:
            error_detail = str(resp.json())
        except ValueError:
            error_detail = resp.text[:500]
        exc = requests.HTTPError(
            f"Client error '{resp.status_code} {resp.reason}' "
            f"for url: '{resp.url}' | response: {error_detail}"
        )
        exc.response = resp
        raise exc
    data = resp.json()
    logger.info("LinkFox submit_task response: code=%s", data.get("code"))
    return data


def query_task(linkfox_task_id: str) -> dict[str, Any]:
    """查询 LinkFox 任务状态（POST 方式）。

    Args:
        linkfox_task_id: LinkFox 端任务 ID。

    Returns:
        {"code": 0, "data": {"status": 3, ...}, ...}

    status: 1=排队中, 2=生成中, 3=成功, 4=失败
    """
    resp = _get_session().post(
        _get_url("/linkfox-ai/image/v2/make/info"),
        json={"id": linkfox_task_id},
        timeout=30,
    )
    if not resp.ok:
        error_detail = ""
        try:
            error_detail = str(resp.json())
        except ValueError:
            error_detail = resp.text[:500]
        exc = requests.HTTPError(
            f"Client error '{resp.status_code} {resp.reason}' "
            f"for url: '{resp.url}' | response: {error_detail}"
        )
        exc.response = resp
        raise exc
    data = resp.json()
    return data


def map_status(linkfox_status: int) -> str:
    """将 LinkFox 数字状态码映射为内部状态字符串。"""
    return _STATUS_MAP.get(linkfox_status, "unknown")


def is_retryable_error(exc: Exception) -> bool:
    """判断异常是否可重试。"""
    if isinstance(exc, requests.HTTPError):
        return exc.response is not None and exc.response.status_code in RETRYABLE_STATUSES
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    return False
