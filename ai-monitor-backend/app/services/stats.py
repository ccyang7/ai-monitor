"""项目监控中心的统计查询：区间汇总、按天趋势、模型分布。"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Date, func, select
from sqlalchemy.orm import Session, load_only

from app.models import LLMRequest, Project
from app.schemas.stats import (
    CostPoint,
    CostSummary,
    CostsOut,
    ModelUsageItem,
    ModelsOut,
    OverviewOut,
    PeriodMetrics,
    RecentLogItem,
    StatsQuery,
    TokenPoint,
    TokenSummary,
    TokensOut,
)

# 概览页只展示一屏，给 5 条
_RECENT_LIMIT = 5

# 最近调用只取这几列，input / output 不碰：PG 的大文本在 TOAST 表里，
# SELECT 列表不出现该列时连 TOAST 都不读。列不全就会静默触发逐行补查，
# 这份清单必须与 RecentLogItem 的字段一致
_RECENT_COLUMNS = (
    LLMRequest.id,
    LLMRequest.model,
    LLMRequest.total_tokens,
    LLMRequest.latency,
    LLMRequest.status,
    LLMRequest.created_at,
)


def _conditions(
    project_id: int, start: datetime, end: datetime
) -> tuple[ColumnElement[bool], ...]:
    # project_id 永远是第一个条件：漏掉它这个接口就成了读出全系统统计的黑洞
    # 区间一律左闭右开：end 传的是「次日 0 点」这类边界，用 <= 会把那一刻的数据
    # 重复算进相邻两个区间
    return (
        LLMRequest.project_id == project_id,
        LLMRequest.created_at >= start,
        LLMRequest.created_at < end,
    )


def _local_day(tz: str):
    # 先转成目标时区的本地挂钟时间再取日期。直接对 timestamptz 取日期是按数据库
    # 时区（UTC）截断的，UTC+8 的用户会看到「今天」的数据被切一半到昨天去
    return LLMRequest.created_at.op("AT TIME ZONE")(tz).cast(Date)


def _day_skeleton(start: datetime, end: datetime, tz: ZoneInfo) -> list[date]:
    first = start.astimezone(tz).date()
    # 用 end 前的一微秒定位最后一天：end 常常正好落在某天 0 点（「近 7 天」的 end
    # 就是明天 0 点），直接拿 end 的日期会凭空多补一天
    last = (end - timedelta(microseconds=1)).astimezone(tz).date()

    days = []
    cursor = first
    while cursor <= last:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _previous_period(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    # 环比取「上一个等长区间」，一套逻辑覆盖今日 / 近 7 天 / 近 30 天 / 自定义，
    # 不用为每种快捷选项单独定义「昨天」「上周」怎么算
    span = end - start
    return start - span, start


def _summarize(db: Session, project_id: int, start: datetime, end: datetime):
    return db.execute(
        select(
            func.count().label("calls"),
            func.coalesce(func.sum(LLMRequest.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(LLMRequest.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(LLMRequest.total_tokens), 0).label("total_tokens"),
            # SUM 跳过 NULL，所以未知模型（成本为 NULL）的调用不会污染成本合计，
            # 只是不贡献数字。全区间都是未知模型时 SUM 返回 NULL，兜底成 0
            func.coalesce(func.sum(LLMRequest.estimated_cost), 0).label("cost"),
            # 故意不 coalesce：只统计成功调用，且区间内一次成功都没有时返回 NULL。
            # 失败调用的 latency 可能是超时时间本身，混进平均值会让指标失真
            func.avg(LLMRequest.latency)
            .filter(LLMRequest.status == "success")
            .label("avg_latency"),
        ).where(*_conditions(project_id, start, end))
    ).one()


def _avg_latency(value: Decimal | int | None) -> int | None:
    return None if value is None else int(value)


def _daily_totals(
    db: Session,
    project_id: int,
    start: datetime,
    end: datetime,
    tz: str,
    value_column,
) -> dict[date, tuple[int, Decimal]]:
    """按天汇总「调用次数 + 某个数值列」。没有数据的日期不在这里补，由调用方按骨架填。"""
    day = _local_day(tz)
    rows = db.execute(
        select(
            day.label("day"),
            func.count().label("calls"),
            func.coalesce(func.sum(value_column), 0).label("value"),
        )
        .where(*_conditions(project_id, start, end))
        .group_by(day)
        .order_by(day)
    ).all()

    return {row.day: (row.calls, row.value) for row in rows}


def get_overview(db: Session, project: Project, query: StatsQuery) -> OverviewOut:
    start, end = query.start_date, query.end_date
    prev_start, prev_end = _previous_period(start, end)

    current = _summarize(db, project.id, start, end)
    previous = _summarize(db, project.id, prev_start, prev_end)

    # 「最近调用」刻意不按时间选择器过滤：它是这个项目最新发生的调用，
    # 选了「近 30 天」也不该看不见今天刚上报的那条
    recent = db.scalars(
        select(LLMRequest)
        .options(load_only(*_RECENT_COLUMNS))
        .where(LLMRequest.project_id == project.id)
        # 第二排序键 id 不能省：同一毫秒的多条记录只按 created_at 排是不稳定序
        .order_by(LLMRequest.created_at.desc(), LLMRequest.id.desc())
        .limit(_RECENT_LIMIT)
    )

    return OverviewOut(
        summary=PeriodMetrics(
            calls=current.calls,
            total_tokens=current.total_tokens,
            cost=float(current.cost),
            avg_latency=_avg_latency(current.avg_latency),
        ),
        previous=PeriodMetrics(
            calls=previous.calls,
            total_tokens=previous.total_tokens,
            cost=float(previous.cost),
            avg_latency=_avg_latency(previous.avg_latency),
        ),
        recent_logs=[RecentLogItem.model_validate(row) for row in recent],
    )


def get_token_stats(db: Session, project: Project, query: StatsQuery) -> TokensOut:
    summary = _summarize(db, project.id, query.start_date, query.end_date)
    per_day = _daily_totals(
        db,
        project.id,
        query.start_date,
        query.end_date,
        query.tz,
        LLMRequest.total_tokens,
    )

    series = []
    for day in _day_skeleton(query.start_date, query.end_date, query.tzinfo):
        # 区间内没有调用的日期在这里补成 0：不补的话折线图会从缺失的那天直接连到
        # 后一天，看起来像「那天也有数据」
        calls, tokens = per_day.get(day, (0, 0))
        series.append(TokenPoint(date=day, calls=calls, total_tokens=tokens))

    return TokensOut(
        summary=TokenSummary(
            calls=summary.calls,
            input_tokens=summary.input_tokens,
            output_tokens=summary.output_tokens,
            total_tokens=summary.total_tokens,
        ),
        series=series,
    )


def get_cost_stats(db: Session, project: Project, query: StatsQuery) -> CostsOut:
    summary = _summarize(db, project.id, query.start_date, query.end_date)
    per_day = _daily_totals(
        db,
        project.id,
        query.start_date,
        query.end_date,
        query.tz,
        LLMRequest.estimated_cost,
    )

    series = []
    for day in _day_skeleton(query.start_date, query.end_date, query.tzinfo):
        calls, cost = per_day.get(day, (0, 0))
        series.append(CostPoint(date=day, calls=calls, cost=float(cost)))

    return CostsOut(
        summary=CostSummary(calls=summary.calls, cost=float(summary.cost)),
        series=series,
    )


def get_model_stats(db: Session, project: Project, query: StatsQuery) -> ModelsOut:
    rows = db.execute(
        select(
            LLMRequest.model,
            func.count().label("calls"),
            func.coalesce(func.sum(LLMRequest.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(LLMRequest.estimated_cost), 0).label("cost"),
            func.avg(LLMRequest.latency)
            .filter(LLMRequest.status == "success")
            .label("avg_latency"),
        )
        .where(*_conditions(project.id, query.start_date, query.end_date))
        .group_by(LLMRequest.model)
        .order_by(func.count().desc(), LLMRequest.model)
    ).all()

    total_calls = sum(row.calls for row in rows)

    items = [
        ModelUsageItem(
            model=row.model,
            calls=row.calls,
            total_tokens=row.total_tokens,
            cost=float(row.cost),
            avg_latency=_avg_latency(row.avg_latency),
            # 占比按调用次数算（不是 Token 数），与设计文档 03 的示例口径一致。
            # 各自四舍五入到 1 位，几项加起来可能是 99.9 或 100.1，这是显示口径
            percentage=round(row.calls / total_calls * 100, 1) if total_calls else 0.0,
        )
        for row in rows
    ]

    return ModelsOut(items=items)
