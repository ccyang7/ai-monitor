"""工具层验收：用手写参数直接调用八个 AI 工具，重点验证用户隔离与边界处理。"""

import inspect
import json
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
from langchain.tools import ToolRuntime  # noqa: E402
from langchain_core.runnables import RunnableConfig  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import OperationalError, ProgrammingError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.ai.tools as ai_tools  # noqa: E402
from app.ai.context import AssistantContext  # noqa: E402
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

# 工具不走 FastAPI 的依赖注入（它们由 Agent 在进程内直接调用），dependency_overrides
# 够不着 —— 把工具模块里的会话工厂整个换成测试库的，工具查的才是测试库
ai_tools.SessionLocal = TestSession


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

# 每次成功调用的固定成本：100 输入 * 1 元/百万 + 200 输出 * 2 元/百万。
# 测试内置模型没配 cache_hit_price（NULL），命中的 40 个 token 退回按输入价算 ——
# 成本与改动前逐分钱一致，所以这个常量不跟着变（两档价公式由 check_ingest.py 专门验证）
CHAT_COST = 0.0005

# 联网搜索的上限写死在验收里（不引 ai/tools.py 的常量）：哪天有人把上限改成 8 条，
# 这里必须先红一次，而不是跟着实现一起悄悄变
MAX_EXPECTED_RESULTS = 5
MAX_EXPECTED_SNIPPET = 1000

# 数据都种在 9 月：落在「近 7 天」基线窗口之外，异常判定结果与跑脚本的日期无关
DAY = datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)
DAY_STR = "2026-09-10"


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


def post_model(token: str, **overrides) -> dict:
    payload = {
        "provider": "deepseek",
        "model_name": "自建模型",
        "input_price": "1.5",
        "output_price": "3",
    }
    payload.update(overrides)
    r = client.post("/api/v1/models", headers=auth(token), json=payload)
    expect(r.status_code == 201, f"创建模型应返回 201，实际 {r.status_code}：{r.text}")
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


def seed_logs(key: str, rows: list[dict]) -> None:
    for row in rows:
        r = ingest(key, **row)
        expect(r.status_code == 201, f"造数据上报失败 {r.status_code}：{r.text}")


def a_user(username: str) -> tuple[int, str]:
    """注册用户并拿到 (user_id, token) —— 工具上下文要的正是这个 user_id。"""
    token = register_and_login(username)
    r = client.get("/api/v1/users/me", headers=auth(token))
    expect(r.status_code == 200, f"/users/me 应返回 200，实际 {r.status_code}：{r.text}")
    return r.json()["id"], token


def ctx_for(user_id: int) -> AssistantContext:
    return AssistantContext(user_id=user_id, timezone=settings.assistant_timezone)


def call_tool(tool_obj, ctx: AssistantContext, **kwargs):
    # 不走 .invoke()：ToolRuntime 由 Agent 运行时注入，纯 Python 调用得自己造一个；
    # .func 就是 @tool 包装之前的那份原始函数
    runtime = ToolRuntime(
        state={},
        context=ctx,
        config=RunnableConfig(),
        stream_writer=lambda _: None,
        tool_call_id="check",
        store=None,
        tools=[],
    )
    # search_web 签名里没有 ToolRuntime（它不碰平台数据，没有要注入的用户身份），
    # 给它硬塞 runtime= 会 TypeError
    if "runtime" in inspect.signature(tool_obj.func).parameters:
        kwargs["runtime"] = runtime
    return tool_obj.func(**kwargs)


