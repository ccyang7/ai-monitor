"""管理后台的跨用户操作：用户列表、每个用户的项目数与最近调用时间，以及级联删除用户。"""

from datetime import datetime

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, load_only

from app.exceptions import BusinessError
from app.models import LLMRequest, Project, User
from app.schemas.admin import AdminUserListQuery
from app.services.logs import escape_like


def list_users_with_activity(
    db: Session, query: AdminUserListQuery
) -> tuple[list[tuple[User, int, datetime | None]], int]:
    conditions = []
    keyword = (query.q or "").strip()
    if keyword:
        pattern = f"%{escape_like(keyword)}%"
        conditions.append(User.username.ilike(pattern, escape="\\"))

    total = db.scalar(select(func.count()).select_from(User).where(*conditions)) or 0

    # load_only 把 password_hash 挡在 SELECT 之外：它本就不该出现在这个接口的任何一层，
    # 一并省掉读取它
    users = list(
        db.scalars(
            select(User)
            .options(
                load_only(
                    User.id,
                    User.username,
                    User.email,
                    User.role,
                    User.created_at,
                )
            )
            .where(*conditions)
            # 第二排序键 id 与日志列表同理：同一时刻注册的多个用户，
            # 只按 created_at 排会得到不稳定序，翻页时可能重复或漏人
            .order_by(User.created_at.desc(), User.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
    )
    if not users:
        return [], total

    # 一条聚合查询覆盖当前页所有用户，不逐个用户查（N+1）。
    # count(distinct) 不能写成 count(*)：outer join 之后一个项目会按日志条数膨胀
    activity = {
        owner_id: (project_count, last_called_at)
        for owner_id, project_count, last_called_at in db.execute(
            select(
                Project.owner_id,
                func.count(distinct(Project.id)),
                func.max(LLMRequest.created_at),
            )
            .join(LLMRequest, LLMRequest.project_id == Project.id, isouter=True)
            .where(Project.owner_id.in_([u.id for u in users]))
            .group_by(Project.owner_id)
        ).all()
    }

    rows = []
    for user in users:
        project_count, last_called_at = activity.get(user.id, (0, None))
        rows.append((user, project_count, last_called_at))
    return rows, total


def delete_user(db: Session, user: User, operator: User) -> None:
    # 「不能删自己」是唯一需要的人工护栏：删除动作本身就证明操作者还在，
    # 所以删别人永远不会把管理员删光，不必再单独数一次「还剩几个管理员」。
    # 反过来，允许自删就等于允许把系统删到无人可管
    if user.id == operator.id:
        raise BusinessError("不能删除自己的账号")

    # 项目、API Key、调用日志、异常标准、助手用量、自建模型都在外键上配了
    # ON DELETE CASCADE，一条 DELETE 就能全部带走；User.projects 是 passive_deletes，
    # ORM 不会先把项目全加载进内存
    db.delete(user)
    db.commit()
