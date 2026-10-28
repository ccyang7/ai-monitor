"""AI 分析助手的工具：项目名解析与鉴权、自然日换算、九个只读查询与联网搜索。"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from langchain_tavily import TavilySearch
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.context import AssistantContext
from app.config import settings
from app.database import SessionLocal
from app.models import LLMRequest, Project, User
from app.schemas.anomaly import AnomalyQuery
from app.schemas.log import LogListQuery
from app.schemas.stats import StatsQuery
from app.services import anomaly as anomaly_service
from app.services import logs as logs_service
from app.services import model as model_service
from app.services import project as project_service
from app.services import stats as stats_service

# 不封顶的话，一句「把最近的调用都列出来」就能把几十 KB 的日志摘要塞进模型上下文
MAX_LOG_LIMIT = 20
MAX_ERROR_MESSAGE_CHARS = 200
MAX_SEARCH_RESULTS = 5
# 实测 Tavily 的正文多在 400~1400 字符，且价格表常常在后半段 —— 截得太狠等于把答案切掉。
# 1000 是个「几乎不误伤、又能拦住超长页面」的位置
MAX_SNIPPET_CHARS = 1000

QueryT = TypeVar("QueryT")


def _resolve_projects(
    db: Session, names: list[str] | None, ctx: AssistantContext
) -> list[Project] | dict:
    if not names:
        projects = list(
            db.scalars(
                select(Project)
                .where(Project.owner_id == ctx.user_id, Project.status == "active")
                .order_by(Project.created_at)
            )
        )
        if not projects:
            return {"error": "你还没有任何项目。"}
        return projects

    resolved: list[Project] = []
    for name in names:
        # 解析和鉴权是同一条查询：名字和 owner_id 一起进 WHERE。分成「先按名字查、
        # 再判断归属」两步的写法，迟早会漏掉后半句
        project = db.scalar(
            select(Project).where(Project.name == name, Project.owner_id == ctx.user_id)
        )
        if project is None:
            # 候选清单必须列全（V1 每用户 ≤ 3 个项目，成本为零）：用户说的是简称时，
            # 模型要靠它对齐出准确名称再重试。不做模糊匹配 —— 多个项目都命中时，
            # 服务端选谁都是「替模型猜」，猜错就是把别人的数据说成用户要的（设计 §3.3.1）
            available = list(
                db.scalars(
                    select(Project.name)
                    .where(Project.owner_id == ctx.user_id, Project.status == "active")
                    .order_by(Project.created_at)
                )
            )
            listed = "、".join(available) if available else "（还没有项目）"
            return {"error": f"项目「{name}」不存在。你的项目有：{listed}"}
        resolved.append(project)
    return resolved


def _day_range(
    start_date: str, end_date: str, tz: str
) -> tuple[datetime, datetime] | dict:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except (TypeError, ValueError):
        return {
            "error": (
                "日期格式应为 YYYY-MM-DD，实际收到 "
                f"start_date={start_date!r}、end_date={end_date!r}"
            )
        }
    if end < start:
        return {"error": "end_date 不能早于 start_date"}

    # 工具的日期是「自然日」语义（含首尾），后端统一左闭右开 —— 所以终点补一天。
    # 换算必须落在助手时区上，否则 +08:00 用户的「今天」会被 UTC 切成两半
    zone = ZoneInfo(tz)
    start_at = datetime.combine(start, time.min, tzinfo=zone).astimezone(timezone.utc)
    end_at = datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone).astimezone(
        timezone.utc
    )
    return start_at, end_at


def _build_query(model_cls: type[QueryT], **kwargs: object) -> QueryT | dict:
    # 模型给的参数可能越界（比如按小时统计却跨了好几天）。返回错误 dict 而不是抛异常：
    # 抛出去这一轮对话就断了，返回错误它还能读完自己纠正重试
    try:
        return model_cls(**kwargs)
    except ValidationError as exc:
        message = str(exc.errors()[0].get("msg", exc))
        return {"error": f"参数不合法：{message.removeprefix('Value error, ')}"}


def _local(moment: datetime, tz: str) -> str:
    return moment.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")


@tool
def list_projects(runtime: ToolRuntime[AssistantContext]) -> dict:
    """列出当前用户的全部项目：名称、ID 和最近一次调用时间。

    用户问「我有哪些项目」时调用；拿不准用户说的项目名对应哪个项目时，
    也先调用它拿到准确的项目名清单。
    """
    ctx = runtime.context
    with SessionLocal() as db:
        user = db.get(User, ctx.user_id)
        if user is None:
            return {"error": "当前用户不存在。"}
        rows = project_service.list_projects(db, user)
        return {
            "projects": [
                {
                    "name": project.name,
                    "id": project.id,
                    "last_called_at": (
                        _local(last_called, ctx.timezone) if last_called else None
                    ),
                }
                # 第三个值是「最近是否有异常」，只给首页卡片用，助手这里不需要
                for project, last_called, _ in rows
            ]
        }


@tool
def get_project_overview(
    runtime: ToolRuntime[AssistantContext],
    start_date: str,
    end_date: str,
    project_names: list[str] | None = None,
) -> dict:
    """查询项目概览：调用次数、Token 总量、估算成本（元）、平均响应时间（毫秒），
    并给出上一个等长区间的同组指标（用于算环比）和各类异常的条数。
    每个区间都带 cache_hit_tokens：输入 token 中命中缓存的部分（子集）。

    Args:
        start_date: 开始日期，自然日、含当天，格式 YYYY-MM-DD。
        end_date: 结束日期，自然日、含当天，格式 YYYY-MM-DD。
        project_names: 项目名称列表。不传 = 当前用户的全部项目；
            一个名字 = 单个项目；多个名字 = 跨项目对比。
    """
    ctx = runtime.context
    rng = _day_range(start_date, end_date, ctx.timezone)
    if isinstance(rng, dict):
        return rng
    start, end = rng

    with SessionLocal() as db:
        projects = _resolve_projects(db, project_names, ctx)
        if isinstance(projects, dict):
            return projects
        query = _build_query(StatsQuery, start_date=start, end_date=end, tz=ctx.timezone)
        if isinstance(query, dict):
            return query

        result = {}
        for project in projects:
            # 异常条数由 anomaly service 算、传进 get_overview，与概览页接口同一条路径
            counts, baseline_available = anomaly_service.count_anomalies(
                db, project.id, start, end, ctx.timezone
            )
            overview = stats_service.get_overview(
                db, project, query, counts, baseline_available
            )
            result[project.name] = {
                "calls": overview.summary.calls,
                "total_tokens": overview.summary.total_tokens,
                "cache_hit_tokens": overview.summary.cache_hit_tokens,
                "estimated_cost_cny": overview.summary.cost,
                "avg_latency_ms": overview.summary.avg_latency,
                "previous": {
                    "calls": overview.previous.calls,
                    "total_tokens": overview.previous.total_tokens,
                    "cache_hit_tokens": overview.previous.cache_hit_tokens,
                    "estimated_cost_cny": overview.previous.cost,
                    "avg_latency_ms": overview.previous.avg_latency,
                },
                "anomaly_counts": overview.anomaly_counts.model_dump(),
                "baseline_available": overview.baseline_available,
            }
        return result


@tool
def get_token_usage(
    runtime: ToolRuntime[AssistantContext],
    start_date: str,
    end_date: str,
    project_names: list[str] | None = None,
    granularity: Literal["day", "hour"] = "day",
) -> dict:
    """查询 Token 使用情况：汇总 + 趋势序列（按天或按小时）。
    summary.cache_hit_tokens 是 input_tokens 中命中缓存的部分（子集），
    不要把它和 input_tokens 相加。

    Args:
        start_date: 开始日期，自然日、含当天，格式 YYYY-MM-DD。
        end_date: 结束日期，自然日、含当天，格式 YYYY-MM-DD。
        project_names: 项目名称列表。不传 = 当前用户的全部项目；
            一个名字 = 单个项目；多个名字 = 跨项目对比。
        granularity: 趋势粒度，"day" 按天（默认）或 "hour" 按小时（区间不能超过 1 天）。
    """
    ctx = runtime.context
    rng = _day_range(start_date, end_date, ctx.timezone)
    if isinstance(rng, dict):
        return rng
    start, end = rng

    with SessionLocal() as db:
        projects = _resolve_projects(db, project_names, ctx)
        if isinstance(projects, dict):
            return projects
        query = _build_query(
            StatsQuery,
            start_date=start,
            end_date=end,
            tz=ctx.timezone,
            granularity=granularity,
        )
        if isinstance(query, dict):
            return query

        result = {}
        for project in projects:
            stats = stats_service.get_token_stats(db, project, query)
            result[project.name] = {
                "calls": stats.summary.calls,
                "input_tokens": stats.summary.input_tokens,
                "cache_hit_tokens": stats.summary.cache_hit_tokens,
                "output_tokens": stats.summary.output_tokens,
                "total_tokens": stats.summary.total_tokens,
                "series": [
                    {
                        "date": point.date,
                        "calls": point.calls,
                        "total_tokens": point.total_tokens,
                    }
                    for point in stats.series
                ],
            }
        return result


@tool
def get_cost_statistics(
    runtime: ToolRuntime[AssistantContext],
    start_date: str,
    end_date: str,
    project_names: list[str] | None = None,
    granularity: Literal["day", "hour"] = "day",
) -> dict:
    """查询成本（估算，单位元）：汇总 + 趋势序列（按天或按小时）。

    Args:
        start_date: 开始日期，自然日、含当天，格式 YYYY-MM-DD。
        end_date: 结束日期，自然日、含当天，格式 YYYY-MM-DD。
        project_names: 项目名称列表。不传 = 当前用户的全部项目；
            一个名字 = 单个项目；多个名字 = 跨项目对比。
        granularity: 趋势粒度，"day" 按天（默认）或 "hour" 按小时（区间不能超过 1 天）。
    """
    ctx = runtime.context
    rng = _day_range(start_date, end_date, ctx.timezone)
    if isinstance(rng, dict):
        return rng
    start, end = rng

    with SessionLocal() as db:
        projects = _resolve_projects(db, project_names, ctx)
        if isinstance(projects, dict):
            return projects
        query = _build_query(
            StatsQuery,
            start_date=start,
            end_date=end,
            tz=ctx.timezone,
            granularity=granularity,
        )
        if isinstance(query, dict):
            return query

        result = {}
        for project in projects:
            stats = stats_service.get_cost_stats(db, project, query)
            result[project.name] = {
                "calls": stats.summary.calls,
                "estimated_cost_cny": stats.summary.cost,
                "series": [
                    {
                        "date": point.date,
                        "calls": point.calls,
                        "estimated_cost_cny": point.cost,
                    }
                    for point in stats.series
                ],
            }
        return result


@tool
def get_model_usage(
    runtime: ToolRuntime[AssistantContext],
    start_date: str,
    end_date: str,
    project_names: list[str] | None = None,
) -> dict:
    """查询各模型的使用分布：调用次数、Token、成本及其占比。
    每项含 cache_hit_tokens（该模型输入中命中缓存的部分，子集）。

    Args:
        start_date: 开始日期，自然日、含当天，格式 YYYY-MM-DD。
        end_date: 结束日期，自然日、含当天，格式 YYYY-MM-DD。
        project_names: 项目名称列表。不传 = 当前用户的全部项目；
            一个名字 = 单个项目；多个名字 = 跨项目对比。
    """
    ctx = runtime.context
    rng = _day_range(start_date, end_date, ctx.timezone)
    if isinstance(rng, dict):
        return rng
    start, end = rng

    with SessionLocal() as db:
        projects = _resolve_projects(db, project_names, ctx)
        if isinstance(projects, dict):
            return projects
        query = _build_query(StatsQuery, start_date=start, end_date=end, tz=ctx.timezone)
        if isinstance(query, dict):
            return query

        result = {}
        for project in projects:
            stats = stats_service.get_model_stats(db, project, query)
            result[project.name] = {
                "models": [
                    {
                        "model": item.model,
                        "calls": item.calls,
                        "total_tokens": item.total_tokens,
                        "cache_hit_tokens": item.cache_hit_tokens,
                        "estimated_cost_cny": item.cost,
                        "avg_latency_ms": item.avg_latency,
                        "call_percentage": item.call_percentage,
                        "token_percentage": item.token_percentage,
                        "cost_percentage": item.cost_percentage,
                    }
                    for item in stats.items
                ]
            }
        return result


@tool
def get_recent_logs(
    runtime: ToolRuntime[AssistantContext],
    project_names: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """查询最近的调用日志摘要，不含请求与响应正文。
    每条含 cache_hit_tokens（输入中命中缓存的部分，子集）。

    Args:
        project_names: 项目名称列表。不传 = 当前用户的全部项目；
            一个名字 = 单个项目；多个名字 = 跨项目对比。
        limit: 返回条数，默认 10，最大 20。
    """
    ctx = runtime.context
    limit = max(1, min(limit, MAX_LOG_LIMIT))

    with SessionLocal() as db:
        projects = _resolve_projects(db, project_names, ctx)
        if isinstance(projects, dict):
            return projects
        query = LogListQuery(page=1, page_size=limit)

        result = {}
        for project in projects:
            rows, _ = logs_service.list_logs(db, project, query)
            result[project.name] = {
                "items": [
                    {
                        "time": _local(row.created_at, ctx.timezone),
                        "model": row.model,
                        "total_tokens": row.total_tokens,
                        "cache_hit_tokens": row.cache_hit_tokens,
                        "latency_ms": row.latency,
                        "status": row.status,
                    }
                    for row in rows
                ]
            }
        return result


@tool
def get_error_logs(
    runtime: ToolRuntime[AssistantContext],
    project_names: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """查询最近的失败调用（status=error）及其错误信息摘要。
    每条含 cache_hit_tokens（输入中命中缓存的部分，子集）。

    Args:
        project_names: 项目名称列表。不传 = 当前用户的全部项目；
            一个名字 = 单个项目；多个名字 = 跨项目对比。
        limit: 返回条数，默认 10，最大 20。
    """
    ctx = runtime.context
    limit = max(1, min(limit, MAX_LOG_LIMIT))

    with SessionLocal() as db:
        projects = _resolve_projects(db, project_names, ctx)
        if isinstance(projects, dict):
            return projects
        query = LogListQuery(page=1, page_size=limit, status="error")

        result = {}
        for project in projects:
            rows, _ = logs_service.list_logs(db, project, query)
            # list_logs 的列清单刻意不含 error_message（与列表接口的字段保持一致），
            # 逐行访问它会让每一行再发一条 SELECT。按 id 一次性取回：N+1 变 1
            ids = [row.id for row in rows]
            messages: dict[int, str | None] = {}
            if ids:
                messages = dict(
                    db.execute(
                        select(LLMRequest.id, LLMRequest.error_message).where(
                            LLMRequest.id.in_(ids)
                        )
                    ).all()
                )
            result[project.name] = {
                "items": [
                    {
                        "time": _local(row.created_at, ctx.timezone),
                        "model": row.model,
                        "total_tokens": row.total_tokens,
                        "cache_hit_tokens": row.cache_hit_tokens,
                        "latency_ms": row.latency,
                        "status": row.status,
                        "error_message": (messages.get(row.id) or "")[
                            :MAX_ERROR_MESSAGE_CHARS
                        ],
                    }
                    for row in rows
                ]
            }
        return result


@tool
def analyze_anomalies(
    runtime: ToolRuntime[AssistantContext],
    start_date: str,
    end_date: str,
    project_names: list[str] | None = None,
) -> dict:
    """查询区间内被判定为异常的调用：Token 异常、延迟异常、失败调用、错误率异常。

    baseline_available 为 false 表示样本不足、Token 与延迟两类暂时判不了 ——
    这种情况应如实告诉用户「数据还在积累」，而不是「没有异常」。

    Args:
        start_date: 开始日期，自然日、含当天，格式 YYYY-MM-DD。
        end_date: 结束日期，自然日、含当天，格式 YYYY-MM-DD。
        project_names: 项目名称列表。不传 = 当前用户的全部项目；
            一个名字 = 单个项目；多个名字 = 跨项目对比。
    """
    ctx = runtime.context
    rng = _day_range(start_date, end_date, ctx.timezone)
    if isinstance(rng, dict):
        return rng
    start, end = rng

    with SessionLocal() as db:
        projects = _resolve_projects(db, project_names, ctx)
        if isinstance(projects, dict):
            return projects
        query = _build_query(
            AnomalyQuery, start_date=start, end_date=end, tz=ctx.timezone
        )
        if isinstance(query, dict):
            return query

        result = {}
        for project in projects:
            anomalies = anomaly_service.list_anomalies(db, project, query)
            result[project.name] = {
                "total": anomalies.total,
                "baseline_available": anomalies.baseline_available,
                "insufficient_baselines": [
                    {
                        "model": item.model,
                        "samples": item.samples,
                        "required": item.required,
                    }
                    for item in anomalies.insufficient_baselines
                ],
                "items": [
                    {
                        "type": item.type,
                        "time": _local(item.created_at, ctx.timezone),
                        "log_id": item.log_id,
                        "window": item.window,
                        "model": item.model,
                        "value": item.value,
                        "baseline": item.baseline,
                        "ratio": item.ratio,
                        "error_message": (item.error_message or "")[
                            :MAX_ERROR_MESSAGE_CHARS
                        ]
                        or None,
                    }
                    for item in anomalies.items
                ],
            }
        return result


@tool
def list_models(runtime: ToolRuntime[AssistantContext]) -> dict:
    """列出平台里已配置的模型及其价格（元 / 百万 Token）：模型名、提供方、
    输入价、输出价、缓存命中价、来源（内置 / 自建）、在用的项目。

    用户问「有哪些模型」「某个模型在库里是什么价」时调用。它给的是平台上
    已配置的价格，不是厂商官网的最新定价 —— 用户问「官方定价 / 最新价格」
    时改用 search_web 联网查。
    """
    ctx = runtime.context
    with SessionLocal() as db:
        user = db.get(User, ctx.user_id)
        if user is None:
            return {"error": "当前用户不存在。"}
        rows = model_service.list_models(db, user)
        return {
            "models": [
                {
                    "model_name": model.model_name,
                    "provider": model.provider,
                    "input_price_cny_per_million": float(model.input_price),
                    "output_price_cny_per_million": float(model.output_price),
                    # None = 没配缓存档，与「缓存价是 0（真的免费）」是两回事
                    "cache_hit_price_cny_per_million": (
                        float(model.cache_hit_price)
                        if model.cache_hit_price is not None
                        else None
                    ),
                    "source": "内置" if model.is_builtin else "自建",
                    "used_by_projects": projects,
                }
                for model, projects in rows
            ]
        }


def _tavily_search(query: str) -> dict:
    # 真正打网络的一层单独拆出来：验收脚本把它整个换掉，就能离线测返回结构
    searcher = TavilySearch(
        max_results=MAX_SEARCH_RESULTS,
        tavily_api_key=settings.tavily_api_key,
    )
    return searcher.invoke({"query": query})


@tool
def search_web(query: str) -> dict:
    """联网搜索公开网页，返回最多 5 条结果的标题、链接和摘要。

    Args:
        query: 搜索关键词。「deepseek-chat 官方定价」这种具体词组比一整句
            问句更容易搜到有用结果。
    """
    try:
        raw = _tavily_search(query)
    except Exception as exc:  # noqa: BLE001
        # 断网、限流、Key 失效都会从这里抛出来。返回错误让模型如实转述，
        # 而不是把异常抛上去中断整轮对话
        return {"error": f"联网搜索失败：{exc}"}

    items = raw.get("results") or []
    return {
        "results": [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": (item.get("content") or "").strip()[:MAX_SNIPPET_CHARS],
            }
            for item in items[:MAX_SEARCH_RESULTS]
        ]
    }


def build_tools() -> list[BaseTool]:
    """组装工具清单。search_web 要用 Tavily Key，没配就不注册 —— 模型看不到它，
    问价格时只能如实回答没有联网能力。

    写成函数而不是直接把列表定死：验收要在同一个进程里切换 Key 的有无，
    只看模块级的 ALL_TOOLS 切不动。生产仍然用下面这一份。
    """
    tools = [
        list_projects,
        get_project_overview,
        get_token_usage,
        get_cost_statistics,
        get_model_usage,
        get_recent_logs,
        get_error_logs,
        analyze_anomalies,
        list_models,
    ]
    if settings.tavily_api_key:
        tools.append(search_web)
    return tools


ALL_TOOLS = build_tools()
