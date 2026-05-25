"""API 鉴权 —— HMAC-SHA256 签名校验，不直接传输 API Key。"""

import hashlib
import hmac
import logging
import time

from fastapi import HTTPException, Request

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SIG_HEADER = "X-Signature"
TS_HEADER = "X-Timestamp"
WINDOW_SECONDS = 300


def _compute_signature(api_key: str, timestamp: str, method: str, path: str) -> str:
    payload = f"{timestamp}.{method}.{path}"
    return hmac.new(
        api_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def verify_auth(request: Request) -> None:
    if not settings.api_key:
        logger.warning("Auth disabled: API_KEY not configured")
        return

    sig = request.headers.get(SIG_HEADER, "")
    ts_str = request.headers.get(TS_HEADER, "")

    if not sig or not ts_str:
        raise HTTPException(status_code=401, detail="Missing X-Signature or X-Timestamp header")

    try:
        ts = int(ts_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid X-Timestamp")

    now = int(time.time())
    if abs(now - ts) > WINDOW_SECONDS:
        logger.warning("Auth failed: timestamp expired (now=%s, req=%s)", now, ts)
        raise HTTPException(status_code=401, detail="X-Timestamp expired")

    expected = _compute_signature(
        settings.api_key,
        ts_str,
        request.method.upper(),
        request.url.path,
    )

    if not hmac.compare_digest(sig, expected):
        client_ip = request.client.host if request.client else "unknown"
        logger.warning("Auth failed: signature mismatch for IP %s", client_ip)
        raise HTTPException(status_code=401, detail="Unauthorized")
