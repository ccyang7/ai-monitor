"""API Key 的生成、列表与吊销。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApiKey, Project
from app.security import API_KEY_PREFIX_LENGTH, generate_api_key, hash_api_key


def create_key(db: Session, project: Project) -> tuple[ApiKey, str]:
    # 本模块唯一【不提交】的函数：它是「事务的零件」而不是完整动作 ——
    # 创建项目时这把 Key 必须和项目共用同一个事务、出错一起回滚，提交时机由调用方定
    plaintext = generate_api_key()

    key = ApiKey(
        project_id=project.id,
        key_hash=hash_api_key(plaintext),
        key_prefix=plaintext[:API_KEY_PREFIX_LENGTH],
    )
    db.add(key)
    db.flush()
    return key, plaintext


def list_keys(db: Session, project: Project) -> list[ApiKey]:
    # 只列 active：已吊销的只是留个记录，列出来只会让用户分不清哪把还能用
    return list(
        db.scalars(
            select(ApiKey)
            .where(ApiKey.project_id == project.id, ApiKey.status == "active")
            .order_by(ApiKey.created_at.desc())
        )
    )


def revoke_key(db: Session, key: ApiKey) -> None:
    # 软吊销不物理删除：阶段 4 的上报接口要靠 status 判断「这把 Key 已失效」并返回 401，
    # 删了记录，一把已泄露的 Key 就无从追溯
    key.status = "revoked"
    db.commit()
