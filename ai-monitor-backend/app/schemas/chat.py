"""AI 助手接口的请求与响应模型。"""

from pydantic import BaseModel, Field


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # 前端生成的 uuid，只是本地的「会话切换」标识；服务端会拼上 user_{id}_ 前缀，
    # 所以这里传什么都不会越权（见设计 07 §5.1）
    thread_id: str | None = Field(default=None, max_length=64)
    current_project: str | None = Field(default=None, max_length=50)


class ToolCallItem(BaseModel):
    """模型这次调用过哪个工具、传了什么参数（调试用）。"""

    tool: str
    args: dict


class ChatOut(BaseModel):
    thread_id: str
    reply: str
    tool_calls: list[ToolCallItem]
