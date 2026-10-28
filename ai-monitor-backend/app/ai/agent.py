"""Agent 单例：模型、工具、动态提示词与会话检查点在模块导入时装配一次。"""

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from app.ai.context import AssistantContext
from app.ai.prompt import build_prompt
from app.ai.tools import ALL_TOOLS
from app.config import settings

# 空字符串会在构造时就被 openai 客户端拒绝（Missing credentials），
# 未配 Key 时给个占位串：构造能过，真正调用时由接口层的守卫返回 503
_PLACEHOLDER_KEY = "not-configured"


def recursion_limit() -> int:
    # LangGraph 按「超步」计数：一轮工具调用占 2 步（模型节点 + 工具节点），
    # 末尾再留 1 步给生成回答
    return settings.assistant_max_tool_rounds * 2 + 1


# 模块级单例：InMemorySaver 与 agent 实例同生共死，每次请求新建 = 每次都失忆
agent = create_agent(
    model=ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or _PLACEHOLDER_KEY,
        temperature=settings.llm_temperature,
        # 不显式打开的话，非 OpenAI 官方的 base_url 默认不返回 usage，
        # 流式路径的用量就永远是 0。实测 DeepSeek 支持，每次调用只在最后一片给一次
        stream_usage=True,
    ),
    tools=ALL_TOOLS,
    middleware=[build_prompt],
    context_schema=AssistantContext,
    checkpointer=InMemorySaver(),
)