def check_own_project_data() -> None:
    """用例 1：A 查自己的项目，数字与手工上报的数据对得上。"""
    uid, token = a_user("alice")
    project = new_project(token, "问答助手")
    seed_logs(
        project["api_key"],
        [
            {"timestamp": (DAY + timedelta(hours=2)).isoformat()},
            {"timestamp": (DAY + timedelta(hours=3)).isoformat()},
        ],
    )
    ctx = ctx_for(uid)

    usage = call_tool(
        ai_tools.get_token_usage, ctx, start_date=DAY_STR, end_date=DAY_STR
    )
    expect(
        set(usage.keys()) == {"问答助手"},
        f"返回应以项目名为 key，实际 {list(usage.keys())}",
    )
    data = usage["问答助手"]
    expect(data["calls"] == 2, f"应有 2 次调用，实际 {data['calls']}")
    expect(data["total_tokens"] == 600, f"Token 应为 600，实际 {data['total_tokens']}")
    expect(
        data["input_tokens"] == 200 and data["output_tokens"] == 400,
        f"输入输出拆分不对：{data}",
    )
    expect(
        data["cache_hit_tokens"] == 80,
        f"两次调用各命中 40，应汇总为 80，实际 {data['cache_hit_tokens']}",
    )

    cost = call_tool(
        ai_tools.get_cost_statistics, ctx, start_date=DAY_STR, end_date=DAY_STR
    )
    expect_close(cost["问答助手"]["estimated_cost_cny"], 2 * CHAT_COST, "两次调用的成本")

    overview = call_tool(
        ai_tools.get_project_overview, ctx, start_date=DAY_STR, end_date=DAY_STR
    )
    body = overview["问答助手"]
    expect(
        body["calls"] == 2 and body["total_tokens"] == 600,
        f"概览汇总应与上报数据一致，实际 {body}",
    )
    expect(
        body["cache_hit_tokens"] == 80 and body["previous"]["cache_hit_tokens"] == 0,
        f"概览应带命中数、环比区间没数据应为 0，实际 {body['cache_hit_tokens']} / "
        f"{body['previous']['cache_hit_tokens']}",
    )
    expect(
        set(body["anomaly_counts"]) == {"token", "latency", "error", "error_rate"},
        f"概览应带四类异常条数，实际 {body['anomaly_counts']}",
    )

    models = call_tool(
        ai_tools.get_model_usage, ctx, start_date=DAY_STR, end_date=DAY_STR
    )
    item = models["问答助手"]["models"][0]
    expect(
        item["model"] == "deepseek-chat" and item["cache_hit_tokens"] == 80,
        f"模型分布应带命中数，实际 {item}",
    )


def check_foreign_project_name() -> None:
    """用例 2：A 传 B 的项目名 —— 报「不存在」，且不透露这个名字在系统里存在。"""
    uid_a, token_a = a_user("alice")
    _, token_b = a_user("bob")
    new_project(token_a, "Alice 的项目")
    b_one = new_project(token_b, "Bob 的项目一")
    new_project(token_b, "Bob 的项目二")

    ctx = ctx_for(uid_a)
    got = call_tool(
        ai_tools.get_token_usage,
        ctx,
        start_date=DAY_STR,
        end_date=DAY_STR,
        project_names=[b_one["name"]],
    )
    expect("error" in got, f"传别人的项目名应返回 error，实际 {got}")

    text = json.dumps(got, ensure_ascii=False)
    expect(
        "Bob 的项目二" not in text,
        "错误信息里出现了别人的项目名 —— 候选清单只允许列当前用户自己的",
    )
    expect(
        "Alice 的项目" in text,
        "错误信息应附带用户自己的项目名清单（模型靠它纠正重试）",
    )

    # 除回显用户输入的那个名字外，两种错误的文案必须一字不差。只要有任何差异，
    # 就等于告诉对方「这个名字在系统里是存在的」—— 和 404 不返回 403 是同一条道理
    ghost_name = "根本不存在的项目"
    ghost = call_tool(
        ai_tools.get_token_usage,
        ctx,
        start_date=DAY_STR,
        end_date=DAY_STR,
        project_names=[ghost_name],
    )
    expect(
        got["error"].replace(b_one["name"], "★")
        == ghost["error"].replace(ghost_name, "★"),
        f"「别人的项目名」与「纯属虚构的名字」的错误文案必须无法区分，"
        f"实际：{got['error']!r} vs {ghost['error']!r}",
    )


