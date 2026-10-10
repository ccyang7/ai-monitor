"""调用日志表：一次 LLM 调用一条记录，成本在写入时快照。"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.project import Project


class LLMRequest(Base):
    __tablename__ = "llm_requests"

    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'error')", name="ck_llm_requests_status"
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0",
            name="ck_llm_requests_tokens",
        ),
        CheckConstraint("latency >= 0", name="ck_llm_requests_latency"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )

    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100), index=True)

    # input / output 是 Python 内置函数名，作为类属性会遮蔽它们 —— 但只在这个类的
    # 命名空间内，类方法里也没人调 input()。对外字段名是硬契约（接入方按它发请求），
    # 为了内部好看改名字不值当
    input: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)

    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, server_default="0")

    latency: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(Text)

    # 快照：写入时算好就不再变，改模型价格不影响历史
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))

    # 本表没有 updated_at（日志是不可变的事实），所以不继承 TimestampMixin
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    project: Mapped["Project"] = relationship(back_populates="llm_requests")
