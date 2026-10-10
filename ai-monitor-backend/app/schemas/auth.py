"""注册、登录与 token 的请求、响应模型。"""

from pydantic import BaseModel, Field, field_validator

from app.security import MAX_PASSWORD_BYTES


class RegisterIn(BaseModel):
    # max_length 必须和数据库 VARCHAR 对齐，否则超长值会走到数据库才被拒，返回 500 而不是 422
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6)
    email: str | None = Field(default=None, max_length=255)

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str) -> str:
        # 上限是【字节】不是字符（一个汉字 3 字节）。不在这里挡，超长密码会走到
        # bcrypt 抛 ValueError，变成 500 而不是 422
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(
                f"密码过长：UTF-8 编码后不能超过 {MAX_PASSWORD_BYTES} 字节"
                "（一个汉字算 3 字节）"
            )
        return value

    @field_validator("email")
    @classmethod
    def empty_email_to_none(cls, value: str | None) -> str | None:
        # NULL 之间不冲突，但两个空字符串会撞唯一约束
        return value or None


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
