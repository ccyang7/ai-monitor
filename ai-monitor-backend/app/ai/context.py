"""AI 助手的运行时上下文：工具层鉴权取信源的唯一来源。"""

from dataclasses import dataclass


@dataclass
class AssistantContext:
    user_id: int
    timezone: str
    current_project: str | None = None
