"""任务队列服务 —— 队列管理、并发控制、排位估算。"""

import logging
from uuid import UUID

import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

CONCURRENT_LOCK_KEY = "linkfox:concurrent:count"
QUEUE_COUNTER_KEY = "linkfox:queue:counter"

_redis_pool: aioredis.Redis | None = None

async def _redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=20,
        )
    return _redis_pool


# ─── 并发控制 ──────────────────────────────────────────

_ACQUIRE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current >= tonumber(ARGV[1]) then
    return 0
end
redis.call('INCR', KEYS[1])
return 1
"""

async def acquire_slot() -> bool:
    """尝试获取一个并发槽位。使用 Lua 脚本保证原子性。"""
    r = await _redis()
    result = await r.eval(_ACQUIRE_LUA, 1, CONCURRENT_LOCK_KEY, settings.max_concurrent_tasks)
    return result == 1


async def release_slot() -> None:
    """释放一个并发槽位。"""
    r = await _redis()
    val = int(await r.get(CONCURRENT_LOCK_KEY) or 0)
    if val > 0:
        await r.decr(CONCURRENT_LOCK_KEY)


async def active_slots() -> int:
    """当前活跃并发数。"""
    r = await _redis()
    return int(await r.get(CONCURRENT_LOCK_KEY) or 0)


# ─── 队列 ──────────────────────────────────────────────

async def enqueue(task_id: UUID) -> None:
    """将任务 ID 加入 Redis 队列。"""
    r = await _redis()
    await r.rpush(QUEUE_COUNTER_KEY, str(task_id))


async def dequeue() -> UUID | None:
    """从队列头部取出一个任务 ID。"""
    r = await _redis()
    raw = await r.lpop(QUEUE_COUNTER_KEY)
    return UUID(raw) if raw else None


async def queue_length() -> int:
    """当前队列长度。"""
    r = await _redis()
    return await r.llen(QUEUE_COUNTER_KEY)


async def queue_position(task_id: UUID) -> int:
    """查询某任务在队列中的位置（0-based，0 表示队首）。"""
    r = await _redis()
    items = await r.lrange(QUEUE_COUNTER_KEY, 0, -1)
    try:
        return items.index(str(task_id))
    except ValueError:
        return -1


async def remove_from_queue(task_id: UUID) -> bool:
    """从队列中移除任务。返回 True 表示成功移除。"""
    r = await _redis()
    removed = await r.lrem(QUEUE_COUNTER_KEY, 0, str(task_id))
    return removed > 0
