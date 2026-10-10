"""API Key 的响应模型，对外不含 key_hash。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # 不含 key_hash：哈希泄露和密码泄露是同一级别的事故，对外只给 key_prefix
    id: int
    key_prefix: str
    status: str
    created_at: datetime
    expired_at: datetime | None


class ApiKeyCreatedOut(ApiKeyOut):
    api_key: str
