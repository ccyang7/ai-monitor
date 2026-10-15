"""模型的查询与增删改：可见性过滤（内置 + 自建）、未配价差集、重名校验、禁用。"""

from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.exceptions import BusinessError
from app.models import LLMRequest, Model, Project, User
from app.schemas.model import ModelCreateIn, ModelUpdateIn


def list_models(db: Session, user: User) -> list[tuple[Model, list[str]]]:
    # 可见性：内置模型人人可见，自建模型只有主人可见 —— 漏掉 is_builtin 那半段，
    # 用户就看不到任何内置模型
    models = list(
        db.scalars(
            select(Model)
            .where(
                Model.status == "active",
                or_(Model.is_builtin.is_(True), Model.owner_id == user.id),
            )
            # 内置在前（系统的基准价），组内按名称排。不写 ORDER BY 的话行的顺序
            # 全看 PG 的心情，前端表格每次刷新都可能换一个样
            .order_by(Model.is_builtin.desc(), Model.model_name.asc())
        )
    )
    if not models:
        return []

    usage = _used_by_projects(db, user)
    return [(m, usage.get(m.model_name, [])) for m in models]


def list_used_projects(db: Session, user: User, model_name: str) -> list[str]:
    return _used_by_projects(db, user).get(model_name, [])


def list_unpriced_models(db: Session, user: User) -> list[tuple[str, list[str]]]:
    # 口径是与「当前可见的 active 模型名」做差集，不是 estimated_cost IS NULL ——
    # 那是历史快照：补价之后老日志的成本照样是 NULL，提示会永远消不掉。
    # 差集与 ingest.find_priced_model 同一套匹配规则（只比 model_name，不看 provider）
    usage = _used_by_projects(db, user)
    visible_names = set(
        db.scalars(
            select(Model.model_name).where(
                Model.status == "active",
                or_(Model.is_builtin.is_(True), Model.owner_id == user.id),
            )
        )
    )
    return [
        (name, projects)
        for name, projects in sorted(usage.items())
        if name not in visible_names
    ]


def _used_by_projects(db: Session, user: User) -> dict[str, list[str]]:
    # 一条聚合查询覆盖所有模型，而不是逐个模型查一遍（N+1 的教训同 list_projects）。
    # 匹配用 llm_requests.model ↔ models.model_name 字符串 —— 与成本匹配同一套口径
    rows = db.execute(
        select(LLMRequest.model, Project.name)
        .join(Project, Project.id == LLMRequest.project_id)
        # owner_id 过滤不能漏 —— 不加就是跨用户泄露「别人在用哪些模型」
        .where(Project.owner_id == user.id)
        .group_by(LLMRequest.model, Project.name)
        .order_by(LLMRequest.model, Project.name)
    ).all()

    usage: dict[str, list[str]] = defaultdict(list)
    for model_name, project_name in rows:
        usage[model_name].append(project_name)
    return usage


def create_model(db: Session, user: User, data: ModelCreateIn) -> Model:
    _ensure_name_available(db, user.id, data.model_name)

    model = Model(
        owner_id=user.id,
        provider=data.provider,
        model_name=data.model_name,
        input_price=data.input_price,
        output_price=data.output_price,
        cache_hit_price=data.cache_hit_price,
        is_builtin=False,
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


def update_model(db: Session, model: Model, data: ModelUpdateIn) -> Model:
    # exclude_unset 分得清「没传」和「传 null」：cache_hit_price 传 null 是
    # 「清空缓存档」，不是「别动它」
    changes = data.model_dump(exclude_unset=True)

    if "model_name" in changes:
        _ensure_name_available(
            db, model.owner_id, changes["model_name"], exclude_id=model.id
        )

    # 逐字段显式赋值，不用 setattr 循环 —— 理由同 project.py：将来给 UpdateIn 加个
    # 不该改的字段，循环会安静地改掉
    if "provider" in changes:
        model.provider = changes["provider"]
    if "model_name" in changes:
        model.model_name = changes["model_name"]
    if "input_price" in changes:
        model.input_price = changes["input_price"]
    if "output_price" in changes:
        model.output_price = changes["output_price"]
    if "cache_hit_price" in changes:
        model.cache_hit_price = changes["cache_hit_price"]

    db.commit()
    db.refresh(model)
    return model


def disable_model(db: Session, model: Model) -> None:
    # DELETE 实际执行禁用而不是物理删除：模型价格是历史的一部分，删掉之后
    # 「这个模型明明用过，怎么列表里没有了」就没法解释 —— 禁用既让它不再匹配
    # 新上报，又留下了可追溯的记录
    model.status = "disabled"
    db.commit()


def _ensure_name_available(
    db: Session, owner_id: int, model_name: str, exclude_id: int | None = None
) -> None:
    # 只在自己的自建模型里查重：与内置模型同名是合法的 —— 那正是
    # 「改内置价走副本」的正道，同名才能让上报匹配到自己这份价格。
    # 内置模型的 owner_id 是 NULL，== owner_id 天然把它排除在外；
    # 别人的自建同理，各人一份副本互不干扰
    conditions = [
        Model.status == "active",
        Model.model_name == model_name,
        Model.owner_id == owner_id,
    ]
    if exclude_id is not None:
        # 排除自己：打开编辑框什么都不改直接保存，否则会撞上自己当前的名字
        conditions.append(Model.id != exclude_id)

    duplicated = db.scalar(select(Model.id).where(*conditions))
    if duplicated:
        raise BusinessError("你已有一个同名模型，请换个名字或直接改它的价格")
