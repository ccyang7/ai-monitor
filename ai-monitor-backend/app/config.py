"""应用配置：从环境变量和 .env 读取，并守卫 JWT 密钥强度。"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator, model_validator
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

    # 异常判定阈值（设计 05 §2.1）。做成配置项而不是常量：不同项目对「异常」的容忍度
    # 不同，改阈值不该重新部署。latency 的倍数比 token 低，是因为延迟的日常波动幅度小，
    # 等到 5 倍才报的话，那时候早就慢得离谱了
    token_anomaly_ratio: float = 5.0
    latency_anomaly_ratio: float = 3.0
    error_rate_threshold: float = 0.2
    # 样本少于这个数就不判定 —— 两条历史记录算出来的「平均值」谁信谁误报
    baseline_min_samples: int = 10

    # AI 分析助手（阶段 11-12）。这一组刻意不设启动守卫，与 JWT_SECRET_KEY 相反而为：
    # 登录是核心链路，密钥弱就该拒启；助手是增值功能，没配 Key 只影响它自己
    # （聊天接口返回 503），不该拖着整个平台起不来
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-flash"
    llm_temperature: float = 0.3
    assistant_timezone: str = "Asia/Shanghai"
    # 一次问答通常 2~5 轮工具调用。设上限是防失控：模型反复调工具不给答案时，
    # 一次坏请求能长时间占着连接烧 Token
    assistant_max_tool_rounds: int = 10

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

    @field_validator("assistant_timezone")
    @classmethod
    def _check_assistant_timezone(cls, value: str) -> str:
        # 时区名写错的话，工具的每个日期换算都会在运行时抛异常（每条请求 500）。
        # 挪到启动时一次性拒掉，错在哪儿一目了然
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"assistant_timezone 不是合法的时区名：{value}") from None
        return value

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
