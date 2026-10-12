"""项目的异常列表接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_own_project
from app.models import Project
from app.schemas.anomaly import AnomalyListOut, AnomalyQuery
from app.services import anomaly as anomaly_service

# 挂 /projects/{project_id}/anomalies：隔离由路径天然表达，
# 归属校验直接复用 get_own_project
router = APIRouter(prefix="/projects", tags=["anomaly"])


# 查询参数必须写 Query() 标注，不能写 Depends()：后者会让 model_validator 抛的
# ValidationError 绕过参数校验层，end <= start 变成 500 而不是 422
@router.get("/{project_id}/anomalies", summary="异常列表")
def list_anomalies(
    query: Annotated[AnomalyQuery, Query()],
    project: Project = Depends(get_own_project),
    db: Session = Depends(get_db),
) -> AnomalyListOut:
    return anomaly_service.list_anomalies(db, project, query)
