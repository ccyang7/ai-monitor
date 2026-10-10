"""注册与登录校验。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.exceptions import BusinessError
from app.models import User
from app.security import hash_password, password_within_limit, verify_password

# 用户不存在时也拿它跑一次比对：不跑的话响应会快几十毫秒，
# 攻击者用时间差就能测出系统里有哪些用户名。模块加载时算一次
_DUMMY_HASH = hash_password("__never_matches__")


def register(db: Session, username: str, password: str, email: str | None) -> User:
    # 先查一次是为了给出明确提示；但先查再插有竞态，所以下面还要捕 IntegrityError ——
    # 数据库的 UNIQUE 约束才是唯一说了算的那道防线
    if db.scalar(select(User.id).where(User.username == username)):
        raise BusinessError("用户名已存在")

    user = User(
        username=username,
        password_hash=hash_password(password),
        email=email,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # 必须先回滚：PG 在语句出错后事务就中止了，不回滚后续任何 SQL 都会被拒绝
        db.rollback()
        raise BusinessError("用户名或邮箱已被占用") from None

    db.refresh(user)
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.scalar(select(User).where(User.username == username))

    if user is None:
        verify_password("__dummy__", _DUMMY_HASH)
        raise BusinessError("用户名或密码错误", status_code=401)

    # 超长密码要先挡：bcrypt 遇到超过 72 字节的输入会抛 ValueError，这里是 500 不是 401
    if not password_within_limit(password):
        raise BusinessError("用户名或密码错误", status_code=401)

    if not verify_password(password, user.password_hash):
        raise BusinessError("用户名或密码错误", status_code=401)

    return user
