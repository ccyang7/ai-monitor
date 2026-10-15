"""项目的增删查，以及项目下 API Key 的列表、新建与吊销。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, get_own_api_key, get_own_project
from app.models import ApiKey, Project, User
from app.schemas.api_key import ApiKeyCreatedOut, ApiKeyOut
from app.schemas.project import (
    ProjectCreateIn,
    ProjectCreatedOut,
    ProjectListItem,
    ProjectListOut,
    ProjectOut,
    ProjectUpdateIn,
)
from app.services import api_key as api_key_service
from app.services import project as project_service

router = APIRouter(prefix="/projects", tags=["projects"])


# 路径写 "" 而不是 "/"：后者生成 /projects/，访问 /projects 会吃一个 307 重定向，
# 而 TestClient 默认跟随重定向，这个坑很难发现
@router.get("", summary="项目列表")
def list_projects(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectListOut:
    rows = project_service.list_projects(db, user)
    return ProjectListOut(
        items=[
            ProjectListItem(
                id=project.id,
                name=project.name,
                tech_stack=project.tech_stack,
                status=project.status,
                last_called_at=last_called_at,
                has_anomaly=has_anomaly,
            )
            for project, last_called_at, has_anomaly in rows
        ],
        total=len(rows),
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="创建项目")
def create_project(
    payload: ProjectCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectCreatedOut:
    project, plaintext = project_service.create_project(db, user, payload)
    return ProjectCreatedOut(
        **ProjectOut.model_validate(project).model_dump(),
        api_key=plaintext,
    )


@router.get("/{project_id}", summary="项目详情")
def get_project(project: Project = Depends(get_own_project)) -> ProjectOut:
    return ProjectOut.model_validate(project)


@router.patch("/{project_id}", summary="编辑项目")
def update_project(
    payload: ProjectUpdateIn,
    project: Project = Depends(get_own_project),
    db: Session = Depends(get_db),
) -> ProjectOut:
    updated = project_service.update_project(db, project, payload)
    return ProjectOut.model_validate(updated)


@router.delete(
    "/{project_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除项目"
)
def delete_project(
    project: Project = Depends(get_own_project),
    db: Session = Depends(get_db),
) -> None:
    project_service.delete_project(db, project)


@router.get("/{project_id}/api-keys", summary="项目的 Key 列表")
def list_api_keys(
    project: Project = Depends(get_own_project),
    db: Session = Depends(get_db),
) -> list[ApiKeyOut]:
    return [ApiKeyOut.model_validate(k) for k in api_key_service.list_keys(db, project)]


@router.post(
    "/{project_id}/api-keys",
    status_code=status.HTTP_201_CREATED,
    summary="新建 Key",
)
def create_api_key(
    project: Project = Depends(get_own_project),
    db: Session = Depends(get_db),
) -> ApiKeyCreatedOut:
    key, plaintext = api_key_service.create_key(db, project)
    # create_key 是「事务的零件」，提交在调用方做
    db.commit()
    return ApiKeyCreatedOut(
        **ApiKeyOut.model_validate(key).model_dump(),
        api_key=plaintext,
    )


api_key_router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@api_key_router.delete(
    "/{key_id}", status_code=status.HTTP_204_NO_CONTENT, summary="吊销 Key"
)
def revoke_api_key(
    key: ApiKey = Depends(get_own_api_key),
    db: Session = Depends(get_db),
) -> None:
    # 本模块第二个越权入口，且比项目那个隐蔽 —— 路径里没有「项目」这个词提醒你
    # 校验归属。校验在 get_own_api_key 的 join 里，不合规一律 404
    api_key_service.revoke_key(db, key)
