"""用户隔离的依赖链：从 JWT 取当前用户，再校验项目与 API Key 的归属。"""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiKey, Project, User
from app.security import decode_access_token

# auto_error=False：没带 token 时自己抛，好统一错误信息风格。
# 用 HTTPBearer 而非 OAuth2PasswordBearer：后者要求登录接口收 OAuth2 表单格式，而我们是 JSON
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未登录或登录已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    try:
        user_id = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:
        # InvalidTokenError 是所有解码异常的基类，逐个列反而容易漏
        raise unauthorized from None

    user = db.get(User, user_id)
    if user is None:
        raise unauthorized

    return user


def get_own_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    # 形参名必须和路由里的 {project_id} 一字不差，否则 FastAPI 会当成查询参数，
    # 症状是「莫名其妙的 422」
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.owner_id == user.id)
    )
    if project is None:
        # 不存在和不属于当前用户走同一条路径、给同一个错误信息：
        # 403 等于告诉攻击者「这个 ID 存在」，他就能把全系统的 ID 枚举出来
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return project


def get_own_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ApiKey:
    # 不能省：DELETE /api-keys/{key_id} 的路径里没有 project_id，用不上
    # get_own_project，但同样是越权入口 —— 拿到别人的 Key 等于拿到别人项目的数据写入权。
    # 归属条件写进 join 而不是「先查 Key 再查它的项目是谁」，少一步就少一次忘掉的机会
    key = db.scalar(
        select(ApiKey)
        .join(Project)
        .where(ApiKey.id == key_id, Project.owner_id == user.id)
    )
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API Key 不存在"
        )
    return key
