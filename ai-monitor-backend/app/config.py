"""应用配置：从环境变量和 .env 读取，并守卫 JWT 密钥强度。"""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 的密钥短于哈希输出（32 字节）时签名空间变小，RFC 7518 §3.2 建议 32 字节起
MIN_JWT_SECRET_BYTES = 32

PLACEHOLDER_SECRETS = {"change_me", "CHANGE_ME_RANDOM_SECRET"}


class Settings(BaseSettings):
    app_name: str = "AI Monitor API"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # +psycopg 指定 psycopg3 驱动；psycopg2 要写 +psycopg2，两者不通用
    database_url: str = "postgresql+psycopg://ai_monitor:change_me@localhost:5432/ai_monitor"

    jwt_secret_key: str = "change_me"
    jwt_algorithm: str = "HS256"

    access_token_expire_minutes: int = 7 * 24 * 60

    max_projects_per_user: int = 3

    # .env 按「启动目录」找，不是按本文件位置 —— 约定从 ai-monitor-backend/ 启动服务
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def _guard_jwt_secret(self) -> "Settings":
        # 占位符写在 .env.example 里、随仓库公开，不换掉等于把签名密钥告诉了所有人。
        # 这个错本地开发毫无感觉，等别人签出一个合法 token 就晚了，所以非 debug 直接拒启
        weak = (
            self.jwt_secret_key in PLACEHOLDER_SECRETS
            or len(self.jwt_secret_key.encode("utf-8")) < MIN_JWT_SECRET_BYTES
        )
        if not weak:
            return self

        if self.jwt_secret_key in PLACEHOLDER_SECRETS:
            reason = "还是占位符"
        else:
            size = len(self.jwt_secret_key.encode("utf-8"))
            reason = f"只有 {size} 字节，建议至少 {MIN_JWT_SECRET_BYTES}"

        message = (
            f"JWT_SECRET_KEY 不安全（{reason}）。\n"
            "  它用来签名登录凭证，泄露 = 任何人都能伪造任意用户的 token。\n"
            "  生成一个：python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
            "  然后填进 ai-monitor-backend/.env"
        )

        if self.debug:
            self._warn(message)
            return self

        raise ValueError(message)

    @staticmethod
    def _warn(message: str) -> None:
        # 中文 Windows 控制台是 GBK，emoji 会让 print() 抛 UnicodeEncodeError，
        # 被 pydantic 包成 ValidationError —— 一句本意是提醒的警告能把应用搞得启动不了
        text = f"\n[警告] {message}\n"
        try:
            print(text)
        except UnicodeEncodeError:
            print(
                "\n[WARNING] JWT_SECRET_KEY is weak or still a placeholder.\n"
                "  Generate one: python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
                "  Then put it in ai-monitor-backend/.env\n"
            )


settings = Settings()
