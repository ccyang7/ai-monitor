"""接口验收：用真实 HTTP 请求跑完阶段 9（异常分析）的全部验收清单。"""

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

# 基线取「现在往前 7 天」，所以数据必须相对当前时刻造 —— 写成固定日期的话，
# 只要那个日期早于 7 天前，基线就永远是空的，测出来的「零误报」其实是「什么都没判」
NOW = datetime.now(timezone.utc)

# 判定期刻意排在基线窗口之外（NOW-14d ~ NOW-8d 对 NOW-7d ~ NOW）：两段不重叠，
# 基线里不会混进被判定的样本，断言才能写死数字。设计 05 §5.3 提到的「自我稀释」
# 正是这么绕开的 —— 顺带也验证了「判定期与基线期不重叠」这条路是通的
PERIOD_START = NOW - timedelta(days=14)
PERIOD_END = NOW - timedelta(days=8)

# 落在基线窗口正中的一刻
BASELINE_AT = NOW - timedelta(days=3)

# 基线样本统一 1000 token / 1000ms，于是 Token 阈值 = 5000、延迟阈值 = 3000
BASE_TOKENS = 1000
BASE_LATENCY = 1000


def mask_password(url: str) -> str:
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", url)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_close(actual: float, expected: float, message: str) -> None:
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
        "output_tokens": 200,
        "latency": 1000,
        "status": "success",
    }
    payload.update(overrides)
    return client.post("/api/v1/logs", headers={"X-API-Key": key}, json=payload)


def seed_logs(key: str, rows: list[dict]) -> list[int]:
    ids = []
    for row in rows:
        r = ingest(key, **row)
        expect(r.status_code == 201, f"造数据上报失败 {r.status_code}：{r.text}")
        ids.append(r.json()["id"])
    return ids


def _call(
    tokens: int,
    latency: int,
    at: datetime,
    status: str = "success",
    model: str = "deepseek-chat",
) -> dict:
    """一次调用。token 数按输入输出对半分，让总数精确等于传入值。"""
    row = {
        "timestamp": at.isoformat(),
        "model": model,
        "input_tokens": tokens // 2,
        "output_tokens": tokens - tokens // 2,
        "latency": latency,
        "status": status,
    }
    if status == "error":
        row["output"] = None
        row["error"] = "Request timeout after 30s"
    return row


def seed_baseline(
    key: str, calls: int = 10, tokens: int = BASE_TOKENS, latency: int = BASE_LATENCY
) -> None:
    """造一批一模一样的成功调用，让基线恰好等于传入的那两个数。"""
    seed_logs(key, [_call(tokens, latency, BASELINE_AT) for _ in range(calls)])


def anomalies(token: str, project_id: int, **params) -> dict:
    # httpx 不会替我们把 datetime 转成 URL 能用的格式，先在这里转好
    clean = {
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in params.items()
        if v is not None
    }
    r = client.get(
        f"/api/v1/projects/{project_id}/anomalies",
        headers=auth(token),
        params=clean,
    )
    expect(r.status_code == 200, f"异常列表应返回 200，实际 {r.status_code}：{r.text}")
    return r.json()


def make_project(token: str, name: str) -> tuple[int, str]:
    created = new_project(token, name)
    return created["id"], created["api_key"]


