"""接口验收：用真实 HTTP 请求跑完阶段 7（Token / Cost 统计）的全部验收清单。"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 把项目根目录（ai-monitor-backend/）加进模块搜索路径 ——
# Python 默认只加了「脚本所在目录」scripts/，那样 import app 会找不到
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import OperationalError, ProgrammingError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from main import app  # noqa: E402

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or (
    settings.database_url.rsplit("/", 1)[0] + "/ai_monitor_test"
)

# 保险栓：库名不对就拒绝跑 —— 每项检查前都会 TRUNCATE，指到真库上等于清空数据
_DB_NAME = TEST_DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
if not _DB_NAME.endswith("_test"):
    print(f"拒绝执行：测试库名必须是 *_test 结尾，当前是「{_DB_NAME}」。")
    print("这个脚本会清空表，不会在非测试库上运行。")
    sys.exit(1)

test_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


def override_get_db():
    db = TestSession()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

BUILTIN_MODELS = [
    ("deepseek", "deepseek-chat", "1", "2"),
    ("openai", "gpt-4o", "10", "30"),
]

BASE = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

# 每次成功调用的固定成本：100 输入 * 1 元/百万 + 200 输出 * 2 元/百万。
# 测试内置模型没配 cache_hit_price（NULL），命中的 40 个 token 退回按输入价算 ——
# 成本与改动前逐分钱一致，所以这个常量不跟着变
CHAT_COST = 0.0005
OV_FIELDS = {"calls", "total_tokens", "cache_hit_tokens", "cost", "avg_latency"}

STATS_KINDS = ("overview", "tokens", "costs", "models")


def mask_password(url: str) -> str:
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", url)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_close(actual: float, expected: float, message: str) -> None:
    # 金额走了一遍 NUMERIC -> float 的转换，直接比等号会被二进制浮点坑到
    if abs(actual - expected) > 1e-9:
        raise AssertionError(f"{message}：期望 {expected}，实际 {actual}")


def reset_db() -> None:
    with test_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, projects, api_keys, models, llm_requests"
                " RESTART IDENTITY CASCADE"
            )
        )
        for provider, name, input_price, output_price in BUILTIN_MODELS:
            conn.execute(
                text(
                    "INSERT INTO models (provider, model_name, input_price,"
                    " output_price, is_builtin) VALUES (:p, :n, :i, :o, true)"
                ),
                {"p": provider, "n": name, "i": input_price, "o": output_price},
            )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def api_key_header(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def register_and_login(username: str, password: str = "pw123456") -> str:
    r = client.post(
        "/api/v1/auth/register", json={"username": username, "password": password}
    )
    expect(r.status_code == 201, f"注册 {username} 应返回 201，实际 {r.status_code}：{r.text}")

    r = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    expect(r.status_code == 200, f"登录 {username} 应返回 200，实际 {r.status_code}：{r.text}")
    return r.json()["access_token"]


def new_project(token: str, name: str) -> dict:
    r = client.post("/api/v1/projects", headers=auth(token), json={"name": name})
    expect(r.status_code == 201, f"创建项目「{name}」应返回 201，实际 {r.status_code}：{r.text}")
    return r.json()


def ingest(key: str, **overrides):
    payload = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "input": "什么是 Redis？",
        "output": "Redis 是一种开源的键值数据库……",
        "input_tokens": 100,
        "cache_hit_tokens": 40,
        "output_tokens": 200,
        "latency": 1000,
        "status": "success",
    }
    payload.update(overrides)
    return client.post("/api/v1/logs", headers=api_key_header(key), json=payload)


def seed_logs(key: str, rows: list[dict]) -> list[int]:
    ids = []
    for row in rows:
        r = ingest(key, **row)
        expect(r.status_code == 201, f"造数据上报失败 {r.status_code}：{r.text}")
        ids.append(r.json()["id"])
    return ids


def _clean(params: dict) -> dict:
    # httpx 不会替我们把 datetime 转成 URL 能用的格式，先在脚本里转好
    return {
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in params.items()
        if v is not None
    }


def stats(token: str, project_id: int, kind: str, **params) -> dict:
    r = client.get(
        f"/api/v1/projects/{project_id}/stats/{kind}",
        headers=auth(token),
        params=_clean(params),
    )
    expect(r.status_code == 200, f"{kind} 应返回 200，实际 {r.status_code}：{r.text}")
    return r.json()


def days_span(days: int) -> tuple[datetime, datetime]:
    """返回以 BASE 为起点、跨度 days 天的区间（左闭右开）。"""
    return BASE, BASE + timedelta(days=days)


# 当前区间 09-08 ~ 09-15，环比区间自然是 09-01 ~ 09-08
CUR_START, CUR_END = BASE + timedelta(days=7), BASE + timedelta(days=14)
PREV_START, PREV_END = BASE, BASE + timedelta(days=7)


def seed_two_periods(key: str) -> None:
    """当前区间 3 次成功（延迟 1000/2000/3000）+ 1 次失败；环比区间 2 次成功。"""
    seed_logs(
        key,
        [
            {"timestamp": (CUR_START + timedelta(hours=1)).isoformat(), "latency": 1000},
            {"timestamp": (CUR_START + timedelta(hours=2)).isoformat(), "latency": 2000},
            {"timestamp": (CUR_START + timedelta(hours=3)).isoformat(), "latency": 3000},
            {
                "timestamp": (CUR_START + timedelta(hours=4)).isoformat(),
                "latency": 30000,
                "status": "error",
                "error": "Request timeout after 30s",
                "output": None,
            },
            {"timestamp": (PREV_START + timedelta(days=1)).isoformat(), "latency": 1000},
            {"timestamp": (PREV_START + timedelta(days=2)).isoformat(), "latency": 1000},
        ],
    )


def check_overview_summary() -> None:
    token = register_and_login("alice")
    created = new_project(token, "概览汇总")
    pid, key = created["id"], created["api_key"]
    seed_two_periods(key)

    body = stats(token, pid, "overview", start_date=CUR_START, end_date=CUR_END)

    expect(
        set(body.keys()) == {"summary", "previous", "anomaly_counts", "baseline_available"},
        f"overview 顶层字段变了：{sorted(body.keys())}",
    )

    s = body["summary"]
    expect(set(s.keys()) == OV_FIELDS, f"summary 字段变了：{sorted(s.keys())}")
    expect(s["calls"] == 4, f"当前区间应有 4 次调用，实际 {s['calls']}")
    expect(s["total_tokens"] == 1200, f"Token 应为 1200，实际 {s['total_tokens']}")
    expect(
        s["cache_hit_tokens"] == 160,
        f"当前区间 4 次调用各命中 40，应为 160，实际 {s['cache_hit_tokens']}",
    )
    # 失败那条一样有 Token 消耗、一样有成本：statistics 不按 status 打折，
    # 只有「平均响应时间」才排除失败调用
    expect_close(s["cost"], 4 * CHAT_COST, "当前区间成本")
    # 失败的 30000ms 必须被排除：算进去平均值会从 2000 跳到 9000
    expect(
        s["avg_latency"] == 2000,
        f"平均响应应只算成功的 3 次 = 2000，实际 {s['avg_latency']} —— 失败调用混进来了",
    )

    # 环比区间必须是「上一个等长区间」，不是「昨天」或「上周」这种自然周期
    p = body["previous"]
    expect(set(p.keys()) == OV_FIELDS, f"previous 字段变了：{sorted(p.keys())}")
    expect(p["calls"] == 2, f"环比区间应有 2 次调用，实际 {p['calls']}")
    expect(p["total_tokens"] == 600, f"环比 Token 应为 600，实际 {p['total_tokens']}")
    expect(
        p["cache_hit_tokens"] == 80,
        f"环比区间 2 次调用各命中 40，应为 80，实际 {p['cache_hit_tokens']}",
    )
    expect_close(p["cost"], 2 * CHAT_COST, "环比区间成本")
    expect(p["avg_latency"] == 1000, f"环比平均响应应为 1000，实际 {p['avg_latency']}")

    # 阶段 9 接上后这里是真实条数。断言四格全写死，同时锁住两件事：那条失败调用算得上
    # error 类异常，以及另外三类都是 0 —— 本脚本的数据都在 9 月，落在「近 7 天」基线
    # 窗口之外，基线样本数为 0，token / latency 本该一个都不判；error_rate 要凑满
    # 10 次调用才判，那天也没那么多
    expect(
        body["anomaly_counts"] == {"token": 0, "latency": 0, "error": 1, "error_rate": 0},
        f"异常数应只有那条失败调用，实际 {body['anomaly_counts']!r}",
    )
    # 基线不可用要说出来：四格里的 0 有「量过了是 0」和「还没开始量」两种含义，
    # 前端只有拿到这个布尔值才分得清该说「没有异常」还是「样本还在攒」
    expect(
        body["baseline_available"] is False,
        f"本脚本的数据全在基线窗口之外，应报基线不可用，实际 {body['baseline_available']!r}",
    )


def check_period_boundary() -> None:
    """区间左闭右开：正好落在 end 那一刻的记录不属于本区间。"""
    token = register_and_login("bob")
    created = new_project(token, "边界")
    pid, key = created["id"], created["api_key"]

    seed_logs(
        key,
        [
            # 刚好落在起点上：属于当前区间
            {"timestamp": CUR_START.isoformat()},
            # 刚好落在终点上：当前区间与环比区间都不该收它
            {"timestamp": CUR_END.isoformat()},
            # 环比区间的最后一刻：属于环比区间
            {"timestamp": (CUR_START - timedelta(microseconds=1)).isoformat()},
        ],
    )

    body = stats(token, pid, "overview", start_date=CUR_START, end_date=CUR_END)
    expect(
        body["summary"]["calls"] == 1,
        f"起点那一刻的记录应算进当前区间（共 1 条），实际 {body['summary']['calls']} —— "
        "区间可能被写成了闭区间",
    )
    expect(
        body["previous"]["calls"] == 1,
        f"环比区间应收到 end 前最后一微秒那一条，实际 {body['previous']['calls']}",
    )


def check_token_series_gapfill() -> None:
    token = register_and_login("dave")
    created = new_project(token, "Token 趋势")
    pid, key = created["id"], created["api_key"]

    start, end = days_span(5)
    # 只在第 1、3、5 天有调用，中间两天必须补零
    seed_logs(
        key,
        [
            {"timestamp": (start + timedelta(hours=1)).isoformat()},
            {"timestamp": (start + timedelta(days=2, hours=1)).isoformat()},
            {"timestamp": (start + timedelta(days=4, hours=1)).isoformat(), "input_tokens": 300, "output_tokens": 500},
        ],
    )

    body = stats(token, pid, "tokens", start_date=start, end_date=end, tz="UTC")

    s = body["summary"]
    expect(
        set(s.keys())
        == {
            "calls",
            "input_tokens",
            "cache_hit_tokens",
            "output_tokens",
            "total_tokens",
        },
        f"tokens summary 字段变了：{sorted(s.keys())}",
    )
    expect(s["calls"] == 3, f"应有 3 次调用，实际 {s['calls']}")
    expect(s["input_tokens"] == 100 + 100 + 300, f"输入 Token 不对：{s['input_tokens']}")
    expect(s["output_tokens"] == 200 + 200 + 500, f"输出 Token 不对：{s['output_tokens']}")
    expect(s["cache_hit_tokens"] == 120, f"命中 Token 不对：{s['cache_hit_tokens']}")
    expect(
        s["input_tokens"] + s["output_tokens"] == s["total_tokens"],
        "输入 + 输出应等于总量",
    )

    series = body["series"]
    expect(len(series) == 5, f"5 天区间应有 5 个数据点，实际 {len(series)}")
    expect(
        [p["date"] for p in series] == [f"2026-09-0{i}" for i in range(1, 6)],
        f"日期序列不连续：{[p['date'] for p in series]}",
    )

    calls_by_day = {p["date"]: p["calls"] for p in series}
    expect(
        calls_by_day == {"2026-09-01": 1, "2026-09-02": 0, "2026-09-03": 1, "2026-09-04": 0, "2026-09-05": 1},
        f"没有调用的日期必须补零而不是缺失，实际 {calls_by_day}",
    )

    gap = [p for p in series if p["calls"] == 0]
    expect(
        all(p["total_tokens"] == 0 for p in gap),
        f"补零的日期 Token 也该是 0，实际 {gap}",
    )


def check_cost_series() -> None:
    token = register_and_login("erin")
    created = new_project(token, "成本趋势")
    pid, key = created["id"], created["api_key"]

    start, end = days_span(3)
    seed_logs(
        key,
        [
            {"timestamp": (start + timedelta(hours=1)).isoformat()},
            {"timestamp": (start + timedelta(hours=2)).isoformat()},
            {"timestamp": (start + timedelta(days=2, hours=1)).isoformat(), "provider": "openai", "model": "gpt-4o"},
        ],
    )

    body = stats(token, pid, "costs", start_date=start, end_date=end, tz="UTC")
    expect(
        set(body["summary"].keys()) == {"calls", "cost"},
        f"costs summary 字段变了：{sorted(body['summary'].keys())}",
    )

    # gpt-4o 是 10 / 30 元每百万：100*10/1e6 + 200*30/1e6 = 0.007
    expect_close(body["summary"]["cost"], 2 * CHAT_COST + 0.007, "成本合计")

    series_total = sum(p["cost"] for p in body["series"])
    expect_close(
        series_total,
        body["summary"]["cost"],
        "按天成本之和应等于区间合计 —— 两边口径不一致的话，图和数字会对不上",
    )
    expect(len(body["series"]) == 3, f"3 天区间应有 3 个点，实际 {len(body['series'])}")


def check_model_breakdown() -> None:
    token = register_and_login("frank")
    created = new_project(token, "模型分布")
    pid, key = created["id"], created["api_key"]

    start, end = days_span(2)
    at = (start + timedelta(hours=1)).isoformat()
    seed_logs(
        key,
        [
            {"timestamp": at},
            {"timestamp": at},
            # gpt-4o 显式加大 Token：三次调用都用默认的 100+200 的话，
            # 「按次数」和「按 Token」会算出同一个占比，这条用例就白测了。
            # 现在 gpt-4o 只有 1 次调用（25%），Token 却最多（800/1700）
            {
                "timestamp": at,
                "provider": "openai",
                "model": "gpt-4o",
                "input_tokens": 400,
                "output_tokens": 400,
            },
            # 没配价格的模型：照常写入，成本留空
            {"timestamp": at, "provider": "unknown", "model": "not-priced-yet"},
        ],
    )

    body = stats(token, pid, "models", start_date=start, end_date=end, tz="UTC")
    items = body["items"]
    expect(len(items) == 3, f"应统计出 3 个模型，实际 {len(items)}：{[i['model'] for i in items]}")

    by_model = {i["model"]: i for i in items}
    expect(
        [i["model"] for i in items][0] == "deepseek-chat",
        f"应按调用次数倒序，实际顺序 {[i['model'] for i in items]}",
    )

    expect(by_model["deepseek-chat"]["calls"] == 2, "deepseek-chat 应有 2 次")
    expect(by_model["deepseek-chat"]["total_tokens"] == 600, "deepseek-chat Token 不对")
    expect(
        by_model["deepseek-chat"]["cache_hit_tokens"] == 80,
        f"deepseek-chat 命中数不对：{by_model['deepseek-chat']['cache_hit_tokens']}",
    )
    expect_close(by_model["deepseek-chat"]["cost"], 2 * CHAT_COST, "deepseek-chat 成本")
    expect(by_model["deepseek-chat"]["call_percentage"] == 50.0, "按调用次数的占比应为 50%")

    # 未知模型的成本是 0 而不是 null：SUM 跳过 NULL、COALESCE 兜底。
    # 也就是说「没配价格」在模型的成本列上会被显示成 0 元 —— 这是设计文档 03 §5
    # 明确的口径（跳过或按 0 计），不是 bug
    expect(
        by_model["not-priced-yet"]["cost"] == 0.0,
        f"未收录模型的成本应为 0（NULL 被跳过），实际 {by_model['not-priced-yet']['cost']!r}",
    )

    # 三套口径必须是三套数，只算一套再抄给另外两个字段的实现会在这里挂掉：
    # gpt-4o 调用次数只占 25%，Token 却占 800/1700
    expect(
        by_model["gpt-4o"]["call_percentage"] == 25.0,
        f"gpt-4o 按调用次数应占 25%，实际 {by_model['gpt-4o']['call_percentage']}",
    )
    expect(
        by_model["gpt-4o"]["token_percentage"] == 47.1,
        f"gpt-4o 按 Token 应占 47.1%，实际 {by_model['gpt-4o']['token_percentage']}",
    )

    for field in ("call_percentage", "token_percentage", "cost_percentage"):
        total = sum(i[field] for i in items)
        expect(
            abs(total - 100.0) <= 0.3,
            f"{field} 之和应约等于 100（各自四舍五入会有零点几的漂移），实际 {total}",
        )


def check_avg_latency_null() -> None:
    """区间内一次成功调用都没有时，平均响应是 null 而不是 0。"""
    token = register_and_login("grace")
    created = new_project(token, "全失败")
    pid, key = created["id"], created["api_key"]

    start, end = days_span(2)
    seed_logs(
        key,
        [
            {"timestamp": (start + timedelta(hours=1)).isoformat(), "status": "error", "error": "boom", "output": None},
            {"timestamp": (start + timedelta(hours=2)).isoformat(), "status": "error", "error": "boom", "output": None},
        ],
    )

    body = stats(token, pid, "overview", start_date=start, end_date=end, tz="UTC")
    expect(body["summary"]["calls"] == 2, f"失败的调用也要计数，实际 {body['summary']['calls']}")
    expect(
        body["summary"]["avg_latency"] is None,
        f"没有成功调用时平均响应应是 null，实际 {body['summary']['avg_latency']!r} —— "
        "0 会被前端读成「响应快得离谱」",
    )
    # Token 和成本照常统计：失败的调用也烧了钱
    expect(body["summary"]["total_tokens"] == 600, "失败的调用也有 Token 消耗")

    models = stats(token, pid, "models", start_date=start, end_date=end, tz="UTC")
    expect(
        models["items"][0]["avg_latency"] is None,
        f"模型维度的平均响应同理应为 null，实际 {models['items'][0]['avg_latency']!r}",
    )


def check_empty_project() -> None:
    token = register_and_login("heidi")
    created = new_project(token, "空项目")
    pid = created["id"]

    start, end = days_span(3)
    body = stats(token, pid, "overview", start_date=start, end_date=end, tz="UTC")

    s = body["summary"]
    expect(s["calls"] == 0 and s["total_tokens"] == 0, f"空项目各项应为 0，实际 {s}")
    expect_close(s["cost"], 0.0, "空项目成本")
    expect(s["avg_latency"] is None, "空项目的平均响应应是 null")
    expect(body["previous"]["calls"] == 0, "空项目的环比也应是 0")

    tokens = stats(token, pid, "tokens", start_date=start, end_date=end, tz="UTC")
    expect(len(tokens["series"]) == 3, "即使没有数据，趋势也要按天数补零，否则折线图是断的")
    expect(
        all(p["calls"] == 0 and p["total_tokens"] == 0 for p in tokens["series"]),
        f"空项目的趋势点应全是 0，实际 {tokens['series']}",
    )

    models = stats(token, pid, "models", start_date=start, end_date=end, tz="UTC")
    expect(models["items"] == [], f"没有调用时模型列表应为空数组，实际 {models['items']}")


def check_timezone_bucketing() -> None:
    """同一条记录在不同时区下属于不同的「天」—— 这是本模块最容易出错的地方。"""
    token = register_and_login("ivan")
    created = new_project(token, "时区分桶")
    pid, key = created["id"], created["api_key"]

    # 16:30Z 就是北京时间次日 00:30
    at = datetime(2026, 9, 1, 16, 30, tzinfo=timezone.utc)
    seed_logs(key, [{"timestamp": at.isoformat()}])

    start = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc)

    utc = stats(token, pid, "tokens", start_date=start, end_date=end, tz="UTC")
    sh = stats(token, pid, "tokens", start_date=start, end_date=end, tz="Asia/Shanghai")

    utc_calls = {p["date"]: p["calls"] for p in utc["series"]}
    sh_calls = {p["date"]: p["calls"] for p in sh["series"]}

    expect(
        utc_calls.get("2026-09-01") == 1,
        f"UTC 分桶下这条应落在 09-01，实际 {utc_calls}",
    )
    expect(
        sh_calls.get("2026-09-02") == 1,
        f"北京时间分桶下这条应落在 09-02，实际 {sh_calls} —— "
        "分桶没有按时区截断，+08:00 的用户会看到趋势整体偏 8 小时",
    )
    expect(
        utc_calls.get("2026-09-02", 0) == 0 and sh_calls.get("2026-09-01", 0) == 0,
        f"记录不该同时出现在两天：UTC={utc_calls} 上海={sh_calls}",
    )

    # 总数一致：分桶只影响「算哪一天」，不影响「算不算」
    expect(
        utc["summary"]["calls"] == sh["summary"]["calls"] == 1,
        "换时区不该让记录凭空多出来或消失",
    )


def check_isolation() -> None:
    token_a = register_and_login("judy")
    token_b = register_and_login("karl")
    project_a = new_project(token_a, "A 的项目")
    project_b = new_project(token_b, "B 的项目")

    start, end = days_span(3)
    seed_logs(
        project_b["api_key"],
        [{"timestamp": (start + timedelta(hours=1)).isoformat(), "input": "B 的机密数据"}],
    )

    for kind in STATS_KINDS:
        r = client.get(
            f"/api/v1/projects/{project_b['id']}/stats/{kind}",
            headers=auth(token_a),
            params=_clean({"start_date": start, "end_date": end}),
        )
        expect(
            r.status_code == 404,
            f"查别人的项目 {kind} 应 404，实际 {r.status_code}：{r.text}",
        )
        expect("B 的机密数据" not in r.text, f"{kind} 的 404 响应里泄露了别人项目的内容！")

        r = client.get(
            f"/api/v1/projects/999999/stats/{kind}",
            headers=auth(token_a),
            params=_clean({"start_date": start, "end_date": end}),
        )
        expect(r.status_code == 404, f"查不存在的项目 {kind} 应 404，实际 {r.status_code}")

        r = client.get(
            f"/api/v1/projects/{project_b['id']}/stats/{kind}",
            params=_clean({"start_date": start, "end_date": end}),
        )
        expect(r.status_code == 401, f"未登录查 {kind} 应 401，实际 {r.status_code}")

    # B 自己能查到（证明上面的 404 是隔离生效，不是接口坏了）
    body = stats(token_b, project_b["id"], "tokens", start_date=start, end_date=end)
    expect(body["summary"]["calls"] == 1, f"B 应看到自己的 1 条，实际 {body['summary']['calls']}")

    # A 的项目没有数据，统计不该串到 B 的头上
    body = stats(token_a, project_a["id"], "tokens", start_date=start, end_date=end)
    expect(
        body["summary"]["calls"] == 0,
        f"A 的项目不该统计到 B 的数据，实际 {body['summary']['calls']}",
    )


def check_validation() -> None:
    token = register_and_login("laura")
    created = new_project(token, "校验")
    pid = created["id"]

    cases = [
        ("end 早于 start", {"start_date": CUR_END, "end_date": CUR_START}),
        ("end 等于 start", {"start_date": CUR_START, "end_date": CUR_START}),
        ("缺 start_date", {"end_date": CUR_END}),
        ("缺 end_date", {"start_date": CUR_START}),
        ("时区名不存在", {"start_date": CUR_START, "end_date": CUR_END, "tz": "Mars/Olympus"}),
        ("时区名为空串", {"start_date": CUR_START, "end_date": CUR_END, "tz": ""}),
        (
            "区间超过 3660 天上限",
            {"start_date": CUR_START, "end_date": CUR_START + timedelta(days=3661)},
        ),
        ("start_date 格式非法", {"start_date": "昨天", "end_date": CUR_END}),
    ]

    for kind in STATS_KINDS:
        for label, params in cases:
            r = client.get(
                f"/api/v1/projects/{pid}/stats/{kind}",
                headers=auth(token),
                params=_clean(params),
            )
            expect(
                r.status_code == 422,
                f"{kind} 的「{label}」应返回 422，实际 {r.status_code}：{r.text}",
            )

    # 上限之内要放行，否则这个校验就成了「自定义时间永远不可用」
    body = stats(
        token,
        pid,
        "tokens",
        start_date=CUR_START,
        end_date=CUR_START + timedelta(days=3660),
    )
    expect(len(body["series"]) == 3660, f"3660 天应有 3660 个点，实际 {len(body['series'])}")


def check_timezone_param_not_shifted() -> None:
    """不带时区的时间参数按 UTC 解释，与上报接口同一规则。"""
    token = register_and_login("mona")
    created = new_project(token, "参数时区")
    pid, key = created["id"], created["api_key"]

    at = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    seed_logs(key, [{"timestamp": at.isoformat()}])

    # 不带时区的 end 按 UTC 解释 → 与日志时间相等。区间左闭右开，所以正好在 end
    # 那一刻的记录不算数；把它往前挪 1 秒就该算进去。
    # 若被按本机时区（UTC+8）解释，end 会变成 08-31T16:00Z，两种写法都查不到
    body = stats(
        token,
        pid,
        "overview",
        start_date=datetime(2026, 8, 31, 0, 0),
        end_date=datetime(2026, 9, 1, 0, 0),
    )
    expect(
        body["summary"]["calls"] == 0,
        f"end 正好等于记录时间时应查不到（左闭右开），实际 {body['summary']['calls']} 条",
    )

    body = stats(
        token,
        pid,
        "overview",
        start_date=datetime(2026, 8, 31, 0, 0),
        end_date=datetime(2026, 9, 1, 0, 0, 1),
    )
    expect(
        body["summary"]["calls"] == 1,
        f"end 挪后 1 秒就该查到，实际 {body['summary']['calls']} 条 —— "
        "不带时区的参数可能被按本机时区解释了",
    )


def check_hourly_series() -> None:
    """跨度 1 天 + granularity=hour：24 个点，有调用的那些小时必须有数。"""
    token = register_and_login("hugo")
    created = new_project(token, "小时趋势")
    pid, key = created["id"], created["api_key"]

    start, end = days_span(1)
    seed_logs(
        key,
        [
            {"timestamp": (start + timedelta(minutes=30)).isoformat()},
            # 13:59:59 和 14:00:00 分属两个桶：左闭右开在小时粒度上同样成立
            {"timestamp": (start + timedelta(hours=13, minutes=59, seconds=59)).isoformat()},
            {"timestamp": (start + timedelta(hours=14)).isoformat()},
            {"timestamp": (start + timedelta(hours=23, minutes=30)).isoformat()},
        ],
    )

    body = stats(
        token, pid, "tokens", start_date=start, end_date=end, tz="UTC", granularity="hour"
    )

    series = body["series"]
    expect(len(series) == 24, f"1 天的小时序列应有 24 个点，实际 {len(series)}")
    expect(
        [series[0]["date"], series[-1]["date"]] == ["2026-09-01T00:00", "2026-09-01T23:00"],
        f"标签首尾不对：{series[0]['date']} … {series[-1]['date']}",
    )

    # 只数长度抓不出「骨架和 SQL 的桶对不上」—— 那种情况 24 个点一个不少，只是全为 0。
    # 必须断言有调用的那几格真的有数
    calls_by_hour = {p["date"]: p["calls"] for p in series}
    for label in ("2026-09-01T00:00", "2026-09-01T13:00", "2026-09-01T14:00", "2026-09-01T23:00"):
        expect(
            calls_by_hour.get(label) == 1,
            f"{label} 这一格应有 1 次调用，实际 {calls_by_hour.get(label)}",
        )

    total = sum(calls_by_hour.values())
    expect(
        total == 4 and body["summary"]["calls"] == 4,
        f"区间共 4 次调用，桶内合计 {total}、汇总 {body['summary']['calls']}",
    )
    expect(
        sum(1 for calls in calls_by_hour.values() if calls == 0) == 20,
        "没有调用的小时必须补 0 而不是缺失",
    )

    costs = stats(
        token, pid, "costs", start_date=start, end_date=end, tz="UTC", granularity="hour"
    )
    expect(
        [p["date"] for p in costs["series"]] == [p["date"] for p in series],
        "costs 与 tokens 的小时标签必须完全一致",
    )


def check_hourly_timezone() -> None:
    """同一条记录在不同时区落到不同的小时桶，且只落一个。"""
    token = register_and_login("iris")
    created = new_project(token, "小时时区")
    pid, key = created["id"], created["api_key"]

    start, end = days_span(1)
    # 16:30Z：UTC 下是 09-01 的 16 点，+08:00 下是 09-02 的 0 点
    seed_logs(key, [{"timestamp": (start + timedelta(hours=16, minutes=30)).isoformat()}])

    def hourly_calls(tz: str) -> dict[str, int]:
        body = stats(
            token, pid, "tokens", start_date=start, end_date=end, tz=tz, granularity="hour"
        )
        return {p["date"]: p["calls"] for p in body["series"]}

    utc, shanghai = hourly_calls("UTC"), hourly_calls("Asia/Shanghai")

    expect(utc.get("2026-09-01T16:00") == 1, f"UTC 下应落在 16 点桶：{utc.get('2026-09-01T16:00')}")
    expect(utc.get("2026-09-02T00:00", 0) == 0, "UTC 下不该出现次日 0 点的桶")
    expect(
        shanghai.get("2026-09-02T00:00") == 1,
        f"+08:00 下应落在次日 0 点桶：{shanghai.get('2026-09-02T00:00')}",
    )
    expect(shanghai.get("2026-09-01T16:00", 0) == 0, "+08:00 下 16 点不该有数据")


def check_granularity_span() -> None:
    """小时粒度的跨度上限，以及它不会影响 day 的十年上限。"""
    token = register_and_login("juno")
    created = new_project(token, "粒度跨度")
    pid = created["id"]

    start, _ = days_span(1)

    def probe(span: timedelta, granularity: str):
        return client.get(
            f"/api/v1/projects/{pid}/stats/tokens",
            headers=auth(token),
            params={
                "start_date": start.isoformat(),
                "end_date": (start + span).isoformat(),
                "tz": "UTC",
                "granularity": granularity,
            },
        )

    ok = probe(timedelta(days=1), "hour")
    expect(ok.status_code == 200, f"正好 1 天的 hour 区间应放行，实际 {ok.status_code}：{ok.text}")
    expect(len(ok.json()["series"]) == 24, "1 天的 hour 区间应给出 24 个点")

    # 25 小时不是笔误：夏令时秋令退那天，本地日历日就是 25 小时
    expect(
        probe(timedelta(days=1, hours=1), "hour").status_code == 200,
        "25 小时的 hour 区间应放行 —— 夏令时那天的「今日」正好是 25 小时",
    )
    r = probe(timedelta(days=1, hours=2), "hour")
    expect(r.status_code == 422, f"26 小时的 hour 区间应 422，实际 {r.status_code}")

    expect(
        probe(timedelta(days=3660), "day").status_code == 200,
        "day 的十年上限不该被 hour 的分支影响",
    )
    expect(probe(timedelta(days=3661), "day").status_code == 422, "day 超过十年仍应 422")


def check_granularity_default() -> None:
    """不传 granularity 等同于 day，与这次改动之前逐点一致；非法值一律 422。"""
    token = register_and_login("karl")
    created = new_project(token, "粒度默认")
    pid, key = created["id"], created["api_key"]

    start, end = days_span(3)
    seed_logs(key, [{"timestamp": (start + timedelta(hours=1)).isoformat()}])

    implicit = stats(token, pid, "tokens", start_date=start, end_date=end, tz="UTC")
    explicit = stats(
        token, pid, "tokens", start_date=start, end_date=end, tz="UTC", granularity="day"
    )
    expect(implicit == explicit, "不传 granularity 与显式传 day 的结果必须一致")
    expect(
        [p["date"] for p in implicit["series"]] == ["2026-09-01", "2026-09-02", "2026-09-03"],
        f"默认仍应按天：{[p['date'] for p in implicit['series']]}",
    )

    for bad in ("minute", "DAY", "", "Hour", "1h"):
        r = client.get(
            f"/api/v1/projects/{pid}/stats/tokens",
            headers=auth(token),
            params={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "granularity": bad,
            },
        )
        expect(
            r.status_code == 422,
            f"granularity={bad!r} 应 422，实际 {r.status_code}：{r.text}",
        )


CHECKS = [
    ("概览：区间汇总 + 环比是上一个等长区间", check_overview_summary),
    ("区间左闭右开：落在 end 那一刻的记录不算", check_period_boundary),
    ("Token 统计：汇总 + 按天序列 + 缺失日期补零", check_token_series_gapfill),
    ("成本统计：汇总与序列口径一致", check_cost_series),
    ("模型分布：分组 / 占比 / 未收录模型成本为 0", check_model_breakdown),
    ("平均响应只算成功，全失败时为 null", check_avg_latency_null),
    ("空项目：0 与 null 的边界 + 趋势仍补零", check_empty_project),
    ("时区分桶：同一条记录在 UTC 与 +08:00 落到不同的天", check_timezone_bucketing),
    ("★ 用户隔离：跨项目 404、跨用户 404、未登录 401", check_isolation),
    ("边界校验（422 而不是 500）", check_validation),
    ("时间参数：不带时区按 UTC 解释（与上报一致）", check_timezone_param_not_shifted),
    ("小时粒度：24 个点 + 桶内有数 + 空小时补零", check_hourly_series),
    ("小时粒度：时区分桶（同一条记录落不同的桶）", check_hourly_timezone),
    ("粒度跨度：hour 上限 1 天（含夏令时余量），day 仍是十年", check_granularity_span),
    ("粒度默认值：不传等于 day，非法值一律 422", check_granularity_default),
]


def main() -> int:
    print("=" * 62)
    print("AI Monitor — Token / Cost 统计验收（阶段 7）")
    print("=" * 62)
    print(f"测试库：{mask_password(TEST_DATABASE_URL)}")
    print()

    try:
        reset_db()
    except OperationalError as exc:
        print("❌ 连不上测试库\n")
        print(f"原始错误：{exc.orig}\n")
        return 1
    except ProgrammingError as exc:
        print("❌ 测试库里还没有 models / llm_requests 表\n")
        print(f"原始错误：{exc.orig}\n")
        print("把建表脚本跑在测试库上（001 和 002 都要）：")
        print("   psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 \\")
        print("     -f sql/002_models_and_llm_requests.sql")
        return 1

    failures: list[str] = []

    for name, check in CHECKS:
        reset_db()  # 每项检查都从空库 + 内置模型开始
        print(f"▸ {name}")
        try:
            check()
        except AssertionError as exc:
            print(f"   ❌ {exc}")
            failures.append(name)
        except Exception as exc:  # noqa: BLE001
            print(f"   ❌ 意外错误：{type(exc).__name__}: {exc}")
            failures.append(name)
        else:
            print("   ✓ 通过")
        print()

    # 撤掉依赖覆盖，别影响同进程里后续的代码
    app.dependency_overrides.clear()
    reset_db()

    print("=" * 62)
    if failures:
        print(f"❌ {len(failures)}/{len(CHECKS)} 项未通过：")
        for name in failures:
            print(f"     · {name}")
        return 1

    print(f"全部通过 ✅ {len(CHECKS)}/{len(CHECKS)}")
    print("阶段 7 Token / Cost 统计完成 —— 概览、趋势、成本与模型分布都已就位")
    return 0


if __name__ == "__main__":
    sys.exit(main())
