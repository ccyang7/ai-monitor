"""项目相关的请求与响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectCreateIn(BaseModel):
    # max_length 必须和数据库 VARCHAR 对齐，否则超长值是 500 而不是 422
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    tech_stack: str | None = Field(default=None, max_length=50)


class ProjectUpdateIn(BaseModel):
    """三个字段都可选：没传 = 不动它，传 null = 清空它（name 除外，name 不能清空）。"""

    # max_length 必须和数据库 VARCHAR 对齐，否则超长值是 500 而不是 422
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    tech_stack: str | None = Field(default=None, max_length=50)

    @field_validator("name")
    @classmethod
    def _name_cannot_be_nulled(cls, value: str | None) -> str | None:
        # 字段「没传」时这个校验器根本不会跑，所以走到这里的 None 一定是显式传的 null。
        # name 那列是 NOT NULL，放行等于拿 500 换一个 422
        if value is None:
            raise ValueError("项目名称不能为 null，不修改请不要传这个字段")
        return value


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    tech_stack: str | None
    status: str
    created_at: datetime


class ProjectCreatedOut(ProjectOut):
    api_key: str


class ProjectListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    tech_stack: str | None
    status: str

    # 由 service 用 llm_requests 聚合后显式传入。没有上报记录时是 null，
    # 前端靠它区分「还没接入」和「上报正常」—— 不能编造，也不能拿项目创建时间冒充
    last_called_at: datetime | None = None

    # 口径是「最近 7 天有没有四类异常」，与异常页的默认区间一致
    has_anomaly: bool = False


class ProjectListOut(BaseModel):
    items: list[ProjectListItem]
    total: int
