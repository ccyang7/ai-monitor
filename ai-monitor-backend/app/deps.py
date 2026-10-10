"""用户隔离的依赖链：从 JWT 取当前用户，验证项目与 API Key 的归属；上报接口走 X-API-Key。"""

from datetime import datetime, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiKey, LLMRequest, Project, User
from app.security import decode_access_token, hash_api_key

# auto_error=False：没带 token 时自己抛，好统一错误信息风格。
# 用 HTTPBearer 而非 OAuth2PasswordBearer：后者要求登录接口收 OAuth2 表单格式，而我们是 JSON
_bearer_scheme = HTTPBearer(auto_error=False)

# auto_error 同样必须关掉：它默认为 True 时，请求缺这个头会返回 403，
# 而设计文档和接入方都按 401 处理 —— 「没带凭证」和「没权限」是两回事
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


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


def get_own_log(
    log_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LLMRequest:
    # 与 get_own_api_key 同一个模式：详情接口的路径是 /logs/{log_id}，没有 project_id
    # 可用，get_own_project 帮不上忙 —— 而日志同样是越权入口，归属校验必须显式写在 join 里
    log = db.scalar(
        select(LLMRequest)
        .join(Project)
        .where(LLMRequest.id == log_id, Project.owner_id == user.id)
    )
    if log is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="日志不存在")
    return log


def get_api_key_project(
    api_key: str | None = Depends(_api_key_header),
    db: Session = Depends(get_db),
) -> Project:
    """上报接口的认证：从 X-API-Key 头识别项目。返回的是项目，不是用户。"""
    # 缺头、Key 不存在、已吊销、已过期，全部返回同一个 401 —— 区分开来等于
    # 告诉攻击者「你猜的这把 Key 存在，只是吊销了」，能用来探测 Key 的有效性
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="API Key 无效或已失效",
    )

    if not api_key:
        raise unauthorized

    # 查的是哈希：库表里根本没存明文，明文只在创建那一刻返回过一次
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key)))
    if key is None or key.status != "active":
        raise unauthorized

    if key.expired_at is not None and key.expired_at <= datetime.now(timezone.utc):
        raise unauthorized

    project = db.get(Project, key.project_id)
    if project is None:
        # 外键 CASCADE 下正常不会走到这里，兜底防住「Key 在、项目被删了」的并发窗口
        raise unauthorized

    return project
