"""项目监控中心的统计查询：区间汇总、按天或按小时的趋势、模型分布。"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Date, func, select
from sqlalchemy.orm import Session

from app.models import LLMRequest, Project
from app.schemas.stats import (
    CostPoint,
    CostSummary,
    CostsOut,
    ModelUsageItem,
    ModelsOut,
    OverviewOut,
    PeriodMetrics,
    StatsQuery,
    TokenPoint,
    TokenSummary,
    TokensOut,
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


def _bucket_expr(tz: str, granularity: Literal["day", "hour"]):
    # 先转成目标时区的本地挂钟时间再截断。直接对 timestamptz 取日期是按数据库
    # 时区（UTC）截断的，UTC+8 的用户会看到「今天」的数据被切一半到昨天去
    local = LLMRequest.created_at.op("AT TIME ZONE")(tz)
    if granularity == "hour":
        return func.date_trunc("hour", local)
    return local.cast(Date)


def _bucket_skeleton(
    start: datetime, end: datetime, tz: ZoneInfo, granularity: Literal["day", "hour"]
) -> list[date] | list[datetime]:
    # 用 end 前的一微秒定位最后一个桶：end 常常正好落在某天 0 点（「近 7 天」的 end
    # 就是明天 0 点），直接拿 end 的日期会凭空多补一天
    last_moment = (end - timedelta(microseconds=1)).astimezone(tz)

    if granularity == "hour":
        # 这里必须生成 naive 对象。SQL 侧 AT TIME ZONE 之后返回的是不带时区的墙钟值，
        # 驱动给过来的是 naive datetime —— 骨架留着 tzinfo 的话，aware 和 naive 相等
        # 恒为 False（不报错），补零查询会静默全部落空，图上是一条 0 直线
        first = start.astimezone(tz).replace(minute=0, second=0, microsecond=0, tzinfo=None)
        last = last_moment.replace(minute=0, second=0, microsecond=0, tzinfo=None)

        hours = []
        cursor = first
        while cursor <= last:
            hours.append(cursor)
            cursor += timedelta(hours=1)
        return hours

    days = []
    cursor = start.astimezone(tz).date()
    while cursor <= last_moment.date():
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _label(bucket: date | datetime) -> str:
    # 按小时的要抹掉秒：datetime.isoformat() 默认给 '2026-10-12T14:00:00'，
    # 而这是个给人看的标签，不是等着被解析回去的时刻
    if isinstance(bucket, datetime):
        return bucket.isoformat(timespec="minutes")
    return bucket.isoformat()


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


def _percent(part: Decimal | int, total: Decimal | int) -> float:
    # 区间内一次调用都没有时 total 是 0，这里给 0 —— 直接除会 500
    if not total:
        return 0.0
    return round(float(part) / float(total) * 100, 1)


def _bucketed_totals(
    db: Session,
    project_id: int,
    start: datetime,
    end: datetime,
    tz: str,
    value_column,
    granularity: Literal["day", "hour"],
) -> dict[date | datetime, tuple[int, Decimal]]:
    """按桶汇总「调用次数 + 某个数值列」。空桶不在这里补，由调用方按骨架填。"""
    bucket = _bucket_expr(tz, granularity)
    rows = db.execute(
        select(
            bucket.label("bucket"),
            func.count().label("calls"),
            func.coalesce(func.sum(value_column), 0).label("value"),
        )
        .where(*_conditions(project_id, start, end))
        .group_by(bucket)
        .order_by(bucket)
    ).all()

    return {row.bucket: (row.calls, row.value) for row in rows}


def get_overview(db: Session, project: Project, query: StatsQuery) -> OverviewOut:
    start, end = query.start_date, query.end_date
    prev_start, prev_end = _previous_period(start, end)

    current = _summarize(db, project.id, start, end)
    previous = _summarize(db, project.id, prev_start, prev_end)

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
    )


def get_token_stats(db: Session, project: Project, query: StatsQuery) -> TokensOut:
    summary = _summarize(db, project.id, query.start_date, query.end_date)
    totals = _bucketed_totals(
        db,
        project.id,
        query.start_date,
        query.end_date,
        query.tz,
        LLMRequest.total_tokens,
        query.granularity,
    )

    series = []
    for bucket in _bucket_skeleton(
        query.start_date, query.end_date, query.tzinfo, query.granularity
    ):
        # 区间内没有调用的桶在这里补成 0：不补的话折线图会从缺失的那个桶直接连到
        # 下一个，看起来像「那段时间也有数据」
        calls, tokens = totals.get(bucket, (0, 0))
        series.append(TokenPoint(date=_label(bucket), calls=calls, total_tokens=tokens))

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
    totals = _bucketed_totals(
        db,
        project.id,
        query.start_date,
        query.end_date,
        query.tz,
        LLMRequest.estimated_cost,
        query.granularity,
    )

    series = []
    for bucket in _bucket_skeleton(
        query.start_date, query.end_date, query.tzinfo, query.granularity
    ):
        calls, cost = totals.get(bucket, (0, 0))
        series.append(CostPoint(date=_label(bucket), calls=calls, cost=float(cost)))

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
    total_tokens = sum(row.total_tokens for row in rows)
    total_cost = sum(row.cost for row in rows)

    items = [
        ModelUsageItem(
            model=row.model,
            calls=row.calls,
            total_tokens=row.total_tokens,
            cost=float(row.cost),
            avg_latency=_avg_latency(row.avg_latency),
            # 各自四舍五入到 1 位，几项加起来可能是 99.9 或 100.1，这是显示口径
            call_percentage=_percent(row.calls, total_calls),
            token_percentage=_percent(row.total_tokens, total_tokens),
            cost_percentage=_percent(row.cost, total_cost),
        )
        for row in rows
    ]

    return ModelsOut(items=items)
