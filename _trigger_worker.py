"""手动触发 worker 消费队列中的 pending 任务"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.workers.task_worker import process_next

result = process_next.delay()
print(f"Celery task id: {result.id}")
print("Done — worker should pick up pending tasks now.")