def check_all_projects_default() -> None:
    """用例 3：不传项目名 = 只查自己的全部项目，别人的一条都不带。"""
    uid_a, token_a = a_user("alice")
    _, token_b = a_user("bob")
    a_first = new_project(token_a, "Alice 甲")
    new_project(token_a, "Alice 乙")
    b_project = new_project(token_b, "Bob 的项目")
    seed_logs(a_first["api_key"], [{"timestamp": (DAY + timedelta(hours=1)).isoformat()}])
    seed_logs(b_project["api_key"], [{"timestamp": (DAY + timedelta(hours=1)).isoformat()}])

    usage = call_tool(
        ai_tools.get_token_usage, ctx_for(uid_a), start_date=DAY_STR, end_date=DAY_STR
    )
    expect(
        set(usage.keys()) == {"Alice 甲", "Alice 乙"},
        f"不传项目名应只返回自己的项目，实际 {list(usage.keys())}",
    )
    expect(usage["Alice 甲"]["calls"] == 1, "有数据的项目应统计到 1 次")
    expect(
        usage["Alice 乙"]["calls"] == 0,
        "没有数据的项目也要出现在结果里、值为 0（不能凭空消失）",
    )

    # 一个项目都没有的用户：给一句能直接转述的话，而不是空结果
    uid_c, _ = a_user("charlie")
    empty = call_tool(ai_tools.get_token_usage, ctx_for(uid_c), start_date=DAY_STR, end_date=DAY_STR)
    expect(
        "error" in empty and "项目" in empty["error"],
        f"没有任何项目的用户应得到可转述的提示，实际 {empty}",
    )


def check_unknown_project_name() -> None:
    """用例 4：名字对不上时，错误里带完整的候选清单。"""
    uid, token = a_user("alice")
    new_project(token, "Alice 甲")
    new_project(token, "Alice 乙")

    got = call_tool(
        ai_tools.get_token_usage,
        ctx_for(uid),
        start_date=DAY_STR,
        end_date=DAY_STR,
        project_names=["错的名字"],
    )
    expect("error" in got, f"不存在的项目名应返回 error，实际 {got}")
    expect(
        "Alice 甲" in got["error"] and "Alice 乙" in got["error"],
        f"候选清单必须列全（模型靠它对齐简称），实际 {got['error']!r}",
    )


def check_list_projects() -> None:
    """用例 5：list_projects 只列当前用户的项目。"""
    uid_a, token_a = a_user("alice")
    _, token_b = a_user("bob")
    called = new_project(token_a, "有调用的项目")
    new_project(token_a, "没调用的项目")
    new_project(token_b, "Bob 的项目")
    seed_logs(called["api_key"], [{"timestamp": (DAY + timedelta(hours=1)).isoformat()}])

    got = call_tool(ai_tools.list_projects, ctx_for(uid_a))
    by_name = {item["name"]: item for item in got["projects"]}
    expect(
        set(by_name) == {"有调用的项目", "没调用的项目"},
        f"只该列出自己的项目，实际 {list(by_name)}",
    )
    expect(
        by_name["有调用的项目"]["last_called_at"] is not None,
        "有过调用的项目应带最近调用时间",
    )
    expect(
        by_name["没调用的项目"]["last_called_at"] is None,
        "没调用过的项目最近调用时间应是 null",
    )


