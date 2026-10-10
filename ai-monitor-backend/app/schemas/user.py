"""用户信息的响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # 没有 password_hash 靠的是「根本没写这个字段」，不是靠「记得删掉」
    id: int
    username: str
    email: str | None
    created_at: datetime
