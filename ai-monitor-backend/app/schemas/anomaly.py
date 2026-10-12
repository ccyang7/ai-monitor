"""异常分析接口的查询参数与响应模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import TimeRangeQuery

# 四种类型共用一个响应结构：单条判定（token / latency / error）给 log_id，
# 窗口判定（error_rate）没有具体记录、改给 window 标签，两边各填自己有的字段。
# 不拆成两个模型是因为 items 一旦变成联合类型，前端解析的成本比多几个可空字段高
AnomalyType = Literal["token", "latency", "error", "error_rate"]


class AnomalyQuery(TimeRangeQuery):
    type: AnomalyType | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)

    @model_validator(mode="after")
    def _check_range(self) -> "AnomalyQuery":
        self._check_span()
        return self


class AnomalyItem(BaseModel):
    type: AnomalyType
    # 窗口类异常取窗口起点的时刻，好和单条记录一起排序，不是「异常发生的时刻」
    created_at: datetime

    log_id: int | None = None
    window: str | None = None

    model: str | None = None

    # 窗口判定的 baseline 装的是阈值（0.2）而不是滑动基线 —— 让前端能用同一套
    # 「实际值 vs 参考值」的渲染，不必为错误率单独写一条分支
    value: float | None = None
    baseline: float | None = None
    ratio: float | None = None

    # 只有失败调用有；token / latency 异常的错误信息是空的
    error_message: str | None = None


class AnomalyCounts(BaseModel):
    """按类型分的异常条数。

    概览页的统计卡靠它各看各的：Token 卡只关心 token 那一格，平均响应卡只关心
    latency 那一格。只给总数的话，四张卡要么一起挂提示要么一起不挂 —— 那提示
    就没法说是哪项出了问题。
    """

    token: int
    latency: int
    error: int
    error_rate: int


class InsufficientBaseline(BaseModel):
    """样本不够、暂时判不了的模型。

    光给一个布尔值时，界面只能说「数据太少」；带上还差多少次，
    用户才知道再积累几条就能开始判定。
    """

    model: str
    samples: int
    required: int


class AnomalyListOut(BaseModel):
    items: list[AnomalyItem]
    total: int
    page: int
    page_size: int

    # 「没有异常」和「样本不足、还没法判定」是两回事：前端据此决定说
    # 「一切正常」还是「数据太少，暂时无法分析异常」
    baseline_available: bool

    # 只列样本不足的模型：达标的不需要提示，全列出来等于让前端自己筛
    insufficient_baselines: list[InsufficientBaseline]