def check_single_day_range() -> None:
    """用例 6：单日区间按「助手时区的自然日」换算，两端边界都落准。"""
    uid, token = a_user("alice")
    project = new_project(token, "时区边界")
    # 助手时区是 +08:00，本地 09-10 这一天 = UTC 09-09T16:00 ~ 09-10T16:00
    seed_logs(
        project["api_key"],
        [
            # 本地 09-09 23:59 —— 属于前一天，不该被算进来
            {"timestamp": datetime(2026, 9, 9, 15, 59, tzinfo=timezone.utc).isoformat()},
            # 本地 09-10 00:00 —— 正好压在起点上，属于当天（左闭）
            {"timestamp": datetime(2026, 9, 9, 16, 0, tzinfo=timezone.utc).isoformat()},
            # 本地 09-10 10:00 —— 当天正中间
            {"timestamp": datetime(2026, 9, 10, 2, 0, tzinfo=timezone.utc).isoformat()},
            # 本地 09-11 00:00 —— 正好压在终点上，不属于当天（右开）
            {"timestamp": datetime(2026, 9, 10, 16, 0, tzinfo=timezone.utc).isoformat()},
        ],
    )

    usage = call_tool(
        ai_tools.get_token_usage, ctx_for(uid), start_date=DAY_STR, end_date=DAY_STR
    )
    calls = usage["时区边界"]["calls"]
    expect(
        calls == 2,
        f"本地 09-10 一整天应有 2 条（00:00 那条含、次日 00:00 那条不含），"
        f"实际 {calls} —— 日期换算可能没落在助手时区上，或边界写成了闭区间",
    )


def check_bad_dates() -> None:
    """用例 7：日期颠倒 / 格式非法 —— 返回 error dict，不抛异常。"""
    uid, token = a_user("alice")
    new_project(token, "随便")
    ctx = ctx_for(uid)

    cases = [
        ("起止颠倒", {"start_date": "2026-09-10", "end_date": "2026-09-09"}),
        ("中文日期", {"start_date": "昨天", "end_date": DAY_STR}),
        ("不存在的日期", {"start_date": "2026-13-45", "end_date": DAY_STR}),
    ]
    for label, kwargs in cases:
        got = call_tool(ai_tools.get_token_usage, ctx, **kwargs)
        expect("error" in got, f"「{label}」应返回 error dict，实际 {got}")


def check_hour_granularity_span() -> None:
    """补充：按小时跨了好几天 —— 越界参数也要返回 error dict 而不是抛异常。"""
    uid, token = a_user("alice")
    new_project(token, "随便")

    got = call_tool(
        ai_tools.get_token_usage,
        ctx_for(uid),
        start_date="2026-09-10",
        end_date="2026-09-12",
        granularity="hour",
    )
    expect("error" in got, f"按小时跨 3 天应返回 error dict，实际 {got}")
    expect(
        "1 天" in got["error"],
        f"错误里应说清是跨度超限，实际 {got['error']!r}",
    )


def check_empty_project() -> None:
    """用例 8：一点数据都没有的项目 —— 返回 0 / 空列表，不报错。"""
    uid, token = a_user("alice")
    new_project(token, "空项目")
    ctx = ctx_for(uid)

    usage = call_tool(
        ai_tools.get_token_usage, ctx, start_date=DAY_STR, end_date=DAY_STR
    )
    data = usage["空项目"]
    expect(data["calls"] == 0 and data["total_tokens"] == 0, f"空项目应全是 0，实际 {data}")
    expect(len(data["series"]) == 1, f"单日区间应有一个趋势点，实际 {len(data['series'])}")

    logs = call_tool(ai_tools.get_recent_logs, ctx, project_names=["空项目"])
    expect(logs["空项目"]["items"] == [], f"没有日志时应是空列表，实际 {logs}")

    errors = call_tool(ai_tools.get_error_logs, ctx)
    expect(errors["空项目"]["items"] == [], f"没有失败调用时应是空列表，实际 {errors}")

    anomalies = call_tool(
        ai_tools.analyze_anomalies, ctx, start_date=DAY_STR, end_date=DAY_STR
    )
    expect(anomalies["空项目"]["total"] == 0, f"没有异常时应是 0，实际 {anomalies}")


