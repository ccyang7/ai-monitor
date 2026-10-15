"""生成看起来真实的调用日志，供后续统计、图表、异常分析的开发验证使用。"""

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.exc import OperationalError, ProgrammingError  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import LLMRequest, Model, Project  # noqa: E402
from app.services import ingest as ingest_service  # noqa: E402

# 作息按北京时间加权：凌晨几乎没人用，上午和下午是高峰。
# 库里统一存 UTC，所以生成完要转回去 —— 否则图表上的高峰会出现在半夜
BEIJING = timezone(timedelta(hours=8))
HOUR_WEIGHTS = [
    1, 1, 1, 1, 1, 1, 2, 3, 5, 6, 7, 6, 4, 6, 7, 7, 6, 5, 4, 3, 3, 2, 2, 1,
]

QUESTIONS = [
    "什么是 Redis？它和 Memcached 有什么区别？",
    "帮我写一个 Python 快速排序，不能用内置的 sort",
    "解释一下数据库索引为什么能让查询变快",
    "FastAPI 的 Depends 是怎么工作的？",
    "如何设计一个短链接系统？说说关键点",
    "这段代码为什么会有内存泄漏？",
    "用通俗的话解释 TCP 三次握手",
    "Docker 容器和虚拟机的本质区别是什么？",
    "PostgreSQL 的分页查询怎么写性能最好？",
    "什么是 JWT？为什么签出去的 token 没法撤销？",
    "解释一下数据库事务的四种隔离级别",
    "用通俗的语言讲讲什么是向量数据库",
    "为什么我的 Java 服务 CPU 一直占满 100%？",
    "微服务之间怎么做鉴权比较合理？",
    "帮我看看这个 SQL 为什么这么慢",
    "LangChain 里的 Chain 和 Agent 有什么区别？",
]

