"""项目监控中心统计接口的参数与响应模型。"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# 补零骨架是按天逐个生成的，区间上限直接决定它有多大。十年是设计文档举的极端例子，
# 再往上放行就等于让一个请求能造出几十万个日期对象
MAX_RANGE_DAYS = 3660


def _assume_utc(value: datetime | None) -> datetime | None:
    # 与上报接口、日志列表同一规则：不带时区的时间按 UTC 解释。
    # 按服务器时区解释的话，本机 UTC+8 会让所有统计整体偏移 8 小时
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class StatsQuery(BaseModel):
    """四个统计接口共用的时间范围与分桶时区。"""

    start_date: datetime
    end_date: datetime

    tz: str = "UTC"

    @field_validator("start_date", "end_date")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return _assume_utc(value)

    @field_validator("tz")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        # 非法时区名漏到 PG 里是 InvalidParameterValue，会冒泡成 500。
        # 而且这个对象算补零骨架时本来就要构造，校验是顺手的事
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"未知的时区：{value}") from None
        return value

    @model_validator(mode="after")
    def _check_range(self) -> "StatsQuery":
        # 先归一成带时区再比大小：naive 和 aware 直接比较会抛 TypeError 变成 500
        if self.end_date <= self.start_date:
            raise ValueError("end_date 必须晚于 start_date（区间左闭右开）")

        if (self.end_date - self.start_date).days > MAX_RANGE_DAYS:
            raise ValueError(f"时间区间不能超过 {MAX_RANGE_DAYS} 天")

        return self

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.tz)


class PeriodMetrics(BaseModel):
    """一个时间区间内的汇总指标，overview 用它表示当前区间和环比区间。"""

    calls: int
    total_tokens: int
    cost: float

    # 区间内一次成功调用都没有时是 null 而不是 0：「没有可统计的响应时间」
    # 和「响应时间是 0 毫秒」是两回事，后者在前端会被读成「快得离谱」
    avg_latency: int | None


class RecentLogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    model: str
    total_tokens: int
    latency: int
    status: str
    created_at: datetime


class OverviewOut(BaseModel):
    summary: PeriodMetrics
    # 上一个等长区间的同一组指标，供前端算环比（「比昨天多 32%」）
    previous: PeriodMetrics
    recent_logs: list[RecentLogItem]
    # 阶段 9（异常分析）接上之前恒为 null。给 null 而不是 0：前端能借此区分
    # 「这个项目没有异常」和「异常功能还没做」，接上之后前端不用改
    anomaly_count: int | None = None


class TokenSummary(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


class TokenPoint(BaseModel):
    # date 而不是 datetime：前端拿到就是「哪一天」这个标签，不涉及时刻，
    # 也就不会踩 new Date('2026-10-01') 按 UTC 解析、new Date('2026-10-01T00:00:00')
    # 按本地解析的坑
    date: date
    calls: int
    total_tokens: int


class TokensOut(BaseModel):
    summary: TokenSummary
    series: list[TokenPoint]


class CostSummary(BaseModel):
    calls: int
    cost: float


class CostPoint(BaseModel):
    date: date
    calls: int
    cost: float


class CostsOut(BaseModel):
    summary: CostSummary
    series: list[CostPoint]


class ModelUsageItem(BaseModel):
    model: str
    calls: int
    total_tokens: int
    cost: float
    avg_latency: int | None
    # 服务端算：前端各算各的，几个百分比加起来常常不是 100%
    percentage: float


class ModelsOut(BaseModel):
    items: list[ModelUsageItem]
