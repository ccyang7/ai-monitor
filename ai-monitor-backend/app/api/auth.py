"""注册与登录接口，成功后签发 JWT。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.schemas.auth import LoginIn, RegisterIn, TokenOut
from app.schemas.user import UserOut
from app.security import create_access_token
from app.services import auth as auth_service

# 路由函数一律用 def 不用 async def：数据库会话是同步的，在 async def 里做同步 IO
# 会阻塞事件循环，所有并发请求一起卡住；用 def 时 FastAPI 会丢到线程池执行
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED, summary="注册")
def register(payload: RegisterIn, db: Session = Depends(get_db)) -> UserOut:
    user = auth_service.register(db, payload.username, payload.password, payload.email)
    return UserOut.model_validate(user)


@router.post("/login", summary="登录")
def login(payload: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    user = auth_service.authenticate(db, payload.username, payload.password)
    return TokenOut(
        access_token=create_access_token(user.id),
        expires_in=settings.access_token_expire_minutes * 60,
    )
