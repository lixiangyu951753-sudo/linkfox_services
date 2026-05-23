import json, requests
data = json.load(open("test_payload.json"))
r = requests.post("http://localhost:8000/api/v1/tasks", json=data)
print(r.status_code)
print(json.dumps(r.json(), indent=2, ensure_ascii=False))
