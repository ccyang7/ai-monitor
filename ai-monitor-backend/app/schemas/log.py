"""调用日志的上报、查询参数与响应模型。"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _assume_utc(value: datetime | None) -> datetime | None:
    # 不带时区的时间会被按服务器时区解释，时间整体偏移（本机 UTC+8，差 8 小时）。
    # 上报方和前端都基本不会显式带时区，所以约定「没带时区就是 UTC」
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class LogIngestIn(BaseModel):
    """接入方上报的一次调用。字段名是跨语言硬契约，一经确定不再更改。"""

    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=100)

    # 设计文档里这两个字段标「必填但可空」。实际放宽为可省略 —— 上报接口的原则是
    # 「宽进严出」：少一个非关键字段，不该丢掉整条日志
    input: str | None = None
    output: str | None = None

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    # 不传时由服务端用 input + output 补齐。让它是 None 而不是默认 0 ——
    # 默认 0 的话，接入方漏传会让统计里凭空出现一批 0 token 的调用
    total_tokens: int | None = Field(default=None, ge=0)

    latency: int = Field(ge=0)
    status: Literal["success", "error"]
    error: str | None = None
    timestamp: datetime | None = None

    @field_validator("timestamp")
    @classmethod
    def _normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return _assume_utc(value)


class LogCreatedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    created_at: datetime


class LogListQuery(BaseModel):
    """列表接口的筛选参数。全部可选，不传就是不过滤，彼此之间是 AND 关系。"""

    page: int = Field(default=1, ge=1)
    # 上限 100 是保护措施：不限制的话，一个 page_size=1000000 的请求就能把数据库拖垮
    page_size: int = Field(default=20, ge=1, le=100)

    model: str | None = Field(default=None, max_length=100)
    status: Literal["success", "error"] | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    min_tokens: int | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=0)

    # 限长 100：搜索词会进 LIKE 模式串，超长输入既搜不到什么，又白搭一次全表扫描
    q: str | None = Field(default=None, max_length=100)

    @field_validator("start_date", "end_date")
    @classmethod
    def _normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return _assume_utc(value)

    @model_validator(mode="after")
    def _check_ranges(self) -> "LogListQuery":
        # 先归一成带时区再比大小：naive 和 aware 直接比较会抛 TypeError 变成 500
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("start_date 不能晚于 end_date")
        if (
            self.min_tokens is not None
            and self.max_tokens is not None
            and self.min_tokens > self.max_tokens
        ):
            raise ValueError("min_tokens 不能大于 max_tokens")
        return self


class LogListItem(BaseModel):
    """列表项刻意不含 input / output：大文本进列表会让一页响应膨胀到几 MB。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency: int
    status: str

    # 传出的是 float 而非 Decimal：这里是展示口径，NUMERIC(14,8) 至多 14 位有效数字，
    # 在 float64 的 15~17 位安全范围内。存储与计算仍全程 NUMERIC，精度一分不让
    estimated_cost: float | None

    created_at: datetime


class LogListOut(BaseModel):
    items: list[LogListItem]
    total: int
    page: int
    page_size: int


class LogDetailOut(LogListItem):
    project_id: int
    provider: str
    input: str | None
    output: str | None
    error_message: str | None
