"""异常判定：按模型算近 7 天基线，逐条比对 Token 与延迟，另加失败调用和窗口错误率。"""

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import LLMRequest, Project, ProjectAnomalySettings
from app.schemas.anomaly import (
    AnomalyCounts,
    AnomalyItem,
    AnomalyListOut,
    AnomalyQuery,
    AnomalySettingField,
    AnomalySettingsIn,
    AnomalySettingsOut,
    AnomalyType,
    InsufficientBaseline,
)
from app.services.stats import bucket_expr, range_conditions

BASELINE_WINDOW = timedelta(days=7)


@dataclass
class _Anomaly:
    """四类判定共用一条记录，各自只填自己有的字段。"""

    type: AnomalyType
    created_at: datetime
    log_id: int | None = None
    window: str | None = None
    model: str | None = None
    value: float | None = None
    baseline: float | None = None
    ratio: float | None = None
    error_message: str | None = None


@dataclass
class _Baselines:
    """基线查询的两个产出：够样本、能用来判定的，以及样本不足、只能如实说出来的。"""

    # model -> (平均 Token, 平均延迟)
    ready: dict[str, tuple[float, float]]
    # (模型名, 已有样本数)
    insufficient: list[tuple[str, int]]


@dataclass(frozen=True)
class AnomalyThresholds:
    """这次判定生效的三个阈值：项目自定义的优先，没设过就回落到 config.py 的默认值。

    字段名与项目配置表的列名、config.py 的设置名三者一致，解析时就是
    「这一列有值就用它，为空就取同名默认值」，不用来回对表。
    """

    token_anomaly_ratio: float
    latency_anomaly_ratio: float
    error_rate_threshold: float

    @classmethod
    def load(cls, db: Session, project_id: int) -> "AnomalyThresholds":
        row = _settings_row(db, project_id)
        # 逐项回落，不是整行二选一：可以只自定义其中一项，另两项继续跟随默认
        return cls(
            token_anomaly_ratio=_resolve_value(
                row, "token_anomaly_ratio", settings.token_anomaly_ratio
            ),
            latency_anomaly_ratio=_resolve_value(
                row, "latency_anomaly_ratio", settings.latency_anomaly_ratio
            ),
            error_rate_threshold=_resolve_value(
                row, "error_rate_threshold", settings.error_rate_threshold
            ),
        )


def _settings_row(
    db: Session, project_id: int
) -> ProjectAnomalySettings | None:
    return db.execute(
        select(ProjectAnomalySettings).where(
            ProjectAnomalySettings.project_id == project_id
        )
    ).scalar_one_or_none()


def _resolve_value(
    row: ProjectAnomalySettings | None, column: str, default: float
) -> float:
    """NULL 表示这一项跟随默认 —— 这条规则只在这一个地方实现。"""
    value = getattr(row, column) if row is not None else None
    return float(default) if value is None else float(value)


def _baselines(db: Session, project_id: int) -> _Baselines:
    """每个模型近 7 天的成功调用的平均 Token 与平均延迟，另附样本不足的模型清单。"""
    # 窗口取「现在往前 7 天」而不是跟着查询区间走（设计 05 §5.1）：基线描述的是这个
    # 模型如今正常该是什么水位。代价有两个 —— 查历史区间时用的仍是今天的基线，
    # 以及判定期和基线期会重叠，异常样本把自己所在的基线抬高一点（自我稀释）。
    # V1 接受这两个近似（设计 05 §5.3），数据量上来后可以改成 T-14~T-7 当基线期
    rows = db.execute(
        select(
            LLMRequest.model,
            func.avg(LLMRequest.total_tokens).label("avg_tokens"),
            func.avg(LLMRequest.latency).label("avg_latency"),
            func.count().label("samples"),
        )
        .where(
            LLMRequest.project_id == project_id,
            # 失败样本不进基线：超时被记录下来的 latency 是超时上限本身，
            # 把它平均进去会让「正常水位」被拉高，真正的慢调用就藏起来了
            LLMRequest.status == "success",
            LLMRequest.created_at >= datetime.now(timezone.utc) - BASELINE_WINDOW,
        )
        .group_by(LLMRequest.model)
    ).all()

    # 样本门槛从 SQL 的 HAVING 挪到 Python：反正要逐行看，顺路把样本数带出来，
    # 界面就能说出「哪个模型还差几次」，而不是笼统地报一句「样本不足」。
    # 代价是没达标的模型也进了结果集 —— 一行一个模型，不提也罢
    #
    # 样本不足不判定：两条记录算出来的「平均值」谁信谁误报。宁可漏报不可误报 ——
    # 告警一旦开始误报，用户就会忽略它，真异常出现时也一起失效了
    min_samples = settings.baseline_min_samples
    return _Baselines(
        ready={
            row.model: (float(row.avg_tokens), float(row.avg_latency))
            for row in rows
            if row.samples >= min_samples
        },
        insufficient=sorted(
            (row.model, row.samples) for row in rows if row.samples < min_samples
        ),
    )