ANSWERS = [
    "Redis 是开源的内存键值数据库。和 Memcached 相比，它支持更丰富的数据结构：字符串、列表、哈希、集合、有序集合，还支持持久化、主从复制和集群。Memcached 只支持简单的键值缓存，也没有持久化能力。选型上，如果只是做纯缓存，两者都可以；如果需要计数器、排行榜、消息队列这类场景，Redis 更合适。",
    "快速排序的核心是分治：选一个基准值，把数组分成比它小和比它大的两部分，再递归处理。平均时间复杂度 O(n log n)，最坏情况 O(n²)——发生在每次选到的基准都是极值时，所以工程上常用随机选取基准来规避。原地分区版本通过双指针交换实现，不需要额外数组。",
    "数据库索引通常用 B+ 树实现。没有索引时，查询要逐行扫描整张表，复杂度 O(n)；有了索引，B+ 树的高度通常只有 3 到 4 层，每次查询只需要几次磁盘 IO，复杂度降到 O(log n)。需要注意的是，索引会占用额外空间，而且每次写入都要维护，所以不是越多越好。",
    "Depends 是 FastAPI 的依赖注入机制。你在函数参数上声明 Depends(some_func)，框架会在调用你的接口函数之前先执行 some_func，把返回值作为参数传进来。它的价值在于把鉴权、数据库会话、分页参数这类横切逻辑从业务代码里抽出来，还能形成依赖链：某个依赖自己也可以声明依赖。",
    "短链接系统的关键点有几个：一是发号，可以用自增 ID 转 62 进制，也可以用哈希后取前几位；二是存储，映射关系放 KV 数据库最合适；三是跳转，用 301 还是 302 要看是否需要统计点击；四是缓存，热点链接要挡在 Redis 里；五是高可用，发号器的单点问题需要提前设计。",
    "这段代码的问题在于每次循环都创建了新的对象并注册到全局监听器上，但退出循环时没有注销。监听器持有对象的强引用，GC 就无法回收，随着循环次数增加，内存占用会持续上升。修复方法是在合适的时机调用注销方法，或者改用弱引用容器来保存监听器。",
    "三次握手的目的，是让通信双方都确认「我能发、你能收」这件事。第一次客户端发 SYN，服务端知道客户端能发；第二次服务端回 SYN+ACK，客户端知道服务端能收也能发；第三次客户端再发 ACK，服务端知道客户端能收。三次是理论上的最小值，两次无法让服务端确认客户端的接收能力。",
    "容器和虚拟机的根本区别在于虚拟化的层次。虚拟机虚拟的是硬件，每台虚拟机跑一个完整的操作系统内核，隔离性强但资源开销大；容器虚拟的是操作系统，所有容器共享宿主机内核，通过 namespace 做隔离、cgroup 做资源限制，所以启动快、开销小，但隔离性弱于虚拟机。",
    "PostgreSQL 里用 LIMIT/OFFSET 分页，偏移量大时性能会急剧下降——因为数据库仍要扫描并丢弃前 N 行。更好的做法是键集分页：记住上一页最后一行的排序键，下一页用 WHERE (created_at, id) < (上一页最后一个值) 来查。这种方式无论翻到第几页，扫描量都是固定的。",
    "JWT 是无状态的：服务端不保存已签发的 token，只验证签名。这意味着只要签名合法、没过期，服务端就认。想撤销就必须引入服务端状态，比如维护一个黑名单，或者记录 token 的签发版本号。一旦引入这些，JWT 就失去了无状态的优点——这也是很多系统只在短期凭证上用它的原因。",
    "四种隔离级别从低到高是：读未提交、读已提交、可重复读、串行化。它们分别解决了脏读、不可重复读、幻读这三类问题。PostgreSQL 默认是读已提交；MySQL 的 InnoDB 默认是可重复读，并通过间隙锁在很大程度上避免了幻读。级别越高，并发性能越低。",
    "向量数据库是把文本、图片这类非结构化数据转成高维向量后存储，并提供相似度检索能力的数据库。传统数据库查的是「等于什么」，它查的是「和什么最像」。索引结构通常是 HNSW 或 IVF 这类近似最近邻算法，用一点点精度换取大幅的性能提升。",
    "CPU 占满通常要先定位到线程。用 top -H 找到占用高的线程 ID，转成十六进制，再 jstack 导出线程栈去匹配，就能定位到具体代码。常见原因有：死循环或忙等待、正则回溯、频繁的 Full GC、以及锁竞争导致的空转。先从线程栈入手，一般几分钟就能找到方向。",
    "服务间鉴权常见三种做法：一是共享密钥，简单但泄露后影响面大；二是 mTLS，双向证书认证，安全但运维成本高；三是在网关统一签发内部 token，服务间透传。实际项目里通常是网关做外部鉴权、内部用轻量 token 加网络隔离，不必每个服务都做完整的认证。",
    "这个 SQL 慢的原因在于 WHERE 里对索引列用了函数，比如 WHERE DATE(created_at) = '2026-01-01'，这会让索引失效，退化成全表扫描。改写成范围查询 WHERE created_at >= '2026-01-01' AND created_at < '2026-01-02' 就能用上索引。另外可以看看执行计划，确认估算行数和实际行数是否差很多。",
    "Chain 是把多个组件按固定顺序串起来的流程，路径是确定的：输入经过提示词模板、模型、输出解析器，一步接一步。Agent 则把「下一步做什么」交给模型来决定，它会根据当前情况选择调用哪个工具，所以路径是动态的。简单的固定流程用 Chain，需要多步推理和工具调用的场景用 Agent。",
]

