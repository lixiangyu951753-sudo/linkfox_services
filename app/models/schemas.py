"""Pydantic 请求/响应 Schema。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ─── 请求体 ────────────────────────────────────────────

class TaskCreateRequest(BaseModel):
    """创建任务请求。"""

    image_list: list[str] = Field(..., description="商品图片 URL 列表")
    seller_point: str = Field(..., description="商品卖点")
    provider: str = Field(default="BANANA_PRO", description="AI 模型")
    priority: int = Field(default=5, ge=1, le=10, description="优先级 1-10")
    a_plus_num: int = Field(default=2, ge=0, description="A+ 图数量")
    scene_type_num: int = Field(default=3, ge=0, description="场景图数量")
    seller_type_num: int = Field(default=1, ge=0, description="卖家图数量")
    close_up_type_num: int = Field(default=1, ge=0, description="特写图数量")
    white_bg_type_num: int = Field(default=0, ge=0, description="白底图数量")
    aspect_ratio: str = Field(default="1:1", description="宽高比")
    resolution: str = Field(default="2K", description="分辨率")
    callback_url: str | None = Field(default=None, description="回调网钩 URL")


# ─── 响应体 ────────────────────────────────────────────

class ImageResult(BaseModel):
    """生成图片结果。"""
    type: str
    url: str
    width: int | None = None
    height: int | None = None
    format: str | None = None


class TaskCreateResponse(BaseModel):
    """创建任务响应。"""
    task_id: UUID
    status: str
    queue_position: int
    estimated_wait: str


class TaskDetailResponse(BaseModel):
    """任务详情响应。"""
    id: UUID
    linkfox_task_id: str | None = None
    status: str
    seller_point: str | None = None
    provider: str | None = None
    priority: int
    params: dict | None = None
    image_list: list[str] | None = None
    results: list[ImageResult] | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskListItem(BaseModel):
    """任务列表项。"""
    id: UUID
    status: str
    seller_point: str | None = None
    provider: str | None = None
    priority: int
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """任务列表响应。"""
    total: int
    page: int
    page_size: int
    items: list[TaskListItem]


class StatsResponse(BaseModel):
    """统计数据响应。"""
    pending: int = 0
    queued: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    success_rate: float = 0.0
    avg_process_time: str = "0s"


# ─── 通用包装 ──────────────────────────────────────────

class ApiResponse(BaseModel):
    """统一 API 响应包装。"""
    code: int = 0
    message: str = "success"
    data: dict | list | None = None
