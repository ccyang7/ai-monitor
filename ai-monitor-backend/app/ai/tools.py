"""AI 分析助手的查询工具：项目名解析与鉴权、自然日换算，以及八个只读查询。"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

from langchain.tools import ToolRuntime, tool
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.context import AssistantContext
from app.database import SessionLocal
from app.models import LLMRequest, Project, User
from app.schemas.anomaly import AnomalyQuery
from app.schemas.log import LogListQuery
from app.schemas.stats import StatsQuery
from app.services import anomaly as anomaly_service
from app.services import logs as logs_service
from app.services import project as project_service
from app.services import stats as stats_service

# 不封顶的话，一句「把最近的调用都列出来」就能把几十 KB 的日志摘要塞进模型上下文
MAX_LOG_LIMIT = 20
MAX_ERROR_MESSAGE_CHARS = 200

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
                for project, last_called in rows
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
                "estimated_cost_cny": overview.summary.cost,
                "avg_latency_ms": overview.summary.avg_latency,
                "previous": {
                    "calls": overview.previous.calls,
                    "total_tokens": overview.previous.total_tokens,
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


ALL_TOOLS = [
    list_projects,
    get_project_overview,
    get_token_usage,
    get_cost_statistics,
    get_model_usage,
    get_recent_logs,
    get_error_logs,
    analyze_anomalies,
]
