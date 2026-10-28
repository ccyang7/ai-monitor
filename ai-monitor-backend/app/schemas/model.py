"""模型的请求与响应模型。"""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelCreateIn(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    model_name: str = Field(min_length=1, max_length=100)

    # 与数据库 NUMERIC(12, 6) 对齐：位数超限在入口就是 422，而不是写库时才炸
    input_price: Decimal = Field(ge=0, max_digits=12, decimal_places=6)
    output_price: Decimal = Field(ge=0, max_digits=12, decimal_places=6)

    # 可空 = 无缓存档（命中部分按 input_price 算）；0 是真实价格，两者语义不同
    cache_hit_price: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=6
    )


class ModelUpdateIn(BaseModel):
    """字段都可选：没传 = 不动它。唯一例外是 cache_hit_price —— 传 null = 清空缓存档。"""

    provider: str | None = Field(default=None, min_length=1, max_length=50)
    model_name: str | None = Field(default=None, min_length=1, max_length=100)
    input_price: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=6
    )
    output_price: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=6
    )
    cache_hit_price: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=6
    )

    @field_validator("provider", "model_name", "input_price", "output_price")
    @classmethod
    def _cannot_be_nulled(cls, value: Any) -> Any:
        # 字段「没传」时校验器根本不会跑，所以走到这里的 None 一定是显式传的 null。
        # 这四列都是 NOT NULL，放行等于拿 500 换一个 422（同 ProjectUpdateIn 的 name）
        if value is None:
            raise ValueError("字段不能为 null，不修改请不要传")
        return value


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    model_name: str

    # 和 estimated_cost 一样传出 float：这是展示口径，NUMERIC(12, 6) 至多 12 位
    # 有效数字，在 float64 的安全范围内；存储与计算仍全程走 NUMERIC
    input_price: float
    output_price: float
    cache_hit_price: float | None

    is_builtin: bool
    status: str

    # 由 service 聚合后传入，ORM 模型上没有这个属性
    used_by_projects: list[str] = []


class UnpricedModelOut(BaseModel):
    model_name: str
    # 谁在用这个名字：由 _used_by_projects 的聚合顺手带出，它也回答了用户的下一问
    used_by_projects: list[str] = []


class AdminUnpricedModelOut(BaseModel):
    model_name: str
    # 聚合计数只说「影响面」，不带任何项目名或用户名 —— 管理员的可见范围止于账号信息
    call_count: int
    user_count: int


class ModelListOut(BaseModel):
    items: list[ModelOut]
    total: int
    # 上报过、但和当前可见的 active 模型名对不上的：页面据此提示用户去补价
    unpriced_models: list[UnpricedModelOut] = []
