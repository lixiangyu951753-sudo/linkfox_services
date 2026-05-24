# LinkFox AI 后端 — 代码审计报告

> 审计日期：2026-05-23
> 审计范围：全项目源代码
> 审计方法：静态代码分析 + 双 Agent 交叉验证

---

## 审计概览

| 统计 | 数量 |
|------|------|
| 🔴 Critical | 3 |
| 🟠 Major | 3 |
| 🟡 Minor | 4 |
| **合计** | **10** |

---

## 问题清单

### 🔴 Critical — #1 并发控制竞态

**文件**：`app/services/task_queue.py` L24-L31

**问题**：`acquire_slot()` 中 `get` + `incr` 不是原子操作。高并发下多个 Worker 可能同时读到 `current < max`，都通过检查并递增，导致实际并发数超过 `max_concurrent_tasks` 限制。

**当前代码**：
```python
async def acquire_slot() -> bool:
    r = await _redis()
    current = int(await r.get(CONCURRENT_LOCK_KEY) or 0)  # ← 步骤1：读
    if current >= settings.max_concurrent_tasks:
        return False
    await r.incr(CONCURRENT_LOCK_KEY)  # ← 步骤2：写（非原子）
    return True
```

**建议**：使用 Redis Lua 脚本实现原子的"检查+递增"：
```python
_ACQUIRE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current >= tonumber(ARGV[1]) then
    return 0
end
redis.call('INCR', KEYS[1])
return 1
"""

async def acquire_slot() -> bool:
    r = await _redis()
    result = await r.eval(_ACQUIRE_SCRIPT, 1, CONCURRENT_LOCK_KEY, settings.max_concurrent_tasks)
    return result == 1
```

---

### 🔴 Critical — #2 并发槽位释放时机错误

**文件**：`app/workers/task_worker.py` L137-L138

**问题**：`release_slot()` 在 `finally` 块中执行，意味着 `submit_task()` 成功后就释放槽位，但任务仍在 LinkFox 生成中（5-10 分钟）。并发控制只保护了"提交"动作，没有保护"生成中"状态。10 个任务可以同时跑，`max_concurrent_tasks=3` 的限制形同虚设。

**当前代码**：
```python
try:
    ...
    resp = submit_task(request_params)  # 提交成功
    ...
    threading.Thread(target=_run_poll_in_thread, ...).start()  # 启动轮询
finally:
    await release_slot()  # ← 立即释放！任务还在生成中！
```

**建议**：将 `release_slot()` 移到轮询结束后调用：
```python
# 不在 finally 中释放，改为在 poll_task 完成后释放
threading.Thread(
    target=_run_poll_in_thread,
    args=(task_id,),
    ...
).start()
# 移除 finally 中的 release_slot()
```

在 `poll_task()` 终态处理中添加：
```python
from app.services.task_queue import release_slot

# completed / failed / cancelled / timeout 的每个 return 前添加：
await release_slot()
```

---

### 🔴 Critical — #3 Redis 连接泄漏

**文件**：`app/services/task_queue.py` L18-L19

**问题**：`_redis()` 每次调用都通过 `aioredis.from_url()` 创建新连接，但从未关闭。每次 `acquire_slot` / `release_slot` / `enqueue` / `dequeue` 都会创建一个新连接，高频调用下会耗尽连接资源。

**当前代码**：
```python
async def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)
```

**建议**：改为单例连接池模式：
```python
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
```

---

### 🟠 Major — #4 callback_url SSRF 风险

**文件**：`app/services/poller.py` L98-L119

**问题**：`_invoke_callback()` 直接对用户提供的 `callback_url` 发起 HTTP POST 请求，攻击者可以指定内网地址（如 `http://169.254.169.254` 获取阿里云 ECS 元数据、`http://localhost:6379` 攻击 Redis），造成 SSRF 攻击。

**建议**：校验 URL 协议和域名，禁止内网/回环地址：
```python
from urllib.parse import urlparse
import ipaddress

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
]

def _is_safe_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    try:
        ip = ipaddress.ip_address(hostname)
        return not any(ip in net for net in _BLOCKED_NETWORKS)
    except ValueError:
        return True  # 域名，允许
```

---

### 🟠 Major — #5 query_task 错误处理不一致

**文件**：`app/services/linkfox.py` L127

**问题**：`submit_task()` 有详细的错误体提取逻辑（解析 JSON 响应体），但 `query_task()` 仍使用 `resp.raise_for_status()`，只抛出简短错误信息，丢失了 LinkFox 返回的详细错误原因。

**建议**：与 `submit_task()` 保持一致的错误处理方式。

---

### 🟠 Major — #6 数据库密码硬编码

**文件**：`docker-compose.yml` L9, L46-L47

**问题**：`POSTGRES_PASSWORD=pass` 和 `DATABASE_URL` 中的 `user:pass` 都是硬编码的弱密码，且 PostgreSQL 端口 5438 映射到宿主机，外网可访问。

**建议**：
1. 使用 `${POSTGRES_PASSWORD}` 从 `.env` 注入
2. 生产环境关闭 PG 外部端口映射（删除 `ports: "5438:5432"`）
3. 使用强密码

---

### 🟡 Minor — #7 CORS 完全开放

**文件**：`app/main.py` L50

**问题**：`allow_origins=["*"]`，允许任何来源的跨域请求，生产环境存在安全风险。

**建议**：限制为实际前端域名，或通过环境变量配置。

---

### 🟡 Minor — #8 Celery 中 asyncio.run 嵌套风险

**文件**：`app/workers/task_worker.py` L51, L144

**问题**：`process_next` 使用 `asyncio.run()`，poll 线程又调用 `asyncio.run()`。如果 Celery worker 使用 gevent/eventlet 池，`asyncio.run()` 可能崩溃或死锁。

**建议**：当前使用 prefork 池暂无问题，但需注意不要切换池类型。长期建议重构为纯异步架构。

---

### 🟡 Minor — #9 get_db 自动 commit

**文件**：`app/core/database.py` L35

**问题**：`get_db()` 在 yield 后自动 commit，即使是只读查询（如 `get_task`、`list_tasks`、`get_stats`）也会产生无意义的 COMMIT 开销。

**建议**：区分读写操作，只读请求不 commit。

---

### 🟡 Minor — #10 生产环境使用 create_all

**文件**：`app/main.py` L32

**问题**：lifespan 中使用 `Base.metadata.create_all` 创建表，没有使用 Alembic 迁移，生产环境数据库变更无法版本化管理。

**建议**：引入 Alembic 管理迁移。

---

## 修复优先级建议

1. **立即修复**：#2（槽位释放时机）+ #3（Redis 连接泄漏）+ #1（并发竞态）— 这三个问题相互关联，建议一起修复
2. **尽快修复**：#4（SSRF）+ #5（错误处理）+ #6（密码硬编码）— 安全相关
3. **计划修复**：#7 ~ #10 — 最佳实践改进
