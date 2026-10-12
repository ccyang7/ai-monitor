"""项目监控中心统计接口的参数与响应模型。"""

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, model_validator

from app.schemas.anomaly import AnomalyCounts
from app.schemas.common import TimeRangeQuery

# 小时骨架同理。留出 1 小时余量是因为夏令时：秋令退的那天本地日历日是 25 小时，
# 按绝对时长卡死 24 小时的话，「今日」在那些时区每年有一天会直接 422
MAX_HOUR_SPAN = timedelta(days=1, hours=1)


class StatsQuery(TimeRangeQuery):
    """四个统计接口共用的时间范围与分桶时区。"""

    # 粒度由调用方给，不在服务端按跨度自动推断：「一天」是个本地日历概念，
    # 服务端算不出用户那边的一天有多长（夏令时那天是 25 小时）
    granularity: Literal["day", "hour"] = "day"

    @model_validator(mode="after")
    def _check_range(self) -> "StatsQuery":
        # 按小时的跨度上限排在通用跨度检查前面：跨 5000 天却传 hour 时，
        # 报「不能超过 1 天」比报「不能超过 3660 天」更贴近用户犯的那个错
        if self.granularity == "hour" and self.end_date - self.start_date > MAX_HOUR_SPAN:
            raise ValueError("按小时统计时，区间不能超过 1 天")
        self._check_span()
        return self


class PeriodMetrics(BaseModel):
    """一个时间区间内的汇总指标，overview 用它表示当前区间和环比区间。"""

    calls: int
    total_tokens: int
    cost: float

    # 区间内一次成功调用都没有时是 null 而不是 0：「没有可统计的响应时间」
    # 和「响应时间是 0 毫秒」是两回事，后者在前端会被读成「快得离谱」
    avg_latency: int | None


class OverviewOut(BaseModel):
    summary: PeriodMetrics
    # 上一个等长区间的同一组指标，供前端算环比（「比昨天多 32%」）
    previous: PeriodMetrics
    # 按类型分的异常条数，喂统计卡上的角标。判定由 anomaly service 完成，
    # 概览页只回答「哪几项不对」—— 明细去异常列表页看
    anomaly_counts: AnomalyCounts
    # token / latency 这两类到底判没判（口径同 AnomalyListOut.baseline_available）。
    # 没有它的话，四格全是 0 时概览只能说「没有异常」—— 可对一个还在攒数据的新项目，
    # 那其实是「还没开始判」。这里只要一个布尔值，按模型的明细归异常列表页管
    baseline_available: bool


class TokenSummary(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


class TokenPoint(BaseModel):
    # 标签字符串，不是时刻：按天是 '2026-10-01'，按小时是 '2026-10-12T14:00'。
    # 用 str 而不是 date/datetime，是为了让两种粒度装得进同一个字段，且前端不必
    # 去解析它 —— 解析就会踩 new Date('2026-10-01') 按 UTC、new Date('2026-10-01T00:00:00')
    # 按本地解析的坑
    date: str
    calls: int
    total_tokens: int


class TokensOut(BaseModel):
    summary: TokenSummary
    series: list[TokenPoint]


class CostSummary(BaseModel):
    calls: int
    cost: float


class CostPoint(BaseModel):
    date: str
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
    # 三个口径的占比都由服务端算，一套口径一个字段。前端自己算的话，
    # 几个百分比加起来常常不是 100%，而且同一个模型在三套口径下的
    # 舍入方式得各自对齐，迟早对不齐
    call_percentage: float
    token_percentage: float
    cost_percentage: float


class ModelsOut(BaseModel):
    items: list[ModelUsageItem]
