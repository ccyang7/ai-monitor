"""健康检查接口，供部署探活与回归用。"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health", summary="健康检查")
def health() -> dict[str, str]:
    return {"status": "ok"}
