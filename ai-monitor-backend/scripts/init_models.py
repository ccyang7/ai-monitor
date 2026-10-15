"""写入内置模型价格（幂等，可反复执行），上报接口算成本依赖这份数据。"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import OperationalError, ProgrammingError  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Model  # noqa: E402

# 价格单位：元 / 百万 token，照抄厂商官网的报价口径。
# 参考价会随厂商调价变动 —— 改这里的数字重跑脚本即可，已有记录会被更新
#
# 五元组：provider, model_name, input_price, output_price, cache_hit_price。
# cache_hit_price = None 表示「无缓存档，或官网查不到当前两档价」，
# 计费时命中部分按 input_price 算 —— 宁可高估也不低估
BUILTIN_MODELS = [
    ("deepseek", "deepseek-chat", "1.000000", "2.000000", None),
    ("deepseek", "deepseek-reasoner", "4.000000", "16.000000", None),
    # DeepSeek 2026-04 把缓存命中价永久降至首发价的 1/10（0.2 → 0.02 元/百万），
    # 与官方美元价 $0.003/MTok 按项目内汇率口径换算一致
    ("deepseek", "deepseek-v4-flash", "1.000000", "2.000000", "0.020000"),
    # OpenAI 的 prompt caching 自动生效，缓存输入固定为标准输入的 50%
    # （官方 gpt-4o 为 $1.25 对 $2.50），所以取项目内输入价的一半
    ("openai", "gpt-4o", "18.000000", "72.000000", "9.000000"),
    ("openai", "gpt-4o-mini", "1.100000", "4.400000", "0.550000"),
    # 嵌入模型没有 output token，output_price 填 0 而非留空。input_price 的 0 是
    # 真实价格（硅基流动免费版 bge-m3），不是没填 —— 它让成本列显示 ¥0.000，
    # 含义是「这笔钱不是嵌入花的」，与模型名未收录时显示的「—」是两回事
    ("siliconflow", "BAAI/bge-m3", "0.000000", "0.000000", None),
]


def main() -> int:
    created = 0
    updated = 0

    with SessionLocal() as db:
        for provider, model_name, input_price, output_price, cache_hit_price in BUILTIN_MODELS:
            # 先转好再用：比较和赋值都吃同一份，避免两处转换写法分叉
            input_price_dec = Decimal(input_price)
            output_price_dec = Decimal(output_price)
            cache_hit_price_dec = (
                Decimal(cache_hit_price) if cache_hit_price is not None else None
            )

            # 幂等的关键：先按 model_name + is_builtin 找，找到就更新、找不到才插入。
            # 不做这一步的话跑两次库里就有两份同名的内置模型，上报时会随机匹配到其中一个价格
            model = db.scalar(
                select(Model).where(
                    Model.model_name == model_name, Model.is_builtin.is_(True)
                )
            )

            if model is None:
                db.add(
                    Model(
                        provider=provider,
                        model_name=model_name,
                        input_price=input_price_dec,
                        output_price=output_price_dec,
                        cache_hit_price=cache_hit_price_dec,
                        is_builtin=True,
                    )
                )
                created += 1
            elif (
                model.input_price != input_price_dec
                or model.output_price != output_price_dec
                or model.cache_hit_price != cache_hit_price_dec
                or model.provider != provider
            ):
                model.provider = provider
                model.input_price = input_price_dec
                model.output_price = output_price_dec
                model.cache_hit_price = cache_hit_price_dec
                updated += 1

        db.commit()

    print(f"内置模型：新增 {created} 个，更新 {updated} 个，未变 {len(BUILTIN_MODELS) - created - updated} 个")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OperationalError as exc:
        print("❌ 连不上数据库，先确认 ai-monitor-backend/.env 里的 DATABASE_URL")
        print(f"   原始错误：{exc.orig}")
        sys.exit(1)
    except ProgrammingError as exc:
        print("❌ 数据库里还没有 models 表，先执行建表脚本：")
        print("   psql -U ai_monitor -d ai_monitor -h 127.0.0.1 -f sql/schema.sql")
        print(f"   原始错误：{exc.orig}")
        sys.exit(1)
