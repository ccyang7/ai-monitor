"""项目的查询、创建、删除，以及配额与重名校验。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.exceptions import BusinessError
from app.models import LLMRequest, Project, User
from app.schemas.project import ProjectCreateIn, ProjectUpdateIn
from app.services import api_key as api_key_service


def list_projects(db: Session, user: User) -> list[tuple[Project, datetime | None]]:
    # owner_id 过滤绝不能省 —— 漏掉就是列出全系统所有项目
    projects = list(
        db.scalars(
            select(Project)
            .where(Project.owner_id == user.id)
            .order_by(Project.created_at.desc())
        )
    )
    if not projects:
        return []

    # 一次查回所有项目的最近调用时间，而不是逐个项目查一遍（N+1）：
    # 首页项目不多时开销也看不出来，但没有理由留下这种模式
    last_called_map = dict(
        db.execute(
            select(LLMRequest.project_id, func.max(LLMRequest.created_at))
            .where(LLMRequest.project_id.in_([p.id for p in projects]))
            .group_by(LLMRequest.project_id)
        ).all()
    )
    return [(p, last_called_map.get(p.id)) for p in projects]


def create_project(
    db: Session, user: User, data: ProjectCreateIn
) -> tuple[Project, str]:
    # 只数 active 的：需求说的是「最多 3 个活跃项目」，将来归档的项目不该继续占配额
    active_count = db.scalar(
        select(func.count())
        .select_from(Project)
        .where(Project.owner_id == user.id, Project.status == "active")
    )
    if active_count >= settings.max_projects_per_user:
        raise BusinessError(
            f"项目数量已达上限（{settings.max_projects_per_user} 个），"
            "请先删除不再使用的项目"
        )

    # 数据库没给 name 加唯一索引（为了以后做软删除时还能重建同名项目），
    # 所以这道校验只能放在应用层
    duplicated = db.scalar(
        select(Project.id).where(
            Project.owner_id == user.id, Project.name == data.name
        )
    )
    if duplicated:
        raise BusinessError("同名项目已存在")

    # 项目 + 首把 Key 必须同一个事务，全程只 commit 一次：写成两次 commit 的话，
    # 第二次失败会留下一个没有 Key 的孤儿项目，用户拿着项目却上报不了数据
    project = Project(
        owner_id=user.id,
        name=data.name,
        description=data.description,
        tech_stack=data.tech_stack,
    )
    db.add(project)
    db.flush()

    _, plaintext = api_key_service.create_key(db, project)

    db.commit()
    db.refresh(project)
    return project, plaintext


def update_project(db: Session, project: Project, data: ProjectUpdateIn) -> Project:
    # exclude_unset 是这个函数的关键：description「没传」和「传 null」在 model_dump()
    # 的结果里长得一模一样，只有它分得清「别动它」和「清空它」
    changes = data.model_dump(exclude_unset=True)

    if "name" in changes:
        # 必须排除自己 —— 打开编辑框什么都不改直接保存，否则会撞上自己当前的名字。
        # 也不要退化成「名字没变就跳过查重」：name 在数据库层没有唯一索引（刻意的，
        # 为了以后做软删除时能重建同名项目），万一真存在重名数据，那种写法会静默放过
        duplicated = db.scalar(
            select(Project.id).where(
                Project.owner_id == project.owner_id,
                Project.name == changes["name"],
                Project.id != project.id,
            )
        )
        if duplicated:
            raise BusinessError("同名项目已存在")

    # 逐字段显式赋值，不用 setattr 循环：setattr 是「schema 里有什么就改什么」，
    # 将来谁往 ProjectUpdateIn 里加个不该改的字段（比如 status），它会安静地改掉
    if "name" in changes:
        project.name = changes["name"]
    if "description" in changes:
        project.description = changes["description"]
    if "tech_stack" in changes:
        project.tech_stack = changes["tech_stack"]

    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project: Project) -> None:
    # 项目下的 Key 由 ON DELETE CASCADE 一起带走。模型里配了 passive_deletes，
    # SQLAlchemy 才不会先 SELECT 出所有 Key 再逐条 DELETE
    db.delete(project)
    db.commit()