def check_token_anomaly() -> None:
    """超基线 5 倍的 Token 被检出；正好等于基线的那条不能误报。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "Token 异常")

    seed_baseline(key)
    ids = seed_logs(
        key,
        [
            _call(BASE_TOKENS, BASE_LATENCY, PERIOD_START + timedelta(hours=1)),
            # 6000 / 1000 = 6 倍，刚过 5 倍的线
            _call(6000, BASE_LATENCY, PERIOD_START + timedelta(hours=2)),
        ],
    )

    body = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)

    expect(
        body["baseline_available"] is True,
        "基线样本 10 条，baseline_available 应为 true",
    )
    expect(
        body["insufficient_baselines"] == [],
        f"样本够的模型不该出现在「样本不足」清单里：{body['insufficient_baselines']}",
    )
    expect(body["total"] == 1, f"只该有 1 条异常，实际 {body['total']}：{body['items']}")

    item = body["items"][0]
    expect(item["type"] == "token", f"类型应为 token，实际 {item['type']}")
    expect(item["log_id"] == ids[1], f"应指向第二条日志 {ids[1]}，实际 {item['log_id']}")
    expect(item["model"] == "deepseek-chat", f"模型名不对：{item['model']}")
    expect(item["value"] == 6000, f"实际值应为 6000，实际 {item['value']}")
    expect(item["baseline"] == 1000.0, f"基线应为 1000，实际 {item['baseline']}")
    expect(item["ratio"] == 6.0, f"倍数应为 6.0，实际 {item['ratio']}")
    expect(item["window"] is None, "单条判定不该带 window")
    expect(item["error_message"] is None, "成功的调用不该带错误信息")


def check_latency_anomaly() -> None:
    """超基线 3 倍的延迟被检出；2.5 倍在阈值内，不能误报。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "延迟异常")

    seed_baseline(key)
    ids = seed_logs(
        key,
        [
            # 2.5 倍：故意卡在 3 倍阈值之下，验「没超就不算」
            _call(BASE_TOKENS, 2500, PERIOD_START + timedelta(hours=1)),
            _call(BASE_TOKENS, 4000, PERIOD_START + timedelta(hours=2)),
        ],
    )

    body = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    expect(body["total"] == 1, f"只该有 1 条异常，实际 {body['total']}：{body['items']}")

    item = body["items"][0]
    expect(item["type"] == "latency", f"类型应为 latency，实际 {item['type']}")
    expect(item["log_id"] == ids[1], f"应指向第二条日志 {ids[1]}，实际 {item['log_id']}")
    expect(item["value"] == 4000, f"实际值应为 4000，实际 {item['value']}")
    expect(item["ratio"] == 4.0, f"倍数应为 4.0，实际 {item['ratio']}")


def check_both_types_on_one_record() -> None:
    """一次调用同时超两类阈值时，应该出两条异常、指向同一条日志。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "两类都超")

    seed_baseline(key)
    ids = seed_logs(key, [_call(6000, 4000, PERIOD_START + timedelta(hours=1))])

    body = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    expect(body["total"] == 2, f"应出 2 条异常，实际 {body['total']}：{body['items']}")

    expect(
        {item["type"] for item in body["items"]} == {"token", "latency"},
        f"两条的类型应分别是 token 和 latency：{[i['type'] for i in body['items']]}",
    )
    expect(
        {item["log_id"] for item in body["items"]} == {ids[0]},
        "两条异常应指向同一条日志",
    )


def check_error_calls() -> None:
    """失败调用逐条列出，带错误信息；它们不做阈值判定，所以数值字段全是空。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "失败调用")

    seed_baseline(key)
    ids = seed_logs(
        key,
        [
            _call(BASE_TOKENS, BASE_LATENCY, PERIOD_START + timedelta(hours=1), "error"),
            _call(BASE_TOKENS, BASE_LATENCY, PERIOD_START + timedelta(hours=2), "error"),
        ],
    )

    body = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    expect(body["total"] == 2, f"应检出 2 条失败，实际 {body['total']}：{body['items']}")

    for item, log_id in zip(body["items"], reversed(ids)):
        expect(item["type"] == "error", f"类型应为 error，实际 {item['type']}")
        expect(item["log_id"] == log_id, f"日志 id 不对：{item['log_id']}")
        expect(
            item["error_message"] == "Request timeout after 30s",
            f"错误信息应原样带出：{item['error_message']}",
        )
        expect(
            item["value"] is None and item["baseline"] is None and item["ratio"] is None,
            "失败调用没有阈值判定，三个数值字段都该是空的",
        )


