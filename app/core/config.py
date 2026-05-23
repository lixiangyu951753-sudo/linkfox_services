"""应用配置管理，基于 pydantic-settings 加载环境变量。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置，自动从 .env 文件和环境变量加载。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LinkFox API ---
    linkfox_api_key: str = ""
    linkfox_api_base: str = "https://open.ziniao.com"

    # --- 数据库 ---
    database_url: str = "postgresql+asyncpg://user:pass@localhost:5432/linkfox"

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- 任务配置 ---
    max_concurrent_tasks: int = 3
    poll_interval: int = 5          # 轮询间隔（秒）
    max_retries: int = 3
    task_timeout_minutes: int = 30  # 单任务超时（分钟）

    # --- 服务 ---
    host: str = "0.0.0.0"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    """返回单例配置。"""
    return Settings()
