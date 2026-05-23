# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start the API server (with auto-reload)
python -m app.main

# Start the Celery worker (separate terminal)
celery -A app.workers.task_worker.celery_app worker --loglevel=info

# Start infrastructure via Docker
docker-compose up -d postgres redis
```

The API runs on `http://localhost:8000`. Swagger docs at `http://localhost:8000/docs`. A management UI is at `http://localhost:8000/manager`.

Helper scripts (one-off, not part of the service):
- `_check_redis.py` — inspects Redis queue state (pings, queue length, keys)
- `_trigger_worker.py` — manually dispatches a `process_next` Celery task
- `_test_db.py` — POSTs `test_payload.json` to the create-task endpoint

## Architecture

This is an **AI-generated product image service** (LinkFox AI Backend). It accepts image-generation requests, submits them to a third-party LinkFox API, polls for completion, and surfaces results via a REST API.

### Stack
- **FastAPI** with async SQLAlchemy (asyncpg driver) for the API layer
- **PostgreSQL** for persistent task/status storage
- **Redis** for the task queue (FIFO list) and concurrency slot counter
- **Celery** (Redis-backed) as the async task processor that dequeues and submits to LinkFox

### Task state machine
`pending` → `queued` (in Redis list) → `processing` (submitted to LinkFox) → `completed` / `failed` / `cancelled`

### Request flow
1. `POST /api/v1/tasks` → creates a `Task` row in Postgres, pushes `task_id` onto the Redis queue, fires `process_next.delay()`
2. Celery worker picks up `process_next` → `acquire_slot()` (Redis counter ≤ `max_concurrent_tasks`) → `dequeue()` → calls `submit_task()` to LinkFox API
3. On success, `asyncio.create_task(poll_task(...))` starts a background polling loop that calls LinkFox's query endpoint every `poll_interval` seconds until terminal status or timeout
4. On completion, optionally POSTs results to a user-supplied `callback_url`

### Key files by role
| File | Role |
|---|---|
| `app/main.py` | FastAPI app, lifespan (auto-creates tables), CORS, route mounting, `/health` |
| `app/core/config.py` | `Settings` via `pydantic-settings`, reads `.env`, singleton via `lru_cache` |
| `app/core/database.py` | Async engine/session factory, `Base` declarative base, `get_db` dependency |
| `app/models/task.py` | `Task` and `TaskLog` ORM models |
| `app/models/schemas.py` | Pydantic request/response schemas + `ApiResponse` wrapper |
| `app/services/linkfox.py` | LinkFox API client: `submit_task()`, `query_task()`, snake→camelCase mapping, status code mapping (1=queued, 2=processing, 3=completed, 4=failed) |
| `app/services/poller.py` | Background polling loop: calls LinkFox query, updates Task status/results, invokes callbacks |
| `app/services/task_queue.py` | Redis FIFO queue operations + concurrency slot management (`acquire_slot`/`release_slot`) |
| `app/api/tasks.py` | `POST/GET/DELETE /api/v1/tasks` — CRUD for image generation tasks |
| `app/api/stats.py` | `GET /api/v1/stats` — aggregate counts, success rate, average processing time |
| `app/workers/task_worker.py` | Celery app definition + `process_next` task that orchestrates dequeue→submit→poll |

### Concurrency control
`app/services/task_queue.py` uses a Redis key `linkfox:concurrent:count` as a semaphore. `acquire_slot()` increments if below `max_concurrent_tasks` and returns `True`; otherwise the worker re-enqueues `process_next` with a delay. `release_slot()` decrements in a `finally` block.

### Configuration
All config lives in `Settings` (`app/core/config.py`), loaded from environment / `.env`. The `.env.example` shows all available vars. Key ones: `DATABASE_URL` (asyncpg), `REDIS_URL`, `LINKFOX_API_KEY`, `MAX_CONCURRENT_TASKS`, `POLL_INTERVAL`, `TASK_TIMEOUT_MINUTES`.
