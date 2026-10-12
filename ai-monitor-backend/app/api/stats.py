"""项目监控中心的统计接口：概览、Token、成本、模型分布。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_own_project
from app.models import Project
from app.schemas.stats import CostsOut, ModelsOut, OverviewOut, StatsQuery, TokensOut
from app.services import stats as stats_service

# 全部挂在 /projects/{project_id}/stats/... 下：隔离由路径天然表达，
# 归属校验直接复用 get_own_project，不给自己留一个忘记过滤 project_id 的机会
router = APIRouter(prefix="/projects", tags=["stats"])


# 查询参数必须用 Query() 标注，不能写 Depends()：后者把 model 当普通函数调用，
# model_validator 抛的 ValidationError 不经过参数校验层，end <= start 会变成 500 而不是 422
@router.get("/{project_id}/stats/overview", summary="项目概览")
def get_overview(
    query: Annotated[StatsQuery, Query()],
    project: Project = Depends(get_own_project),
    db: Session = Depends(get_db),
) -> OverviewOut:
    return stats_service.get_overview(db, project, query)


@router.get("/{project_id}/stats/tokens", summary="Token 统计与趋势")
def get_token_stats(
    query: Annotated[StatsQuery, Query()],
    project: Project = Depends(get_own_project),
    db: Session = Depends(get_db),
) -> TokensOut:
    return stats_service.get_token_stats(db, project, query)


@router.get("/{project_id}/stats/costs", summary="成本统计与趋势")
def get_cost_stats(
    query: Annotated[StatsQuery, Query()],
    project: Project = Depends(get_own_project),
    db: Session = Depends(get_db),
) -> CostsOut:
    return stats_service.get_cost_stats(db, project, query)


@router.get("/{project_id}/stats/models", summary="模型使用情况")
def get_model_stats(
    query: Annotated[StatsQuery, Query()],
    project: Project = Depends(get_own_project),
    db: Session = Depends(get_db),
) -> ModelsOut:
    return stats_service.get_model_stats(db, project, query)
