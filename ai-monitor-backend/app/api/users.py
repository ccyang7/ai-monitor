"""当前登录用户的信息接口。"""

from fastapi import APIRouter, Depends

from app.deps import get_current_user
from app.models import User
from app.schemas.user import UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", summary="当前用户信息")
def read_me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
