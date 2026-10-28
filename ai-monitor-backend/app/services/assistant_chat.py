"""AI 助手用量的写入与按用户聚合：只记数字，不存对话正文。"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AssistantChat, User
from app.schemas.admin import AdminAssistantQuery


@dataclass
class TurnUsage:
    """一轮提问的用量累加器：非流式整条喂，流式逐片喂。"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    tool_call_count: int = 0

    def add_usage(self, message: BaseMessage) -> None:
        # 流式的每次模型调用只在最后一片带 usage（实测 DeepSeek 24 片里只有 1 片带），
        # 所以逐片累加不会重复计数
        usage = message.usage_metadata
        if not usage:
            return
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.total_tokens += usage.get("total_tokens", 0)

    def add_message(self, message: BaseMessage) -> None:
        if not isinstance(message, AIMessage):
            return
        self.add_usage(message)
        self.tool_call_count += len(message.tool_calls)


def current_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    # checkpointer 会把整个会话的历史一起带回来，只有最后一个用户提问之后的部分属于本轮。
    # 不切分的话，第二轮提问的 token 会把第一轮的也算进去，越聊越大
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index + 1 :]
    return messages


def record_chat(
    db: Session,
    *,
    user_id: int,
    thread_id: str,
    usage: TurnUsage,
    latency_ms: int,
    status: str,
    error_message: str | None = None,
) -> AssistantChat:
    record = AssistantChat(
        user_id=user_id,
        thread_id=thread_id,
        model=settings.llm_model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        tool_call_count=usage.tool_call_count,
        # 秒表理论上不会为负，这里是防数据库 ck_assistant_chats_latency 把整条记录拒掉
        latency=max(latency_ms, 0),
        status=status,
        error_message=error_message,
    )
    db.add(record)
    db.commit()
    return record


def list_assistant_usage(
    db: Session, query: AdminAssistantQuery
) -> tuple[list, int, tuple[int, int, int, int]]:
    conditions = []
    if query.days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=query.days)
        conditions.append(AssistantChat.created_at >= since)

    # 分页的单位是「用户」不是「记录」：只统计有使用记录的人，
    # 没用过助手的人不占位置
    total = (
        db.scalar(
            select(func.count(distinct(AssistantChat.user_id))).where(*conditions)
        )
        or 0
    )

    # count(*) FILTER (WHERE ...) 是 PG 的写法，比 sum(case when ...) 直白
    error_count = func.count().filter(AssistantChat.status == "error")

    rows = db.execute(
        select(
            AssistantChat.user_id,
            User.username,
            func.count(),
            func.coalesce(func.sum(AssistantChat.total_tokens), 0),
            func.coalesce(func.sum(AssistantChat.tool_call_count), 0),
            func.coalesce(func.avg(AssistantChat.latency), 0),
            error_count,
            func.max(AssistantChat.created_at),
        )
        .join(User, User.id == AssistantChat.user_id)
        .where(*conditions)
        .group_by(AssistantChat.user_id, User.username)
        # 提问多的排前面；并列时按 id，保证翻页顺序稳定
        .order_by(func.count().desc(), AssistantChat.user_id)
        .offset((query.page - 1) * query.page_size)
        .limit(query.page_size)
    ).all()

    # 汇总取的是过滤后的全量，不是当前页 —— 卡片上的数字必须和翻不翻页无关
    summary = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(AssistantChat.total_tokens), 0),
            error_count,
            func.count(distinct(AssistantChat.user_id)),
        ).where(*conditions)
    ).one()

    return rows, total, tuple(summary)