def check_limit_clamp() -> None:
    """用例 9：日志条数上限 —— 传 100 被夹到 20，默认 10 条。"""
    uid, token = a_user("alice")
    project = new_project(token, "日志很多")
    seed_logs(
        project["api_key"],
        [
            {"timestamp": (DAY + timedelta(minutes=i)).isoformat()}
            for i in range(25)
        ]
        # 再补一条最新的失败调用：验证 get_error_logs 的字段形状需要至少一条正例
        + [
            {
                "timestamp": (DAY + timedelta(hours=1)).isoformat(),
                "status": "error",
                "error": "APITimeoutError: Request timed out",
            }
        ],
    )
    ctx = ctx_for(uid)

    many = call_tool(ai_tools.get_recent_logs, ctx, project_names=["日志很多"], limit=100)
    expect(
        len(many["日志很多"]["items"]) == 20,
        f"limit=100 应被夹到 20，实际 {len(many['日志很多']['items'])}",
    )

    default = call_tool(ai_tools.get_recent_logs, ctx, project_names=["日志很多"])
    expect(
        len(default["日志很多"]["items"]) == 10,
        f"不传 limit 应默认 10 条，实际 {len(default['日志很多']['items'])}",
    )

    # 日志摘要里绝不能带请求 / 响应正文：几十 KB 的大文本会把模型上下文撑爆
    first = default["日志很多"]["items"][0]
    expect(
        set(first)
        == {
            "time",
            "model",
            "total_tokens",
            "cache_hit_tokens",
            "latency_ms",
            "status",
        },
        f"日志摘要字段变了：{sorted(first)}",
    )
    expect(
        first["cache_hit_tokens"] == 40,
        f"日志摘要应带命中数（默认载荷每条命中 40），实际 {first['cache_hit_tokens']}",
    )

    errs = call_tool(ai_tools.get_error_logs, ctx, project_names=["日志很多"])
    err_first = errs["日志很多"]["items"][0]
    expect(
        set(err_first)
        == {
            "time",
            "model",
            "total_tokens",
            "cache_hit_tokens",
            "latency_ms",
            "status",
            "error_message",
        },
        f"失败日志字段变了：{sorted(err_first)}",
    )
    expect(
        err_first["cache_hit_tokens"] == 40 and err_first["status"] == "error",
        f"失败日志应带命中数且只取失败调用，实际 {err_first}",
    )


def check_list_models() -> None:
    """用例 10：list_models 列内置 + 自己的自建，别人的自建一条都不带。"""
    uid_a, token_a = a_user("alice")
    _, token_b = a_user("bob")
    post_model(token_a, model_name="alice-自建", cache_hit_price="0.2")
    post_model(token_b, model_name="bob-自建")

    project = new_project(token_a, "Alice 项目")
    seed_logs(project["api_key"], [{"timestamp": (DAY + timedelta(hours=1)).isoformat()}])

    got = call_tool(ai_tools.list_models, ctx_for(uid_a))
    by_name = {item["model_name"]: item for item in got["models"]}
    expect(
        set(by_name) == {"deepseek-chat", "gpt-4o", "alice-自建"},
        f"应列出内置模型 + 自己的自建，实际 {sorted(by_name)}",
    )

    mine = by_name["alice-自建"]
    expect(mine["source"] == "自建", f"来源标错：{mine['source']}")
    expect_close(mine["input_price_cny_per_million"], 1.5, "自建模型输入价")
    expect_close(mine["output_price_cny_per_million"], 3.0, "自建模型输出价")
    expect_close(mine["cache_hit_price_cny_per_million"], 0.2, "自建模型缓存命中价")

    builtin = by_name["deepseek-chat"]
    expect(builtin["source"] == "内置", f"内置模型来源标错：{builtin['source']}")
    expect(
        builtin["cache_hit_price_cny_per_million"] is None,
        f"内置模型没配缓存价，应是 null 而不是 0：{builtin}",
    )
    expect(
        builtin["used_by_projects"] == ["Alice 项目"],
        f"「在用的项目」应来自上报数据，实际 {builtin['used_by_projects']}",
    )


