"""同步内置模型价格（幂等），上报接口算成本依赖这份数据。"""

import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import (  # noqa: E402
    OperationalError,
    ProgrammingError,
    SQLAlchemyError,
)

from app.database import SessionLocal  # noqa: E402
from app.models import Model  # noqa: E402


# 格式：(provider, model_name, input_price, output_price, cache_hit_price)
# 价格单位：元 / 百万 Token。
# None 表示没有单独配置缓存命中价格，不代表价格为 0。
BUILTIN_MODELS = [
    ("deepseek", "deepseek-chat", "1.000000", "2.000000", None),
    ("deepseek", "deepseek-reasoner", "4.000000", "16.000000", None),
    ("deepseek", "deepseek-v4-flash", "1.000000", "2.000000", "0.020000"),
    ("openai", "gpt-4o", "18.000000", "72.000000", "9.000000"),
    ("openai", "gpt-4o-mini", "1.100000", "4.400000", "0.550000"),
    ("siliconflow", "BAAI/bge-m3", "0.000000", "0.000000", None),
]


def prepare_models():
    prepared = []
    seen = set()

    for provider, name, input_price, output_price, cache_price in BUILTIN_MODELS:
        # 数据库的内置模型唯一索引只约束 model_name，去重口径必须和它一致
        if name in seen:
            raise ValueError(f"内置模型配置重复：{name}")
        seen.add(name)

        try:
            prices = (
                Decimal(input_price),
                Decimal(output_price),
                Decimal(cache_price) if cache_price is not None else None,
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"模型价格格式错误：{provider}/{name}") from exc

        for price in prices:
            if price is not None and (
                not price.is_finite() or price < 0
            ):
                raise ValueError(f"模型价格必须是有限的非负数：{provider}/{name}")

        prepared.append((provider, name, *prices))

    return prepared


def sync_models() -> tuple[int, int, int]:
    created = 0
    updated = 0
    unchanged = 0
    models = prepare_models()

    with SessionLocal() as db:
        try:
            for provider, name, input_price, output_price, cache_price in models:
                # provider 只是描述字段，上报计价也只按 model_name 匹配；
                # 把它放进查找条件会导致厂商名一改就撞 unique index
                statement = select(Model).where(
                    Model.model_name == name,
                    Model.is_builtin.is_(True),
                )
                matches = db.scalars(statement).all()

                # 不擅自删除或覆盖重复记录，避免误伤业务数据。
                if len(matches) > 1:
                    raise RuntimeError(
                        f"数据库存在重复的内置模型：{name}，"
                        "请先检查并清理重复记录。"
                    )

                if not matches:
                    db.add(
                        Model(
                            provider=provider,
                            model_name=name,
                            input_price=input_price,
                            output_price=output_price,
                            cache_hit_price=cache_price,
                            is_builtin=True,
                        )
                    )
                    created += 1
                    continue

                model = matches[0]
                new_values = (
                    provider,
                    input_price,
                    output_price,
                    cache_price,
                )
                old_values = (
                    model.provider,
                    model.input_price,
                    model.output_price,
                    model.cache_hit_price,
                )

                if old_values == new_values:
                    unchanged += 1
                    continue

                model.provider = provider
                model.input_price = input_price
                model.output_price = output_price
                model.cache_hit_price = cache_price
                updated += 1

            # 所有模型统一提交，避免只同步成功一部分。
            db.commit()

        except Exception:
            db.rollback()
            raise

    return created, updated, unchanged


def main() -> int:
    created, updated, unchanged = sync_models()

    print(
        f"内置模型同步完成：新增 {created} 个，"
        f"更新 {updated} 个，未变更 {unchanged} 个。"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OperationalError as exc:
        print("数据库连接失败，请检查 DATABASE_URL 和数据库状态。", file=sys.stderr)
        print(f"详细错误：{exc.orig}", file=sys.stderr)
        sys.exit(1)
    except ProgrammingError as exc:
        print(
            "数据库操作失败，请检查 models 表是否已创建、字段是否匹配。",
            file=sys.stderr,
        )
        print("首次部署请先执行 sql/schema.sql 建表。", file=sys.stderr)
        print(f"详细错误：{exc.orig}", file=sys.stderr)
        sys.exit(1)
    except (SQLAlchemyError, ValueError, RuntimeError) as exc:
        print(f"内置模型同步失败：{exc}", file=sys.stderr)
        sys.exit(1)