def check_error_rate_window() -> None:
    """窗口错误率：同一天 12 次调用里错 4 次 = 33% > 20%，记为一条窗口异常。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "错误率")

    # 挑一个整点再造数据：同一天的 12 条不能跨过午夜，否则会被切成两个窗口
    base = (PERIOD_START + timedelta(days=1)).replace(
        hour=2, minute=0, second=0, microsecond=0
    )
    seed_logs(
        key,
        [
            _call(
                BASE_TOKENS,
                BASE_LATENCY,
                base + timedelta(minutes=i),
                "error" if i < 4 else "success",
            )
            for i in range(12)
        ],
    )

    body = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    rate_items = [item for item in body["items"] if item["type"] == "error_rate"]
    expect(len(rate_items) == 1, f"该有 1 条窗口异常，实际 {len(rate_items)}")

    item = rate_items[0]
    expect(item["log_id"] is None, "窗口异常没有对应的日志，log_id 该是 null")
    expect(item["window"] == base.date().isoformat(), f"窗口标签不对：{item['window']}")
    expect(
        item["value"] == 0.3333,
        f"错误率应为 4/12 = 0.3333，实际 {item['value']}",
    )
    expect(item["baseline"] == 0.2, f"参考值该是阈值 0.2，实际 {item['baseline']}")
    expect(item["ratio"] == 1.7, f"应为阈值的 1.7 倍，实际 {item['ratio']}")

    # 那 4 条失败同时各算一条单条异常
    expect(
        body["total"] == 5,
        f"总数应为 4 条失败 + 1 条窗口 = 5，实际 {body['total']}",
    )


def check_error_rate_not_triggered() -> None:
    """两个方向都要验：错误率没超阈值、以及窗口样本太少，都不该报警。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "错误率不报警")

    quiet = (PERIOD_START + timedelta(days=1)).replace(
        hour=2, minute=0, second=0, microsecond=0
    )
    # 12 条里错 2 条 = 16.7%，低于 20%
    seed_logs(
        key,
        [
            _call(
                BASE_TOKENS,
                BASE_LATENCY,
                quiet + timedelta(minutes=i),
                "error" if i < 2 else "success",
            )
            for i in range(12)
        ],
    )

    tiny = (PERIOD_START + timedelta(days=3)).replace(
        hour=2, minute=0, second=0, microsecond=0
    )
    # 5 条里错 3 条 = 60%，但窗口只有 5 条 —— 样本太少，比例再高也不作数
    seed_logs(
        key,
        [
            _call(
                BASE_TOKENS,
                BASE_LATENCY,
                tiny + timedelta(minutes=i),
                "error" if i < 3 else "success",
            )
            for i in range(5)
        ],
    )

    body = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    expect(
        [item for item in body["items"] if item["type"] == "error_rate"] == [],
        f"两种情况都不该出窗口异常：{body['items']}",
    )
    # 单条失败照常列出（2 + 3 条），验证「窗口不报警」不等于「失败消失」
    expect(body["total"] == 5, f"单条失败应有 5 条，实际 {body['total']}")


def check_insufficient_samples() -> None:
    """样本不足时 Token / 延迟不判定，但失败调用不受影响 —— 它不依赖基线。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "样本不足")

    # 9 条，差一条到门槛
    seed_baseline(key, calls=9)
    seed_logs(
        key,
        [
            # 10000 / 1000 = 10 倍，远超 5 倍 —— 但基线本身不作数，它就不该被报出来
            _call(10000, BASE_LATENCY, PERIOD_START + timedelta(hours=1)),
            _call(BASE_TOKENS, BASE_LATENCY, PERIOD_START + timedelta(hours=2), "error"),
        ],
    )

    body = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    expect(
        body["baseline_available"] is False,
        "只有 9 条样本，baseline_available 应为 false",
    )
    expect(body["total"] == 1, f"只该有那 1 条失败，实际 {body['total']}：{body['items']}")
    expect(body["items"][0]["type"] == "error", "检出的应该是失败调用")

    # 光说「样本不足」没有用，得说清楚还差多少 —— 前端要拿它渲染
    # 「再积累 1 次调用后开始判定」
    expect(
        body["insufficient_baselines"] == [
            {"model": "deepseek-chat", "samples": 9, "required": 10}
        ],
        f"该点名报出 deepseek-chat 有 9 条样本，实际 {body['insufficient_baselines']}",
    )


def check_mixed_baseline_state() -> None:
    """一个模型样本够、另一个不够：判定照常进行，但要把落后的那个如实报出来。

    这正是光有布尔值不够用的场景 —— baseline_available 是 true，界面看着一切正常，
    可 gpt-4o 那一路其实还没开始判定，不说出来用户永远不知道。
    """
    token = register_and_login("alice")
    pid, key = make_project(token, "半数是够的")

    seed_baseline(key)  # deepseek-chat 攒满 10 条
    seed_logs(
        key,
        [
            _call(BASE_TOKENS, BASE_LATENCY, BASELINE_AT, model="gpt-4o")
            for _ in range(3)
        ],
    )
    seed_logs(key, [_call(6000, BASE_LATENCY, PERIOD_START + timedelta(hours=1))])

    body = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    expect(
        body["baseline_available"] is True,
        "deepseek-chat 样本够，baseline_available 应为 true",
    )
    expect(body["total"] == 1, f"那条 Token 异常照常检出，实际 {body['total']}")
    expect(
        body["insufficient_baselines"] == [
            {"model": "gpt-4o", "samples": 3, "required": 10}
        ],
        f"该报出 gpt-4o 差 7 条，实际 {body['insufficient_baselines']}",
    )


def check_zero_baseline() -> None:
    """基线是 0 时跳过该类判定：0 的任意倍还是 0，硬比就是除零。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "零基线")

    seed_baseline(key, tokens=0, latency=0)
    seed_logs(key, [_call(5000, 5000, PERIOD_START + timedelta(hours=1))])

    body = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    expect(body["total"] == 0, f"零基线不该判出异常，实际 {body['total']}：{body['items']}")
    expect(body["baseline_available"] is True, "样本够 10 条，基线本身是可用的")


