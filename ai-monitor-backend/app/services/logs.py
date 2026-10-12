"""调用日志的查询：筛选条件组装、分页与总数统计。"""

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session, load_only

from app.models import LLMRequest, Project
from app.schemas.log import LogListQuery

# 列表查询只取这些列，input / output 不碰：PG 的大文本存放在 TOAST 表，
# SELECT 列表里不出现该列时连 TOAST 都不会读 —— 列不全就会静默触发逐行补查，
# 所以这份清单必须与 LogListItem 的字段保持一致
_LIST_COLUMNS = (
    LLMRequest.id,
    LLMRequest.model,
    LLMRequest.input_tokens,
    LLMRequest.output_tokens,
    LLMRequest.total_tokens,
    LLMRequest.latency,
    LLMRequest.status,
    LLMRequest.estimated_cost,
    LLMRequest.created_at,
)


def _escape_like(keyword: str) -> str:
    # 三步的顺序不可交换：反斜杠必须第一个换，否则后两步生成的反斜杠会被再转义一遍，
    # 搜「50%」就变成搜「50\%」—— % 反而重新变回通配符
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_conditions(project: Project, query: LogListQuery) -> list[ColumnElement[bool]]:
    # project_id 永远是第一个条件：漏掉它，这个接口就成了读出全系统日志的黑洞
    conditions: list[ColumnElement[bool]] = [LLMRequest.project_id == project.id]

    if query.model:
        conditions.append(LLMRequest.model == query.model)
    if query.status:
        conditions.append(LLMRequest.status == query.status)
    if query.start_date is not None:
        conditions.append(LLMRequest.created_at >= query.start_date)
    if query.end_date is not None:
        # 左闭右开，与 stats 接口同一套语义：前端传「次日 0 点」，后端用 < 正好含满当天。
        # 若改成前端传 23:59:59.999 + 后端 <=，那一毫秒到午夜之间的记录会被漏掉
        conditions.append(LLMRequest.created_at < query.end_date)
    if query.min_tokens is not None:
        conditions.append(LLMRequest.total_tokens >= query.min_tokens)
    if query.max_tokens is not None:
        conditions.append(LLMRequest.total_tokens <= query.max_tokens)

    # 纯空白的搜索词要跳过：ILIKE '%  %' 匹配不到任何东西，却照样全表扫一遍
    keyword = (query.q or "").strip()
    if keyword:
        pattern = f"%{_escape_like(keyword)}%"
        # coalesce 把 NULL 当空串：NULL ILIKE 的结果是 NULL 而非 false，
        # OR 的三值逻辑眼下碰巧给出正确结果，但那是巧合，不是保证
        conditions.append(
            or_(
                func.coalesce(LLMRequest.input, "").ilike(pattern, escape="\\"),
                func.coalesce(LLMRequest.output, "").ilike(pattern, escape="\\"),
                LLMRequest.model.ilike(pattern, escape="\\"),
            )
        )

    return conditions


def list_logs(
    db: Session, project: Project, query: LogListQuery
) -> tuple[list[LLMRequest], int]:
    conditions = _build_conditions(project, query)

    # total 是独立的 COUNT 查询，条件与列表一致（去掉分页）
    total = (
        db.scalar(select(func.count()).select_from(LLMRequest).where(*conditions)) or 0
    )

    rows = list(
        db.scalars(
            select(LLMRequest)
            .options(load_only(*_LIST_COLUMNS))
            .where(*conditions)
            # 第二排序键 id 不能省：同一毫秒的多条记录只按 created_at 排是不稳定序，
            # 翻页时会出现某条记录两页都出现、或两页都不出现
            .order_by(LLMRequest.created_at.desc(), LLMRequest.id.desc())
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
    )
    return rows, total
