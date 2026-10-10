"""调用日志：接入方用 X-API-Key 上报，登录用户在项目下读取列表与详情。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_api_key_project, get_own_log, get_own_project
from app.models import LLMRequest, Project
from app.schemas.log import (
    LogCreatedOut,
    LogDetailOut,
    LogIngestIn,
    LogListItem,
    LogListOut,
    LogListQuery,
)
from app.services import ingest as ingest_service
from app.services import logs as logs_service

router = APIRouter(prefix="/logs", tags=["logs"])

# 列表挂在 /projects/{project_id}/logs 下，归属校验直接复用 get_own_project；
# 详情接口的路径里没有 project_id，用不上那个依赖，只能靠 get_own_log 自带校验
project_logs_router = APIRouter(prefix="/projects", tags=["logs"])


@project_logs_router.get("/{project_id}/logs", summary="调用日志列表")
def list_logs(
    # 这里必须用 Query() 标注，不能写 Depends()：后者把 model 当普通函数调用，
    # model_validator 抛的 ValidationError 不经过参数校验层，min_tokens > max_tokens
    # 会变成 500 而不是 422
    query: Annotated[LogListQuery, Query()],
    project: Project = Depends(get_own_project),
    db: Session = Depends(get_db),
) -> LogListOut:
    rows, total = logs_service.list_logs(db, project, query)
    return LogListOut(
        items=[LogListItem.model_validate(row) for row in rows],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


@router.get("/{log_id}", summary="调用日志详情")
def get_log(log: LLMRequest = Depends(get_own_log)) -> LogDetailOut:
    return LogDetailOut.model_validate(log)


# 认证用的是 X-API-Key 而不是 JWT：这个接口的使用者是接入方的程序、不是登录的人，
# 路径写 "" 而不是 "/" 的约定同 projects.py
@router.post("", status_code=status.HTTP_201_CREATED, summary="上报调用日志")
def ingest_log(
    payload: LogIngestIn,
    project: Project = Depends(get_api_key_project),
    db: Session = Depends(get_db),
) -> LogCreatedOut:
    return LogCreatedOut.model_validate(
        ingest_service.ingest_log(db, project, payload)
    )
