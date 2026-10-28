"""AI 助手接口：把提问交给 Agent，返回回答与工具调用记录。"""

import logging
from time import perf_counter
from uuid import uuid4

import openai
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError
from sqlalchemy.orm import Session

from app.ai.agent import agent, recursion_limit
from app.ai.context import AssistantContext
from app.ai.stream import stream_chat
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas.chat import ChatIn, ChatOut, ToolCallItem
from app.services.assistant_chat import TurnUsage, current_turn, record_chat

router = APIRouter(prefix="/chat", tags=["chat"])

logger = logging.getLogger(__name__)


def _prepare(payload: ChatIn, user: User) -> tuple[str, AssistantContext]:
    # 前端传来的只是本地会话标识，前缀由服务端拼：伪造别人的 uuid，拼出来也只会落在自己名下。
    # 校验「这串 id 是不是你的」总有漏掉的分支，拼接让别人的会话根本没有构造出来的途径（设计 §5.1）
    thread_id = f"user_{user.id}_{payload.thread_id or uuid4()}"
    ctx = AssistantContext(
        user_id=user.id,
        timezone=settings.assistant_timezone,
        current_project=payload.current_project,
    )
    return thread_id, ctx


def _require_api_key() -> None:
    # 没配 Key 只影响助手自己（T1.2 的决策）：返回 503，不拖着整个平台启动不了
    if not settings.llm_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AI 助手未配置模型服务"
        )


def _record(
    db: Session,
    user: User,
    thread_id: str,
    usage: TurnUsage,
    started: float,
    status_value: str,
    error_message: str | None = None,
) -> None:
    try:
        record_chat(
            db,
            user_id=user.id,
            thread_id=thread_id,
            usage=usage,
            latency_ms=int((perf_counter() - started) * 1000),
            status=status_value,
            error_message=error_message,
        )
    except Exception:
        # 用量是旁路数据：写不进去不该让一次正常的回答变成 500
        logger.exception("助手用量记录失败")
        db.rollback()


@router.post("", summary="向 AI 助手提问（非流式）")
async def chat(
    payload: ChatIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatOut:
    _require_api_key()
    thread_id, ctx = _prepare(payload, current_user)
    started = perf_counter()

    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": payload.message}]},
            context=ctx,
            config={
                "configurable": {"thread_id": thread_id},
                "recursion_limit": recursion_limit(),
            },
        )
    except GraphRecursionError:
        _record(db, current_user, thread_id, TurnUsage(), started, "error", "分析超时")
        # 504 而不是 500：轮次超限是「上游在限定步数内没给出结果」，转发类语义比内部错误贴切
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT, "分析超时，请换个问法"
        ) from None
    except openai.OpenAIError:
        _record(
            db, current_user, thread_id, TurnUsage(), started, "error", "AI 服务暂时不可用"
        )
        # 连接失败、超时、鉴权失败都归这里。不透传原始报错 —— 里面有 Key 和端点的信息
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AI 服务暂时不可用"
        ) from None

    # 带 checkpointer 时返回的是整个会话，先切出本轮再统计
    turn = current_turn(result["messages"])
    usage = TurnUsage()
    for message in turn:
        usage.add_message(message)
    _record(db, current_user, thread_id, usage, started, "success")

    final = result["messages"][-1]
    # 正常结束 = 模型不再调工具、给出最终回答，所以最后一条必是 AIMessage
    reply = final.text if isinstance(final, AIMessage) else ""
    tool_calls = [
        ToolCallItem(tool=call["name"], args=call["args"])
        for message in turn
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    return ChatOut(thread_id=thread_id, reply=reply, tool_calls=tool_calls)


@router.post("/stream", summary="向 AI 助手提问（SSE 流式）")
async def chat_stream(
    payload: ChatIn,
    current_user: User = Depends(get_current_user),
    # 流式路由原本用不到数据库，这个依赖只为收尾时记一行用量。
    # FastAPI 的 yield 依赖在响应发完之后才关闭，流跑的过程中会话仍然有效
    db: Session = Depends(get_db),
) -> StreamingResponse:
    _require_api_key()
    thread_id, ctx = _prepare(payload, current_user)

    # 状态码在流开始前就定死了：跑到一半才出的错（模型断了、超轮次）改不了状态码，
    # 只能由 stream_chat 发 error 事件 —— 前端不能只看 response.ok
    return StreamingResponse(
        stream_chat(payload.message, ctx, thread_id, db, current_user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 以后挂到 nginx 后面时，别让它把流缓冲成一坨
        },
    )
