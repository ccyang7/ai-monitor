"""业务异常，让 service 层不必依赖 HTTP。"""


class BusinessError(Exception):
    # 默认 400：格式是合法的（格式错由 Pydantic 挡在前面返回 422），
    # 只是内容不满足业务规则
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
