"""查询参数的公共片段：时间区间的归一化与时区合法性校验。"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, field_validator

# 补零骨架是按天逐个生成的，区间上限直接决定它有多大。十年是设计文档举的极端例子，
# 再往上放行就等于让一个请求能造出几十万个日期对象
MAX_RANGE_DAYS = 3660


def _assume_utc(value: datetime | None) -> datetime | None:
    # 与上报接口、日志列表同一规则：不带时区的时间按 UTC 解释。
    # 按服务器时区解释的话，本机 UTC+8 会让所有统计整体偏移 8 小时
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class TimeRangeQuery(BaseModel):
    """带时间区间的查询参数的公共部分。

    跨度校验不放在这里：各接口的上限本来就不一样（按小时统计是 1 天），
    而 pydantic 的 model_validator 在继承链上同名覆盖的行为不够直观，
    不值得为了省几行去依赖它。基类提供 _check_span() 给子类调用。
    """

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

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def _check_span(self) -> None:
        # 先归一成带时区再比大小：naive 和 aware 直接比较会抛 TypeError 变成 500
        if self.end_date <= self.start_date:
            raise ValueError("end_date 必须晚于 start_date（区间左闭右开）")
        if (self.end_date - self.start_date).days > MAX_RANGE_DAYS:
            raise ValueError(f"时间区间不能超过 {MAX_RANGE_DAYS} 天")
