"""AI 助手用量表：一行 = 一次用户提问，只记用量、不含对话正文。"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AssistantChat(Base):
    __tablename__ = "assistant_chats"

    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'error')", name="ck_assistant_chats_status"
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0",
            name="ck_assistant_chats_tokens",
        ),
        CheckConstraint("tool_call_count >= 0", name="ck_assistant_chats_tool_calls"),
        CheckConstraint("latency >= 0", name="ck_assistant_chats_latency"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # 不写 index=True：库里已有 (user_id, created_at DESC, id DESC) 复合索引，
    # 最左列就是 user_id，再建一个单列索引只是重复占空间
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
    )

    thread_id: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))

    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    tool_call_count: Mapped[int] = mapped_column(Integer, server_default="0")

    latency: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(Text)

    # 与 llm_requests 同理：事实表没有 updated_at，写进去就不再改
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
