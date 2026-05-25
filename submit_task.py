"""LinkFox API 提交任务模板 —— HMAC-SHA256 签名鉴权."""

import hashlib
import hmac
import time
from typing import Any

import requests

# ─── 配置 ────────────────────────────────────────────

API_BASE = "http://47.237.78.24:8000"
API_KEY = "fox_link"

# ─── 签名工具 ────────────────────────────────────────


def _sign(method: str, path: str) -> dict[str, str]:
    ts = str(int(time.time()))
    payload = f"{ts}.{method}.{path}"
    sig = hmac.new(
        API_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"X-Timestamp": ts, "X-Signature": sig}


def _post(path: str, body: dict[str, Any]) -> dict:
    url = f"{API_BASE}{path}"
    resp = requests.post(
        url, json=body, headers=_sign("POST", path), timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def _get(path: str) -> dict:
    url = f"{API_BASE}{path}"
    resp = requests.get(url, headers=_sign("GET", path), timeout=10)
    resp.raise_for_status()
    return resp.json()


# ─── API 方法 ─────────────────────────────────────────


def create_task(
    image_list: list[str],
    seller_point: str,
    provider: str = "GPT_2_IMAGE",
    
    priority: int = 5,
    sellerTypeNum: int = 1,
    callback_url: str | None = None,
) -> dict:
    return _post("/api/v1/tasks", {
        "image_list": image_list,
        "seller_point": seller_point,
        "provider": provider,
        "priority": priority,
        "sellerTypeNum": sellerTypeNum,
        "callback_url": callback_url,
    })


def get_task(task_id: str) -> dict:
    return _get(f"/api/v1/tasks/{task_id}")


def list_tasks(status: str | None = None, page: int = 1) -> dict:
    path = f"/api/v1/tasks?page={page}"
    if status:
        path += f"&status={status}"
    return _get(path)


def get_stats() -> dict:
    return _get("/api/v1/stats")


# ─── 示例 ─────────────────────────────────────────────

if __name__ == "__main__":
    result = create_task(
        image_list=[
            "https://cbu01.alicdn.com/img/ibank/O1CN01DZ98p41c2kLsLIUjP_!!2207065463543-0-cib.jpg_.webp",
        ],
        seller_point="生成一个shopfiy商品图片",
        callback_url="https://8.148.250.129/callback",
        sellerTypeNum=1,
    )
    print("创建结果:", result)
    task_id = result["data"]["task_id"]

    stats = get_stats()
    print("统计:", stats)

    task = get_task(task_id)
    print("任务详情:", task)
