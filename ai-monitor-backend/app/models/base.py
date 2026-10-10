"""ORM 基类与公共时间戳字段。"""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# 只做映射不做建表（不调用 Base.metadata.create_all()）：表由 sql/001_init.sql 创建，
# 改结构要同时改那边
class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        # PG 没有 MySQL 的 ON UPDATE CURRENT_TIMESTAMP，只能靠 SQLAlchemy 在 UPDATE 时写；
        # 代价是直接改 SQL 时 updated_at 不会变
        onupdate=func.now(),
        nullable=False,
    )
