"""数据库引擎、会话工厂，以及请求级的 get_db 依赖。"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    echo=settings.debug,
    # 取连接前先探活：数据库在虚拟机里，连接可能被网络中断或 PG 超时踢掉，
    # 没这个参数就会拿到死连接
    pool_pre_ping=True,
    # 会话时区固定 UTC：不固定的话同一条记录在不同时区的机器上读出来偏移量不同，
    # 接口响应会随环境变
    connect_args={"options": "-c timezone=UTC"},
)

# autoflush=False：不在每次查询前偷偷把未提交的改动刷进库，写库时机由代码显式控制。
# autocommit 是 SQLAlchemy 1.x 的遗留参数，2.x 写了等于没写，别指望它管事务
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        # 出错时显式回滚，是为了解开 PG 的「事务已中止」状态：一条语句报错后
        # 这个事务后续任何 SQL 都会被拒绝，必须先回滚才能继续查
        db.rollback()
        raise
    finally:
        db.close()