def _threshold_anomalies(
    db: Session,
    project_id: int,
    start: datetime,
    end: datetime,
    baselines: dict[str, tuple[float, float]],
    thresholds: AnomalyThresholds,
) -> list[_Anomaly]:
    if not baselines:
        return []

    rows = db.execute(
        select(
            LLMRequest.id,
            LLMRequest.model,
            LLMRequest.total_tokens,
            LLMRequest.latency,
            LLMRequest.created_at,
        )
        .where(
            *range_conditions(project_id, start, end),
            LLMRequest.status == "success",
            # 没有基线的模型直接不查出来，省得逐条比对时再跳过一遍。
            # 只取这 5 列是有意的：input / output 是大文本，SELECT 列表里不出现它们，
            # PG 连 TOAST 表都不会去读
            LLMRequest.model.in_(list(baselines)),
        )
    ).all()

    found: list[_Anomaly] = []
    for row in rows:
        avg_tokens, avg_latency = baselines[row.model]

        # 基线为 0 时跳过该类：0 的任意倍还是 0，比不出异常，硬算只会除零
        if (
            avg_tokens
            and row.total_tokens > avg_tokens * thresholds.token_anomaly_ratio
        ):
            found.append(
                _Anomaly(
                    type="token",
                    log_id=row.id,
                    model=row.model,
                    value=row.total_tokens,
                    baseline=round(avg_tokens, 1),
                    ratio=round(row.total_tokens / avg_tokens, 1),
                    created_at=row.created_at,
                )
            )

        if (
            avg_latency
            and row.latency > avg_latency * thresholds.latency_anomaly_ratio
        ):
            found.append(
                _Anomaly(
                    type="latency",
                    log_id=row.id,
                    model=row.model,
                    value=row.latency,
                    baseline=round(avg_latency, 1),
                    ratio=round(row.latency / avg_latency, 1),
                    created_at=row.created_at,
                )
            )

    return found


def _error_anomalies(
    db: Session, project_id: int, start: datetime, end: datetime
) -> list[_Anomaly]:
    # 失败调用不做阈值判定：报错就是报错，没有「比平时错得更厉害」这种事
    rows = db.execute(
        select(
            LLMRequest.id,
            LLMRequest.model,
            LLMRequest.created_at,
            LLMRequest.error_message,
        ).where(
            *range_conditions(project_id, start, end),
            LLMRequest.status == "error",
        )
    ).all()

    return [
        _Anomaly(
            type="error",
            log_id=row.id,
            model=row.model,
            error_message=row.error_message,
            created_at=row.created_at,
        )
        for row in rows
    ]


def _error_rate_anomalies(
    db: Session,
    project_id: int,
    start: datetime,
    end: datetime,
    tz: str,
    thresholds: AnomalyThresholds,
) -> list[_Anomaly]:
    # 只有这一类是窗口判定：看的是「这一天的整体错误比例」，不是某一次调用，
    # 所以 log_id 为 null、改给 window 标签（设计 05 §6.1）
    bucket = bucket_expr(tz, "day")
    rows = db.execute(
        select(
            bucket.label("bucket"),
            func.count().label("calls"),
            func.count().filter(LLMRequest.status == "error").label("errors"),
        )
        .where(*range_conditions(project_id, start, end))
        .group_by(bucket)
        # 窗口太小的日子不判定：3 次调用里错了 1 次就是 33%，可那什么都说明不了。
        # 门槛复用样本数配置 —— 两个参数要的其实是同一件事：多少条才算数
        .having(func.count() >= settings.baseline_min_samples)
        .order_by(bucket)
    ).all()

    found: list[_Anomaly] = []
    for row in rows:
        rate = row.errors / row.calls
        if rate > thresholds.error_rate_threshold:
            found.append(
                _Anomaly(
                    type="error_rate",
                    window=row.bucket.isoformat(),
                    value=round(rate, 4),
                    # 窗口判定的 baseline 装的是阈值（0.2）而非滑动基线，
                    # 好让前端用同一套「实际值 vs 参考值」渲染两种形态
                    baseline=thresholds.error_rate_threshold,
                    ratio=round(rate / thresholds.error_rate_threshold, 1),
                    # 窗口起点的时刻。AT TIME ZONE 交回来的是当地墙钟值，先补回
                    # 时区信息再转 UTC —— 直接 replace 成 UTC 会整体差一个时区
                    created_at=datetime.combine(
                        row.bucket, time.min, tzinfo=ZoneInfo(tz)
                    ).astimezone(timezone.utc),
                )
            )

    return found


