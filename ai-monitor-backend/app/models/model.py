"""模型价格表：内置模型（owner_id 为空）与用户自建模型共存于一张表。"""

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class Model(Base, TimestampMixin):
    __tablename__ = "models"

    # CHECK 写在这里只是让模型完整描述表结构，真正生效的是 sql/schema.sql
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_models_status"),
        CheckConstraint(
            "input_price >= 0 AND output_price >= 0", name="ck_models_price_nonneg"
        ),
        CheckConstraint(
            "cache_hit_price IS NULL OR cache_hit_price >= 0",
            name="ck_models_cache_hit_price_nonneg",
        ),
        CheckConstraint(
            "(is_builtin AND owner_id IS NULL)"
            " OR (NOT is_builtin AND owner_id IS NOT NULL)",
            name="ck_models_builtin_owner",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # NULL 表示内置模型。可空外键是「共享数据 + 私有数据」共存的关键
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    provider: Mapped[str] = mapped_column(String(50))
    model_name: Mapped[str] = mapped_column(String(100), index=True)

    # 单位：元 / 百万 token，和厂商官网的报价口径一致
    input_price: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    output_price: Mapped[Decimal] = mapped_column(Numeric(12, 6))

    # NULL = 没有缓存档（或价格未知），计费时命中部分退回按 input_price 算；
    # 与「缓存价是 0」不同 —— 0 是真实价格（免费模型的缓存命中）
    cache_hit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))

    is_builtin: Mapped[bool] = mapped_column(Boolean, server_default="false")
    status: Mapped[str] = mapped_column(String(20), server_default="active")

    owner: Mapped["User | None"] = relationship()
