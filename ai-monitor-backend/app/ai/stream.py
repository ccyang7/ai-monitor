"""把 Agent 的流式输出翻译成 SSE 帧：状态、正文增量、结束与错误。"""

import json
from collections.abc import AsyncIterator
from time import perf_counter

import openai
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from sqlalchemy.orm import Session

from app.ai.agent import agent, recursion_limit
from app.ai.context import AssistantContext
from app.services.assistant_chat import TurnUsage, record_chat

# 工具的中文描述只在这里给一份，前端拿到什么显示什么，不做翻译
TOOL_LABELS = {
    "list_projects": "查询项目清单",
    "get_project_overview": "查询项目概览",
    "get_token_usage": "查询 Token 用量",
    "get_cost_statistics": "查询成本",
    "get_model_usage": "查询模型使用分布",
    "get_recent_logs": "查询最近调用",
    "get_error_logs": "查询失败调用",
    "analyze_anomalies": "分析异常调用",
    "list_models": "查询模型与价格",
    "search_web": "联网搜索",
}


def sse_frame(payload: dict) -> str:
    # 一帧 = 一行 data: 加 JSON、末尾空行（SSE 靠空行分帧）。
    # ensure_ascii=False 让中文原样出现，curl 调试时不用对着 \uXXXX 猜内容
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _tool_status(update: dict) -> list[dict]:
    # 工具状态从 updates 反推，不用 custom 模式 —— custom 要求工具里调 get_stream_writer()，
    # 而那样写出来的工具没法脱离 LangGraph 单独调用，会毁掉批次 1 的纯 Python 验证路径（设计 §6.4）
    events = []
    for node, payload in update.items():
        if not isinstance(payload, dict):
            continue
        for message in payload.get("messages") or []:
            if node == "model" and isinstance(message, AIMessage):
                for call in message.tool_calls:
                    events.append(
                        {
                            "type": "status",
                            "stage": "tool_start",
                            "name": call["name"],
                            "label": TOOL_LABELS.get(call["name"], call["name"]),
                        }
                    )
            elif node == "tools" and isinstance(message, ToolMessage):
                events.append(
                    {"type": "status", "stage": "tool_end", "name": message.name}
                )
    return events


async def stream_chat(
    message: str,
    ctx: AssistantContext,
    thread_id: str,
    db: Session,
    user_id: int,
) -> AsyncIterator[str]:
    usage = TurnUsage()
    status = "success"
    error_message: str | None = None
    started = perf_counter()

    try:
        async for chunk in agent.astream(
            {"messages": [{"role": "user", "content": message}]},
            context=ctx,
            config={
                "configurable": {"thread_id": thread_id},
                "recursion_limit": recursion_limit(),
            },
            stream_mode=["messages", "updates"],
            version="v2",
        ):
            if chunk["type"] == "messages":
                msg, meta = chunk["data"]
                # 只有模型节点产出的是「回答」。tools 节点也会推 messages ——
                # 那是工具返回值，放过去用户屏幕上会直接冒出 JSON
                if meta.get("langgraph_node") != "model":
                    continue
                usage.add_usage(msg)
                # 增量里混着工具调用的参数碎片（JSON 拼片），只取文本块：
                # 碎片块的 content 和 content_blocks 都是空的，这一步天然挡掉
                text = "".join(
                    block["text"]
                    for block in msg.content_blocks
                    if block["type"] == "text"
                )
                if text:
                    yield sse_frame({"type": "delta", "text": text})
            elif chunk["type"] == "updates":
                for event in _tool_status(chunk["data"]):
                    # 工具次数在这里数：流式 chunk 上的 tool_calls 是拼装中的半成品，
                    # 参数没收全之前拿不到完整的一次调用
                    if event["stage"] == "tool_start":
                        usage.tool_call_count += 1
                    yield sse_frame(event)
    except GraphRecursionError:
        status, error_message = "error", "分析超时，请换个问法"
        yield sse_frame({"type": "error", "message": error_message})
        return
    except openai.OpenAIError:
        # 不透传原始报错 —— 里面有 Key 和端点信息
        status, error_message = "error", "AI 服务暂时不可用"
        yield sse_frame({"type": "error", "message": error_message})
        return
    except BaseException:
        # 用户关页面、反向代理超时都会取消生成器，CancelledError 是 BaseException
        # 的子类而不是 Exception —— 这一轮同样要留一行，否则用量会凭空少掉
        status, error_message = "error", "连接中断"
        raise
    finally:
        # 流式路径的 200 早就发出去了，没机会再改状态码，记录只能在这里收尾
        try:
            record_chat(
                db,
                user_id=user_id,
                thread_id=thread_id,
                usage=usage,
                latency_ms=int((perf_counter() - started) * 1000),
                status=status,
                error_message=error_message,
            )
        except Exception:
            # 记录是旁路：写不进去也不能让一次已经流完或中断的问答再抛错
            db.rollback()

    yield sse_frame({"type": "done", "thread_id": thread_id})
