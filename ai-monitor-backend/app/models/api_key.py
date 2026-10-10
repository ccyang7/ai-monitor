"""API Key 表：只存哈希，吊销即把 status 置为 revoked。"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.project import Project


class ApiKey(Base):
    __tablename__ = "api_keys"

    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked')", name="ck_api_keys_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )

    key_hash: Mapped[str] = mapped_column(String(255), unique=True)
    key_prefix: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), server_default="active")

    # NULL 表示永不过期
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 本表没有 updated_at，所以不继承 TimestampMixin，created_at 单独声明
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    project: Mapped["Project"] = relationship(back_populates="api_keys")
