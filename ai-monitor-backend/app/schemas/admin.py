"""管理后台的只读视图模型：用户列表与助手用量，只含账号信息和聚合数字。"""

from datetime import datetime

from pydantic import BaseModel, Field


class AdminUserListQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    q: str | None = Field(default=None, max_length=100)


class AdminUserItem(BaseModel):
    id: int
    username: str
    email: str | None
    role: str
    created_at: datetime

    # 聚合口径：项目只数个数、不列名字；last_called_at 是跨该项目所有日志的最大时间
    project_count: int
    last_called_at: datetime | None


class AdminUserListOut(BaseModel):
    items: list[AdminUserItem]
    total: int
    page: int
    page_size: int


class AdminAssistantQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    # None = 不限时间。只做「最近 N 天」这一种过滤，不做自定义起止日期 ——
    # V1 的用量统计回答的是「最近用了多少」，不是一个对账工具
    days: int | None = Field(default=None, ge=1, le=365)


class AdminAssistantSummary(BaseModel):
    total_chats: int
    total_tokens: int
    error_count: int
    active_users: int


class AdminAssistantUserRow(BaseModel):
    user_id: int
    username: str
    chat_count: int
    total_tokens: int
    tool_call_count: int
    # 平均值四舍五入到毫秒，不让前端拿到小数再自己格式化
    avg_latency: int
    error_count: int
    last_used_at: datetime


class AdminAssistantOut(BaseModel):
    items: list[AdminAssistantUserRow]
    total: int
    page: int
    page_size: int
    summary: AdminAssistantSummary
