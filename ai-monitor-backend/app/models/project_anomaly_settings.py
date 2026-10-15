"""项目级异常判定标准表：字段为 NULL 表示该项目这一项沿用 config.py 里的默认阈值。"""

from sqlalchemy import BigInteger, CheckConstraint, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ProjectAnomalySettings(Base, TimestampMixin):
    __tablename__ = "project_anomaly_settings"

    # CHECK 写在这里只是让模型完整描述表结构，真正生效的是 sql/schema.sql
    __table_args__ = (
        CheckConstraint(
            "token_anomaly_ratio IS NULL OR token_anomaly_ratio BETWEEN 1.5 AND 20",
            name="ck_project_anomaly_settings_token_ratio",
        ),
        CheckConstraint(
            "latency_anomaly_ratio IS NULL OR latency_anomaly_ratio BETWEEN 1.5 AND 20",
            name="ck_project_anomaly_settings_latency_ratio",
        ),
        CheckConstraint(
            "error_rate_threshold IS NULL OR error_rate_threshold BETWEEN 0.05 AND 1.0",
            name="ck_project_anomaly_settings_error_rate",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # unique 是「一个项目最多一行」的保证：没有它，重复保存会攒出多行，
    # 判定时读到哪一行取决于查询顺序
    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="CASCADE"),
        unique=True,
    )

    # 三列都可空：NULL = 这一项跟随系统默认，界面上的「跟随默认」开关直接对应它
    token_anomaly_ratio: Mapped[float | None] = mapped_column(Float)
    latency_anomaly_ratio: Mapped[float | None] = mapped_column(Float)
    error_rate_threshold: Mapped[float | None] = mapped_column(Float)
