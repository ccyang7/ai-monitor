"""模型的增删改查：列表带「在用的项目」聚合与「未配价模型」提示，删除实际执行禁用。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, get_own_model, get_visible_model
from app.models import Model, User
from app.schemas.model import (
    ModelCreateIn,
    ModelListOut,
    ModelOut,
    ModelUpdateIn,
    UnpricedModelOut,
)
from app.services import model as model_service

router = APIRouter(prefix="/models", tags=["models"])


def _to_out(model: Model, used_by_projects: list[str]) -> ModelOut:
    # 显式构造而不是 model_validate：used_by_projects 不在 ORM 对象上，
    # 是 service 聚合出来的「附赠」数据
    return ModelOut(
        id=model.id,
        provider=model.provider,
        model_name=model.model_name,
        input_price=model.input_price,
        output_price=model.output_price,
        cache_hit_price=model.cache_hit_price,
        is_builtin=model.is_builtin,
        status=model.status,
        used_by_projects=used_by_projects,
    )


# 路径写 "" 而不是 "/"：后者生成 /models/，访问 /models 会吃一个 307 重定向（同 projects.py）
@router.get("", summary="模型列表")
def list_models(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ModelListOut:
    rows = model_service.list_models(db, user)
    unpriced = model_service.list_unpriced_models(db, user)
    return ModelListOut(
        items=[_to_out(model, used) for model, used in rows],
        total=len(rows),
        # 未配价的模型没有行可挂，只能单独列一块 —— 页面据此显示补价提示
        unpriced_models=[
            UnpricedModelOut(model_name=name, used_by_projects=projects)
            for name, projects in unpriced
        ],
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="新增模型")
def create_model(
    payload: ModelCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ModelOut:
    model = model_service.create_model(db, user, payload)
    # 新建的模型不可能有历史调用，不必再查一次聚合
    return _to_out(model, [])


@router.get("/{model_id}", summary="模型详情")
def get_model(
    model: Model = Depends(get_visible_model),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ModelOut:
    return _to_out(model, model_service.list_used_projects(db, user, model.model_name))


@router.patch("/{model_id}", summary="修改模型（仅自建）")
def update_model(
    payload: ModelUpdateIn,
    model: Model = Depends(get_own_model),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ModelOut:
    updated = model_service.update_model(db, model, payload)
    return _to_out(
        updated, model_service.list_used_projects(db, user, updated.model_name)
    )


@router.delete(
    "/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除模型（实际执行禁用）",
)
def disable_model(
    model: Model = Depends(get_own_model),
    db: Session = Depends(get_db),
) -> None:
    model_service.disable_model(db, model)