ERRORS = [
    "Request timeout after 30s",
    "Rate limit exceeded: 429 Too Many Requests",
    "Connection reset by peer",
    "上游服务返回 502 Bad Gateway",
    "context length exceeded: 131072 tokens",
    "Invalid API key provided",
    "Read timed out",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成测试用的调用日志")
    parser.add_argument("--project-id", type=int, required=True, help="日志写入哪个项目")
    parser.add_argument("--count", type=int, default=500, help="生成条数（默认 500）")
    parser.add_argument("--days", type=int, default=30, help="数据跨越最近多少天（默认 30）")
    parser.add_argument("--seed", type=int, default=None, help="随机种子，指定后结果可复现")
    return parser.parse_args()


def random_created_at(rng: random.Random, days: int) -> datetime:
    at = datetime.now(timezone.utc).astimezone(BEIJING)
    at = at.replace(
        hour=rng.choices(range(24), weights=HOUR_WEIGHTS)[0],
        minute=rng.randrange(60),
        second=rng.randrange(60),
        microsecond=0,
    ) - timedelta(days=rng.randrange(days))

    # 今天随机到的钟点可能还没到，那样会生成「未来发生」的调用
    if at > datetime.now(timezone.utc).astimezone(BEIJING):
        at -= timedelta(days=1)

    return at.astimezone(timezone.utc)


def build_record(rng: random.Random, project: Project, priced: Model, days: int) -> LLMRequest:
    # 8% 的样本刻意放大：后面做异常检测（超基线 N 倍判为异常）时需要已知的异常样本，
    # 全是正常数据的话没法验证判定逻辑对不对
    if rng.random() < 0.08:
        input_tokens = rng.randrange(15_000, 40_000)
        output_tokens = rng.randrange(5_000, 20_000)
        latency = rng.randrange(15_000, 40_000)
    else:
        input_tokens = rng.randrange(200, 2_000)
        output_tokens = rng.randrange(300, 3_000)
        latency = rng.randrange(800, 4_000)

    succeeded = rng.random() >= 0.1

    # 只给一部分成功调用生成缓存命中（真实场景里能否命中取决于前缀是否重复出现过），
    # 让开发环境同时有「有命中 / 无命中」两种数据可看
    if succeeded and rng.random() < 0.5:
        cache_hit_tokens = int(input_tokens * rng.uniform(0.3, 0.8))
    else:
        cache_hit_tokens = 0

    return LLMRequest(
        project_id=project.id,
        provider=priced.provider,
        model=priced.model_name,
        input=rng.choice(QUESTIONS),
        output=rng.choice(ANSWERS) if succeeded else None,
        input_tokens=input_tokens,
        cache_hit_tokens=cache_hit_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        latency=latency,
        status="success" if succeeded else "error",
        error_message=None if succeeded else rng.choice(ERRORS),
        # 和上报接口走同一个成本公式，口径必须一致，否则播种数据的成本统计对不上
        estimated_cost=ingest_service.compute_cost(
            input_tokens, cache_hit_tokens, output_tokens, priced
        ),
        created_at=random_created_at(rng, days),
    )


def main() -> int:
    args = parse_args()
    rng = random.Random(args.seed)

    with SessionLocal() as db:
        project = db.get(Project, args.project_id)
        if project is None:
            print(f"❌ 项目 {args.project_id} 不存在")
            return 1

        priced_models = list(
            db.scalars(select(Model).where(Model.is_builtin.is_(True)))
        )
        if not priced_models:
            print("❌ models 表里没有内置模型，先跑：python scripts/init_models.py")
            return 1

        before = db.scalar(
            select(func.count())
            .select_from(LLMRequest)
            .where(LLMRequest.project_id == project.id)
        )

        records = [
            build_record(rng, project, rng.choice(priced_models), args.days)
            for _ in range(args.count)
        ]
        db.add_all(records)
        db.commit()

        # commit 会让 ORM 对象的属性过期，会话一关就再也读不出来（DetachedInstanceError），
        # 所以统计值要在会话内先算好
        project_name, project_id = project.name, project.id
        models_used = len({r.model for r in records})
        failed = sum(1 for r in records if r.status == "error")

    print(f"项目「{project_name}」（id={project_id}）")
    print(f"原有日志 {before} 条，新生成 {len(records)} 条，覆盖 {models_used} 个模型")
    print(f"时间范围：最近 {args.days} 天；失败 {failed} 条")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OperationalError as exc:
        print("❌ 连不上数据库，先确认 ai-monitor-backend/.env 里的 DATABASE_URL")
        print(f"   原始错误：{exc.orig}")
        sys.exit(1)
    except ProgrammingError as exc:
        print("❌ 数据库里还没有 llm_requests / models 表，先执行建表脚本：")
        print("   psql -U ai_monitor -d ai_monitor -h 127.0.0.1 -f sql/schema.sql")
        print(f"   原始错误：{exc.orig}")
        sys.exit(1)