def check_search_web() -> None:
    """用例 11：没配 Key 时 search_web 不进工具清单；配了之后结构固定、条数封顶。"""
    original_key = settings.tavily_api_key

    settings.tavily_api_key = ""
    try:
        names = [tool_obj.name for tool_obj in ai_tools.build_tools()]
        expect(
            "search_web" not in names,
            f"没配 Key 时 search_web 不该出现在工具清单里（模型会看到它）：{names}",
        )
    finally:
        settings.tavily_api_key = original_key

    names = [tool_obj.name for tool_obj in ai_tools.build_tools()]
    expect("search_web" in names, f"配了 Key 时 search_web 应该在清单里，实际 {names}")

    # 返回结构不靠真打网络来验：把打网络的那一层换掉，用例才是确定性的、也不吃配额。
    # 真实联网在批次 4 的浏览器验收里单独走一遍
    fake_results = [
        # 正文故意给得比上限长，才能验出「截断」这一步确实生效
        {"title": f"结果 {i}", "url": f"https://example.com/{i}", "content": "长" * 1500}
        for i in range(8)
    ]
    original_search = ai_tools._tavily_search
    ai_tools._tavily_search = lambda query: {"results": fake_results}
    try:
        got = call_tool(ai_tools.search_web, ctx_for(1), query="随便搜点什么")
    finally:
        ai_tools._tavily_search = original_search

    expect(
        len(got["results"]) == MAX_EXPECTED_RESULTS,
        f"条数应封顶 {MAX_EXPECTED_RESULTS} 条，实际 {len(got['results'])}",
    )
    first = got["results"][0]
    expect(
        set(first) == {"title", "url", "snippet"},
        f"每条字段应固定为 title / url / snippet，实际 {sorted(first)}",
    )
    expect(
        len(first["snippet"]) == MAX_EXPECTED_SNIPPET,
        f"snippet 应截到 {MAX_EXPECTED_SNIPPET} 字符，实际 {len(first['snippet'])}",
    )

    ai_tools._tavily_search = lambda query: {"results": []}
    try:
        empty = call_tool(ai_tools.search_web, ctx_for(1), query="搜不到的词")
    finally:
        ai_tools._tavily_search = original_search
    expect(empty["results"] == [], f"搜不到时应是空列表而不是抛异常：{empty}")

    def boom(query: str) -> dict:
        raise RuntimeError("connection reset")

    ai_tools._tavily_search = boom
    try:
        failed = call_tool(ai_tools.search_web, ctx_for(1), query="随便")
    finally:
        ai_tools._tavily_search = original_search
    expect("error" in failed, f"打网络失败应返回 error dict（模型能转述），实际 {failed}")


CHECKS = [
    ("用例 1：A 查自己的项目，数字与上报一致", check_own_project_data),
    ("★ 用例 2：A 传 B 的项目名 —— 不泄露存在性", check_foreign_project_name),
    ("用例 3：不传项目名只查自己的全部项目", check_all_projects_default),
    ("用例 4：名字对不上时给出完整候选清单", check_unknown_project_name),
    ("用例 5：list_projects 只列自己的项目", check_list_projects),
    ("用例 6：单日区间按助手时区的自然日换算", check_single_day_range),
    ("用例 7：日期颠倒 / 格式非法返回 error dict", check_bad_dates),
    ("补充：按小时跨多天返回 error dict", check_hour_granularity_span),
    ("用例 8：空数据项目返回 0 / 空列表", check_empty_project),
    ("用例 9：日志条数上限被 clamp", check_limit_clamp),
    ("用例 10：list_models 只列内置 + 自己的自建", check_list_models),
    ("用例 11：search_web 注册条件与返回结构", check_search_web),
]


def main() -> int:
    print("=" * 62)
    print("AI Monitor — AI 工具层验收（批次 1 + 批次 4 扩展）")
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
        print("❌ 测试库里还没有建表\n")
        print(f"原始错误：{exc.orig}\n")
        print("把建表脚本跑在测试库上：")
        print("   psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 \\")
        print("     -f sql/schema.sql")
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
    print("工具层完成 —— 数据工具与联网搜索均可用，用户隔离与边界处理已验证")
    return 0


if __name__ == "__main__":
    sys.exit(main())
