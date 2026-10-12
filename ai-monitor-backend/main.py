"""FastAPI 应用装配：创建 app、挂载路由、注册全局异常处理。"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import auth, health, logs, projects, stats, users
from app.config import settings
from app.exceptions import BusinessError

app = FastAPI(title=settings.app_name)

app.include_router(health.router)
app.include_router(auth.router, prefix=settings.api_v1_prefix)
app.include_router(users.router, prefix=settings.api_v1_prefix)
app.include_router(projects.router, prefix=settings.api_v1_prefix)
# 吊销 Key 的路径是 /api-keys/{key_id}，不带 /projects 前缀，所以是另一个 router
app.include_router(projects.api_key_router, prefix=settings.api_v1_prefix)
app.include_router(logs.router, prefix=settings.api_v1_prefix)
# 日志列表的路径是 /projects/{project_id}/logs，前缀是 /projects 而不是 /logs
app.include_router(logs.project_logs_router, prefix=settings.api_v1_prefix)
# 统计接口同理，路径是 /projects/{project_id}/stats/...
app.include_router(stats.router, prefix=settings.api_v1_prefix)


@app.exception_handler(BusinessError)
def handle_business_error(request: Request, exc: BusinessError) -> JSONResponse:
    # service 层不感知 HTTP，不抛 HTTPException，所以需要这个翻译官。
    # 响应体统一成 {"detail": ...}，和 FastAPI 原生错误格式一致，前端只写一套解析
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
