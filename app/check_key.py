"""临时脚本：验证容器内 API Key"""
import os
from app.core.config import get_settings

s = get_settings()
print(f"env LINKFOX_API_KEY: {repr(os.environ.get('LINKFOX_API_KEY'))}")
print(f"settings.linkfox_api_key: {repr(s.linkfox_api_key)}")
print(f"settings.linkfox_api_base: {repr(s.linkfox_api_base)}")
