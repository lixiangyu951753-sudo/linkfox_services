"""任务队列服务 —— 队列管理、并发控制、排位估算。"""

import logging
from uuid import UUID

import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

CONCURRENT_LOCK_KEY = "linkfox:concurrent:count"
QUEUE_COUNTER_KEY = "linkfox:queue:counter"


def _create_redis() -> aioredis.Redis:
    return aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=20,
    )


async def _close_redis(r: aioredis.Redis) -> None:
    try:
        await r.aclose()
    except Exception:
        pass


class RedisPool:
    """局部的 Redis 连接池，用完调用 dispose() 释放。"""

    def __init__(self):
        self._redis = _create_redis()

    @property
    def client(self) -> aioredis.Redis:
        return self._redis

    async def dispose(self) -> None:
        await _close_redis(self._redis)


# ─── 并发控制 ──────────────────────────────────────────

_ACQUIRE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current >= tonumber(ARGV[1]) then
    return 0
end
redis.call('INCR', KEYS[1])
return 1
"""


async def acquire_slot(r: aioredis.Redis) -> bool:
    """尝试获取一个并发槽位。使用 Lua 脚本保证原子性。"""
    result = await r.eval(_ACQUIRE_LUA, 1, CONCURRENT_LOCK_KEY, settings.max_concurrent_tasks)
    return result == 1


async def release_slot(r: aioredis.Redis) -> None:
    """释放一个并发槽位。"""
    val = int(await r.get(CONCURRENT_LOCK_KEY) or 0)
    if val > 0:
        await r.decr(CONCURRENT_LOCK_KEY)


async def active_slots(r: aioredis.Redis) -> int:
    """当前活跃并发数。"""
    return int(await r.get(CONCURRENT_LOCK_KEY) or 0)


# ─── 队列 ──────────────────────────────────────────────

async def enqueue(r: aioredis.Redis, task_id: UUID) -> None:
    """将任务 ID 加入 Redis 队列。"""
    await r.rpush(QUEUE_COUNTER_KEY, str(task_id))


async def dequeue(r: aioredis.Redis) -> UUID | None:
    """从队列头部取出一个任务 ID。"""
    raw = await r.lpop(QUEUE_COUNTER_KEY)
    return UUID(raw) if raw else None


async def queue_length(r: aioredis.Redis) -> int:
    """当前队列长度。"""
    return await r.llen(QUEUE_COUNTER_KEY)


async def queue_position(r: aioredis.Redis, task_id: UUID) -> int:
    """查询某任务在队列中的位置（0-based，0 表示队首）。"""
    items = await r.lrange(QUEUE_COUNTER_KEY, 0, -1)
    try:
        return items.index(str(task_id))
    except ValueError:
        return -1


async def remove_from_queue(r: aioredis.Redis, task_id: UUID) -> bool:
    """从队列中移除任务。返回 True 表示成功移除。"""
    removed = await r.lrem(QUEUE_COUNTER_KEY, 0, str(task_id))
    return removed > 0
