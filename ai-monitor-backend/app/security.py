"""密码哈希、JWT 编解码、API Key 随机串 —— 纯函数，不碰数据库。"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

# bcrypt 超过 72 字节直接抛 ValueError（是报错不是截断），且单位是字节：
# 一个汉字 3 字节，24 个汉字就超了
MAX_PASSWORD_BYTES = 72

API_KEY_PREFIX_LENGTH = 12


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def password_within_limit(plain: str) -> bool:
    # 登录路径也要挡：bcrypt 对超长输入抛 ValueError，不挡就是 500 而不是 401。
    # 不要改成截断「兼容」—— 那会让前 72 字节相同的两个密码变成同一个
    return len(plain.encode("utf-8")) <= MAX_PASSWORD_BYTES


def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        # 必须是字符串：PyJWT 2.10 起解码时强校验 sub 的类型，
        # 塞 int 进来编码不报错，等解码那一刻才抛 InvalidSubjectError
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int:
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        # 只认配置里写死的算法，绝不能让 token 自己声明用哪个算法签的 ——
        # 那正是算法混淆攻击的入口
        algorithms=[settings.jwt_algorithm],
    )
    return int(payload["sub"])


def generate_api_key() -> str:
    # 用 secrets 不用 random：random 是可预测的伪随机，等于把密码设成「123456」
    return "proj_" + secrets.token_urlsafe(32)


def hash_api_key(plaintext: str) -> str:
    # 和密码【故意】相反：Key 是 32 字节随机串（256 位熵），慢哈希换不来安全性，
    # 而每次上报都要验一次 —— bcrypt 的 0.2 秒/次会让上报接口不可用。
    # 熵足够高，也不需要加盐
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
