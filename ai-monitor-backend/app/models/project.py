"""项目表，owner_id 是用户隔离的基础。"""

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.llm_request import LLMRequest
    from app.models.user import User


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    # CHECK 写在这里只是让模型完整描述表结构，真正生效的是 001_init.sql
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_projects_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    owner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    tech_stack: Mapped[str | None] = mapped_column(String(50))

    # 由数据库默认值填 'active'，Python 侧不用再给
    status: Mapped[str] = mapped_column(String(20), server_default="active")

    owner: Mapped["User"] = relationship(back_populates="projects")

    # passive_deletes：删项目时把 Key 交给数据库的 ON DELETE CASCADE，
    # 不先 SELECT 出所有 Key 再逐条 DELETE
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="project",
        passive_deletes=True,
    )

    # 同上：一个项目可能有几十万条日志，删项目时绝不能让 ORM 把它们全加载进内存
    llm_requests: Mapped[list["LLMRequest"]] = relationship(
        back_populates="project",
        passive_deletes=True,
    )
