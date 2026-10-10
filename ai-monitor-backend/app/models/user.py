"""用户表：登录名、密码哈希、可空唯一的邮箱。"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

# 只在类型检查时导入：User 和 Project 互相引用，运行时真导入会形成循环
if TYPE_CHECKING:
    from app.models.project import Project


class User(Base, TimestampMixin):
    __tablename__ = "users"

    # 必须显式写 BigInteger：不写会按 Mapped[int] 推断成 INTEGER，
    # 而建表脚本里是 BIGSERIAL，两边类型不一致外键会对不上
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    # 唯一但可空：PG 的 UNIQUE 允许多行 NULL，所以多个不填邮箱的用户不冲突
    email: Mapped[str | None] = mapped_column(String(255), unique=True)

    # passive_deletes：删用户时不逐条删项目，交给数据库的 ON DELETE CASCADE
    projects: Mapped[list["Project"]] = relationship(
        back_populates="owner",
        passive_deletes=True,
    )