def _detect(
    db: Session, project_id: int, start: datetime, end: datetime, tz: str
) -> tuple[list[_Anomaly], _Baselines]:
    # 阈值查一次往下传，不让各个判定函数各自去读配置 —— 各读各的就会出现
    # 同一份响应里两类异常用的是两套阈值
    thresholds = AnomalyThresholds.load(db, project_id)
    baselines = _baselines(db, project_id)
    items = (
        _threshold_anomalies(db, project_id, start, end, baselines.ready, thresholds)
        + _error_anomalies(db, project_id, start, end)
        + _error_rate_anomalies(db, project_id, start, end, tz, thresholds)
    )
    # 基线可用与否只影响 token / latency 两类：失败调用和错误率不依赖基线，
    # 项目刚接入也能判出来
    return items, baselines


def list_anomalies(db: Session, project: Project, query: AnomalyQuery) -> AnomalyListOut:
    items, baselines = _detect(
        db, project.id, query.start_date, query.end_date, query.tz
    )

    if query.type:
        items = [item for item in items if item.type == query.type]

    # 同一时刻可能有多条（窗口起点常常撞上整点），id 是防翻页重复/漏的兜底键。
    # 窗口类没有 id，用 0 补位，反正同刻的窗口最多一个
    items.sort(key=lambda item: (item.created_at, item.log_id or 0), reverse=True)

    total = len(items)
    offset = (query.page - 1) * query.page_size

    return AnomalyListOut(
        items=[AnomalyItem(**asdict(item)) for item in items[offset : offset + query.page_size]],
        total=total,
        page=query.page,
        page_size=query.page_size,
        baseline_available=bool(baselines.ready),
        insufficient_baselines=[
            InsufficientBaseline(
                model=model, samples=samples, required=settings.baseline_min_samples
            )
            for model, samples in baselines.insufficient
        ],
    )


def count_anomalies(
    db: Session, project_id: int, start: datetime, end: datetime, tz: str
) -> tuple[AnomalyCounts, bool]:
    """按类型数的异常条数，外加基线可不可用。

    返回元组而不是把可用性塞进 AnomalyCounts：那个模型对应的是响应里的
    anomaly_counts 字段，多塞一个字段进去就得给它单起一个响应模型。
    """
    items, baselines = _detect(db, project_id, start, end, tz)
    # Counter 对没出现过的键返回 0，四类各自取一次即可，不用先补零
    tally = Counter(item.type for item in items)
    return (
        AnomalyCounts(
            token=tally["token"],
            latency=tally["latency"],
            error=tally["error"],
            error_rate=tally["error_rate"],
        ),
        bool(baselines.ready),
    )


def _field_state(
    row: ProjectAnomalySettings | None, column: str, default: float
) -> AnomalySettingField:
    value = getattr(row, column) if row is not None else None
    return AnomalySettingField(
        value=_resolve_value(row, column, default),
        default=float(default),
        is_custom=value is not None,
    )


def get_settings(db: Session, project: Project) -> AnomalySettingsOut:
    row = _settings_row(db, project.id)
    return AnomalySettingsOut(
        token_anomaly_ratio=_field_state(
            row, "token_anomaly_ratio", settings.token_anomaly_ratio
        ),
        latency_anomaly_ratio=_field_state(
            row, "latency_anomaly_ratio", settings.latency_anomaly_ratio
        ),
        error_rate_threshold=_field_state(
            row, "error_rate_threshold", settings.error_rate_threshold
        ),
    )


def save_settings(
    db: Session, project: Project, payload: AnomalySettingsIn
) -> AnomalySettingsOut:
    row = _settings_row(db, project.id)
    values = {
        "token_anomaly_ratio": payload.token_anomaly_ratio,
        "latency_anomaly_ratio": payload.latency_anomaly_ratio,
        "error_rate_threshold": payload.error_rate_threshold,
    }

    if all(value is None for value in values.values()):
        # 三项都跟随默认时这一行没有信息量：删掉而不是留一行全 NULL。
        # 「没有行」和「全 NULL」在判定时等价，留着只会让表里攒一堆空壳
        if row is not None:
            db.delete(row)
    elif row is not None:
        for column, value in values.items():
            setattr(row, column, value)
    else:
        db.add(ProjectAnomalySettings(project_id=project.id, **values))

    db.commit()
    # 返回保存后的完整状态，前端不用再补一次 GET
    return get_settings(db, project)