def check_type_filter() -> None:
    """type 参数只留指定的一类，其它一律不出。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "类型筛选")

    seed_baseline(key)
    seed_logs(
        key,
        [
            _call(6000, BASE_LATENCY, PERIOD_START + timedelta(hours=1)),
            _call(BASE_TOKENS, 4000, PERIOD_START + timedelta(hours=2)),
            _call(BASE_TOKENS, BASE_LATENCY, PERIOD_START + timedelta(hours=3), "error"),
        ],
    )

    all_items = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    expect(all_items["total"] == 3, f"不筛时应是 3 条，实际 {all_items['total']}")

    for kind in ("token", "latency", "error"):
        body = anomalies(
            token, pid, start_date=PERIOD_START, end_date=PERIOD_END, type=kind
        )
        expect(body["total"] == 1, f"筛 {kind} 应剩 1 条，实际 {body['total']}")
        expect(body["items"][0]["type"] == kind, f"筛 {kind} 却出了别的类型")
        # 总数是筛选前的口径还是筛选后的？这里是筛选后的 —— 前端要靠它算页数
        expect(body["page"] == 1 and body["page_size"] == 20, "分页字段该有默认值")


def check_pagination() -> None:
    """翻页不重不漏，越界返回空页而不是报错。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "分页")

    base = (PERIOD_START + timedelta(days=1)).replace(
        hour=1, minute=0, second=0, microsecond=0
    )
    ids = seed_logs(
        key,
        [
            _call(BASE_TOKENS, BASE_LATENCY, base + timedelta(minutes=i), "error")
            for i in range(25)
        ],
    )

    # 只翻 error 这一类的页：这 25 条全失败，那天的错误率是 100%，本身还会多出一条
    # 窗口异常。混在一起翻的话第 3 页会多出个 log_id 为 null 的条目，
    # 「不重不漏」就没法用 id 集合来断言了
    page_params = {
        "start_date": PERIOD_START,
        "end_date": PERIOD_END,
        "type": "error",
        "page_size": 10,
    }

    seen: list[int] = []
    for page in (1, 2, 3):
        body = anomalies(token, pid, page=page, **page_params)
        expect(body["total"] == 25, f"第 {page} 页的 total 应恒为 25，实际 {body['total']}")
        expect(body["page"] == page, f"页码回显不对：{body['page']}")
        seen.extend(item["log_id"] for item in body["items"])

    expect(len(seen) == 25, f"三页共应取回 25 条，实际 {len(seen)}")
    expect(set(seen) == set(ids), "翻页出现了重复或漏掉的记录")

    empty = anomalies(token, pid, page=99, **page_params)
    expect(empty["items"] == [], "越界页该是空列表")
    expect(empty["total"] == 25, "越界页的 total 仍应是总数")


