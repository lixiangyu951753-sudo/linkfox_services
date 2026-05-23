import redis

r = redis.from_url("redis://localhost:6382/0")

print("Redis ping:", r.ping())
print("concurrent count:", r.get("linkfox:concurrent:count"))
print("task_queue len:", r.llen("linkfox:queue:counter"))
items = r.lrange("linkfox:queue:counter", 0, -1)
print("task_queue items:", [item.decode() for item in items])
print("celery queue len:", r.llen("celery"))
print("---")

for key in r.keys("*"):
    k = key.decode() if isinstance(key, bytes) else key
    ktype = r.type(key).decode() if isinstance(r.type(key), bytes) else r.type(key)
    if k.startswith("celery-task-meta"):
        val = r.get(k)
        print(f"  {k} = {val[:200]}")
