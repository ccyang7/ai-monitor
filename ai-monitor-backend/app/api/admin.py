"""管理后台接口：内置模型的增删改、用户列表与删除，所有路由都必须过 require_admin。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_builtin_model, require_admin
from app.models import Model, User
from app.schemas.admin import (
    AdminAssistantOut,
    AdminAssistantQuery,
    AdminAssistantSummary,
    AdminAssistantUserRow,
    AdminUserItem,
    AdminUserListOut,
    AdminUserListQuery,
)
from app.schemas.model import (
    AdminUnpricedModelOut,
    ModelCreateIn,
    ModelOut,
    ModelUpdateIn,
)
from app.services import admin as admin_service
from app.services import assistant_chat as assistant_chat_service
from app.services import model as model_service

router = APIRouter(prefix="/admin", tags=["admin"])


# Pydantic 模型必须写成 Annotated[Model, Query()]：写成 Depends() 时
# model_validator 抛的 ValidationError 不被参数校验层捕获，会冒泡成 500
@router.get("/users", summary="用户列表（只读，不含项目内容）")
def list_users(
    query: Annotated[AdminUserListQuery, Query()],
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
) -> AdminUserListOut:
    rows, total = admin_service.list_users_with_activity(db, query)
    return AdminUserListOut(
        items=[
            AdminUserItem(
                id=user.id,
                username=user.username,
                email=user.email,
                role=user.role,
                created_at=user.created_at,
                project_count=project_count,
                last_called_at=last_called_at,
            )
            for user, project_count, last_called_at in rows
        ],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除用户及其全部数据",
)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> None:
    # 目标用户要现查：db.delete 一个不存在的对象不会报错，
    # 不先判 None 的话删一个不存在的 id 会返回 204，前端以为删掉了
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    admin_service.delete_user(db, user, admin)


@router.get("/assistant", summary="AI 助手用量统计（按用户聚合，不含对话内容）")
def list_assistant_usage(
    query: Annotated[AdminAssistantQuery, Query()],
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
) -> AdminAssistantOut:
    rows, total, summary = assistant_chat_service.list_assistant_usage(db, query)
    chats, tokens, errors, users = summary
    return AdminAssistantOut(
        items=[
            AdminAssistantUserRow(
                user_id=user_id,
                username=username,
                chat_count=chat_count,
                total_tokens=int(total_tokens),
                tool_call_count=int(tool_calls),
                avg_latency=int(round(avg_latency)),
                error_count=error_count,
                last_used_at=last_used_at,
            )
            for (
                user_id,
                username,
                chat_count,
                total_tokens,
                tool_calls,
                avg_latency,
                error_count,
                last_used_at,
            ) in rows
        ],
        total=total,
        page=query.page,
        page_size=query.page_size,
        summary=AdminAssistantSummary(
            total_chats=chats,
            total_tokens=int(tokens),
            error_count=errors,
            active_users=users,
        ),
    )


@router.get("/models", summary="内置模型列表（含已禁用）")
def list_builtin_models(
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
) -> list[ModelOut]:
    return [ModelOut.model_validate(m) for m in model_service.list_builtin_models(db)]


# ⚠️ 必须排在 /models/{model_id} 前面：FastAPI 按注册顺序匹配，否则 unpriced
# 会被当成路径参数交给 model_id，得到 422
@router.get("/models/unpriced", summary="全平台未收录模型（按调用量排序）")
def list_global_unpriced_models(
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
) -> list[AdminUnpricedModelOut]:
    return [
        AdminUnpricedModelOut(model_name=name, call_count=calls, user_count=users)
        for name, calls, users in model_service.list_global_unpriced_models(db)
    ]


@router.post(
    "/models",
    status_code=status.HTTP_201_CREATED,
    summary="新增内置模型",
)
def create_builtin_model(
    payload: ModelCreateIn,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
) -> ModelOut:
    return ModelOut.model_validate(model_service.create_builtin_model(db, payload))


@router.patch("/models/{model_id}", summary="修改内置模型")
def update_builtin_model(
    payload: ModelUpdateIn,
    model: Model = Depends(get_builtin_model),
    db: Session = Depends(get_db),
) -> ModelOut:
    return ModelOut.model_validate(
        model_service.update_builtin_model(db, model, payload)
    )


@router.delete(
    "/models/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除内置模型",
)
def delete_builtin_model(
    model: Model = Depends(get_builtin_model),
    db: Session = Depends(get_db),
) -> None:
    model_service.delete_builtin_model(db, model)
