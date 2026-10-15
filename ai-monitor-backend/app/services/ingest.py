"""上报接口的业务逻辑：按当时的模型价格算成本快照，再写入调用日志。"""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import LLMRequest, Model, Project
from app.schemas.log import LogIngestIn

# 价格单位是「元 / 百万 token」，价格表里存的就是官网报价口径，这里只做除法
TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)

# 和 llm_requests.estimated_cost 的 NUMERIC(14, 8) 对齐。
# 不 quantize 的话 Decimal 除法会产生一长串小数，超出列精度时 PG 会直接报错拒写
COST_QUANTUM = Decimal("0.00000001")


def find_priced_model(db: Session, model_name: str, owner_id: int) -> Model | None:
    # 排序规则不能省：同名时用户自建优先于内置 —— 自建的更贴合用户自己的实际情况。
    # PG 里 boolean 排序是 false 在前，所以 asc 出来的第一条正好是自建模型
    return db.scalar(
        select(Model)
        .where(
            Model.model_name == model_name,
            Model.status == "active",
            # 可见性：内置模型人人可见，自建模型只有主人可见。
            # 漏掉 is_builtin 那半段，用户就查不到任何内置价格，成本会全是 null
            or_(Model.is_builtin.is_(True), Model.owner_id == owner_id),
        )
        .order_by(Model.is_builtin.asc())
        .limit(1)
    )


def compute_cost(
    input_tokens: int, cache_hit_tokens: int, output_tokens: int, priced: Model
) -> Decimal:
    # 没配缓存价 → 命中部分按未命中价算，与「没报命中数」殊途同归：
    # 两条降级路径都退回单档公式，老数据的成本逐分钱不变
    hit_price = (
        priced.cache_hit_price
        if priced.cache_hit_price is not None
        else priced.input_price
    )
    miss_tokens = input_tokens - cache_hit_tokens

    cost = (
        Decimal(cache_hit_tokens) / TOKENS_PER_PRICE_UNIT * hit_price
        + Decimal(miss_tokens) / TOKENS_PER_PRICE_UNIT * priced.input_price
        + Decimal(output_tokens) / TOKENS_PER_PRICE_UNIT * priced.output_price
    )
    # 金额四舍五入用 HALF_UP（人熟悉的进位方式），不是 Decimal 默认的 HALF_EVEN
    return cost.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def estimate_cost(db: Session, payload: LogIngestIn, owner_id: int) -> Decimal | None:
    # 查不到价格照常写入、成本留空：数据完整性优先于成本计算。
    # 接入方用了没配价格的新模型，日志不该因此丢掉，补上价格后统计依然可用
    priced = find_priced_model(db, payload.model, owner_id)
    if priced is None:
        return None
    return compute_cost(
        payload.input_tokens, payload.cache_hit_tokens, payload.output_tokens, priced
    )


def ingest_log(db: Session, project: Project, payload: LogIngestIn) -> LLMRequest:
    record = LLMRequest(
        project_id=project.id,
        provider=payload.provider,
        model=payload.model,
        input=payload.input,
        output=payload.output,
        input_tokens=payload.input_tokens,
        cache_hit_tokens=payload.cache_hit_tokens,
        output_tokens=payload.output_tokens,
        # 接入方没传 total 时自己补：漏传不该在统计里留下一批 0 token 的调用
        total_tokens=(
            payload.total_tokens
            if payload.total_tokens is not None
            else payload.input_tokens + payload.output_tokens
        ),
        latency=payload.latency,
        status=payload.status,
        error_message=payload.error,
        estimated_cost=estimate_cost(db, payload, project.owner_id),
    )

    if payload.timestamp is not None:
        record.created_at = payload.timestamp

    db.add(record)
    db.commit()
    db.refresh(record)
    return record
