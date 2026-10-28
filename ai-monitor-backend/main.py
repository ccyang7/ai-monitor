"""FastAPI 应用装配：创建 app、挂载路由、注册全局异常处理。"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import admin, anomaly, auth, chat, health, logs, models, projects, stats, users
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
# 异常列表也是 /projects/{project_id}/anomalies
app.include_router(anomaly.router, prefix=settings.api_v1_prefix)
# 模型管理与项目无关：可见范围是「内置 ∪ 自己的自建」，所以路由不带项目前缀
app.include_router(models.router, prefix=settings.api_v1_prefix)
# AI 助手是全局面板，路径不带项目：权限边界靠 AssistantContext 里的 user_id
app.include_router(chat.router, prefix=settings.api_v1_prefix)
# 管理后台的跨用户能力只从这个前缀进，用户侧路由一行不改
app.include_router(admin.router, prefix=settings.api_v1_prefix)


@app.exception_handler(BusinessError)
def handle_business_error(request: Request, exc: BusinessError) -> JSONResponse:
    # service 层不感知 HTTP，不抛 HTTPException，所以需要这个翻译官。
    # 响应体统一成 {"detail": ...}，和 FastAPI 原生错误格式一致，前端只写一套解析
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
