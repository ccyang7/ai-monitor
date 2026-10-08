"""AI Monitor 后端 —— 环境验证入口。

这个文件目前只有一个作用：验证 Python + FastAPI + uvicorn 环境是否跑得通。

正式的 FastAPI 应用结构（路由分层、配置管理、依赖注入、数据库连接）
将在阶段 2 中由你自己搭建 —— 所以这里刻意保持最小。
"""

from fastapi import FastAPI

app = FastAPI(title="AI Monitor API")


@app.get("/health")
def health():
    """健康检查：能返回 {"status": "ok"} 就说明环境正常。"""
    return {"status": "ok"}
