"""AI 助手接口：把提问交给 Agent，返回回答与工具调用记录。"""

from uuid import uuid4

import openai
from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from app.ai.agent import agent, recursion_limit
from app.ai.context import AssistantContext
from app.config import settings
from app.deps import get_current_user
from app.models import User
from app.schemas.chat import ChatIn, ChatOut, ToolCallItem

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", summary="向 AI 助手提问（非流式）")
async def chat(
    payload: ChatIn,
    current_user: User = Depends(get_current_user),
) -> ChatOut:
    # 没配 Key 只影响助手自己（T1.2 的决策）：返回 503，不拖着整个平台启动不了
    if not settings.llm_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AI 助手未配置模型服务"
        )

    # 前端传来的只是本地会话标识，前缀由服务端拼：伪造别人的 uuid，拼出来也只会落在自己名下。
    # 校验「这串 id 是不是你的」总有漏掉的分支，拼接让别人的会话根本没有构造出来的途径（设计 §5.1）
    thread_id = f"user_{current_user.id}_{payload.thread_id or uuid4()}"
    ctx = AssistantContext(
        user_id=current_user.id,
        timezone=settings.assistant_timezone,
        current_project=payload.current_project,
    )

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
        # 504 而不是 500：轮次超限是「上游在限定步数内没给出结果」，转发类语义比内部错误贴切
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT, "分析超时，请换个问法"
        ) from None
    except openai.OpenAIError:
        # 连接失败、超时、鉴权失败都归这里。不透传原始报错 —— 里面有 Key 和端点的信息
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "AI 服务暂时不可用"
        ) from None

    final = result["messages"][-1]
    # 正常结束 = 模型不再调工具、给出最终回答，所以最后一条必是 AIMessage
    reply = final.text if isinstance(final, AIMessage) else ""
    tool_calls = [
        ToolCallItem(tool=call["name"], args=call["args"])
        for message in result["messages"]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    return ChatOut(thread_id=thread_id, reply=reply, tool_calls=tool_calls)