def check_overview_count_matches() -> None:
    """概览页的异常数必须和异常列表的总数一致 —— 两边口径不一致时用户会以为其中一个算错了。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "口径一致")

    seed_baseline(key)
    seed_logs(
        key,
        [
            _call(6000, BASE_LATENCY, PERIOD_START + timedelta(hours=1)),
            _call(BASE_TOKENS, 4000, PERIOD_START + timedelta(hours=2)),
            _call(BASE_TOKENS, BASE_LATENCY, PERIOD_START + timedelta(hours=3), "error"),
        ],
    )

    listed = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    r = client.get(
        f"/api/v1/projects/{pid}/stats/overview",
        headers=auth(token),
        params={
            "start_date": PERIOD_START.isoformat(),
            "end_date": PERIOD_END.isoformat(),
        },
    )
    expect(r.status_code == 200, f"概览应返回 200，实际 {r.status_code}：{r.text}")

    counts = r.json()["anomaly_counts"]
    expect(
        set(counts.keys()) == {"token", "latency", "error", "error_rate"},
        f"anomaly_counts 字段变了：{sorted(counts.keys())}",
    )
    # 逐类比，而不是只比总数：四格加起来对得上、但分错了类型（比如把延迟算成 Token），
    # 只比总数是查不出来的 —— 而前端正是拿分类型的那一格去挂对应统计卡的角标
    for kind in counts:
        filtered = anomalies(
            token, pid, start_date=PERIOD_START, end_date=PERIOD_END, type=kind
        )
        expect(
            counts[kind] == filtered["total"],
            f"{kind} 类：概览 {counts[kind]} 条 vs 列表 {filtered['total']} 条，对不上",
        )

    expect(
        sum(counts.values()) == listed["total"],
        f"四类合计 {sum(counts.values())} 条 vs 列表总数 {listed['total']} 条，对不上",
    )
    # seed_baseline 攒够了样本，两边都该报基线可用 —— 简报条要靠它决定说
    # 「没有发现异常」还是「数据还在积累」
    expect(
        r.json()["baseline_available"] is True,
        f"概览没有跟着列表报基线可用：{r.json()['baseline_available']!r}",
    )


def check_timezone_window() -> None:
    """窗口按本地日历天分组：同一批记录在 UTC 和 +08:00 下落在不同的日子。"""
    token = register_and_login("alice")
    pid, key = make_project(token, "窗口时区")

    # UTC 16:00 起、每秒一条 —— UTC 下是当天，+08:00 下已经是第二天凌晨
    base = (PERIOD_START + timedelta(days=1)).replace(
        hour=16, minute=0, second=0, microsecond=0
    )
    seed_logs(
        key,
        [
            _call(
                BASE_TOKENS,
                BASE_LATENCY,
                base + timedelta(seconds=i),
                "error" if i < 4 else "success",
            )
            for i in range(12)
        ],
    )

    utc = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    utc_windows = [i["window"] for i in utc["items"] if i["type"] == "error_rate"]
    expect(utc_windows == [base.date().isoformat()], f"UTC 窗口不对：{utc_windows}")

    shanghai = anomalies(
        token, pid, start_date=PERIOD_START, end_date=PERIOD_END, tz="Asia/Shanghai"
    )
    sh_windows = [i["window"] for i in shanghai["items"] if i["type"] == "error_rate"]
    expect(
        sh_windows == [(base + timedelta(hours=8)).date().isoformat()],
        f"+08:00 的窗口该落到第二天：{sh_windows}",
    )


def check_empty_project() -> None:
    """空项目：0 条 + 基线不可用，且不能报错。"""
    token = register_and_login("alice")
    pid, _key = make_project(token, "空项目")

    body = anomalies(token, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    expect(body["total"] == 0, f"空项目应是 0 条，实际 {body['total']}")
    expect(body["items"] == [], "空项目该是空列表")
    expect(body["baseline_available"] is False, "没数据时基线不可用")
    # 一个模型都没有，就不该凭空冒出「还差 N 次」的提示
    expect(body["insufficient_baselines"] == [], "没有任何模型时该是空清单")


def check_isolation() -> None:
    """★ 用户隔离：别人的项目、不存在的项目、没登录，一律 404 / 401。"""
    alice = register_and_login("alice")
    bob = register_and_login("bob")
    pid, key = make_project(alice, "Alice 的项目")
    seed_baseline(key)
    seed_logs(key, [_call(6000, BASE_LATENCY, PERIOD_START + timedelta(hours=1))])

    params = {
        "start_date": PERIOD_START.isoformat(),
        "end_date": PERIOD_END.isoformat(),
    }

    # Alice 自己看得到
    mine = anomalies(alice, pid, start_date=PERIOD_START, end_date=PERIOD_END)
    expect(mine["total"] == 1, f"本人应看到 1 条，实际 {mine['total']}")

    # Bob 看不到 Alice 的项目 —— 404 而不是 403，403 等于确认「这个 ID 存在」
    r = client.get(f"/api/v1/projects/{pid}/anomalies", headers=auth(bob), params=params)
    expect(r.status_code == 404, f"越权应 404，实际 {r.status_code}：{r.text}")

    # 不存在的项目同样 404，与越权无法区分
    r = client.get("/api/v1/projects/999999/anomalies", headers=auth(alice), params=params)
    expect(r.status_code == 404, f"不存在的项目应 404，实际 {r.status_code}")

    # 未登录 401
    r = client.get(f"/api/v1/projects/{pid}/anomalies", params=params)
    expect(r.status_code == 401, f"未登录应 401，实际 {r.status_code}")


def check_validation() -> None:
    """参数非法一律 422，不能漏成 500。"""
    token = register_and_login("alice")
    pid, _key = make_project(token, "参数校验")

    good = {
        "start_date": PERIOD_START.isoformat(),
        "end_date": PERIOD_END.isoformat(),
    }
    cases = [
        ("起止颠倒", {"start_date": PERIOD_END.isoformat(), "end_date": PERIOD_START.isoformat()}),
        ("起止相同", {"start_date": PERIOD_START.isoformat(), "end_date": PERIOD_START.isoformat()}),
        ("缺 start_date", {"end_date": PERIOD_END.isoformat()}),
        ("非法时区", {**good, "tz": "Mars/Olympus"}),
        ("非法类型", {**good, "type": "slow"}),
        ("page 从 0 开始", {**good, "page": 0}),
        ("page_size 为 0", {**good, "page_size": 0}),
        ("page_size 超上限", {**good, "page_size": 101}),
    ]

    for name, params in cases:
        r = client.get(
            f"/api/v1/projects/{pid}/anomalies", headers=auth(token), params=params
        )
        expect(r.status_code == 422, f"{name} 应 422，实际 {r.status_code}：{r.text}")


CHECKS = [
    ("Token 异常：超基线 5 倍检出，等于基线的不误报", check_token_anomaly),
    ("延迟异常：超基线 3 倍检出，2.5 倍不误报", check_latency_anomaly),
    ("一次调用同时超两类阈值 → 两条异常指向同一条日志", check_both_types_on_one_record),
    ("失败调用：逐条列出并带错误信息，数值字段为空", check_error_calls),
    ("窗口错误率：12 次里错 4 次判为异常", check_error_rate_window),
    ("窗口错误率不报警：比例没超阈值 / 窗口样本太少", check_error_rate_not_triggered),
    ("样本不足：Token 与延迟不判定，失败调用照常检出，并点名还差几次", check_insufficient_samples),
    ("一半够一半不够：判定照常，落下的模型如实报出", check_mixed_baseline_state),
    ("零基线：跳过该类判定而不是除零", check_zero_baseline),
    ("类型筛选：只留指定的一类", check_type_filter),
    ("分页：翻页不重不漏，越界返回空页", check_pagination),
    ("概览的 anomaly_counts 逐类与异常列表对得上", check_overview_count_matches),
    ("窗口时区：同一批记录在 UTC 与 +08:00 落到不同的天", check_timezone_window),
    ("空项目：0 条 + 基线不可用", check_empty_project),
    ("★ 用户隔离：跨项目 404、不存在 404、未登录 401", check_isolation),
    ("参数校验（422 而不是 500）", check_validation),
]


def main() -> int:
    print("=" * 62)
    print("AI Monitor — 异常分析验收（阶段 9）")
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
    print("阶段 9 异常分析完成 —— 基线判定、失败调用与窗口错误率都已就位")
    return 0


if __name__ == "__main__":
    sys.exit(main())
