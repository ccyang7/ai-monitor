"""接口验收：用真实 HTTP 请求跑完阶段 6（调用日志查询）的全部验收清单。"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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


# 路由和 deps 引用的是同一个 get_db 函数对象，按对象做键就能全覆盖
app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

BUILTIN_MODELS = [
    ("deepseek", "deepseek-chat", "1", "2"),
    ("openai", "gpt-4o", "10", "30"),
]

BASE = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

LIST_FIELDS = {
    "id",
    "model",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "latency",
    "status",
    "estimated_cost",
    "created_at",
}

DETAIL_FIELDS = LIST_FIELDS | {
    "project_id",
    "provider",
    "input",
    "output",
    "error_message",
}


def mask_password(url: str) -> str:
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", url)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


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
        "output_tokens": 200,
        "latency": 1500,
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


def query_logs(token: str, project_id: int, **params) -> dict:
    # httpx 不会替我们把 datetime 转成 URL 能用的格式，先在脚本里转好
    clean = {
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in params.items()
        if v is not None
    }
    r = client.get(
        f"/api/v1/projects/{project_id}/logs", headers=auth(token), params=clean
    )
    expect(
        r.status_code == 200,
        f"列表接口应返回 200，实际 {r.status_code}：{r.text}",
    )
    return r.json()


def check_list_basic() -> None:
    token = register_and_login("alice")
    created = new_project(token, "列表测试")
    pid, key = created["id"], created["api_key"]

    seed_logs(
        key,
        [
            {"timestamp": (BASE + timedelta(hours=0)).isoformat()},
            {"timestamp": (BASE + timedelta(hours=1)).isoformat()},
            {"timestamp": (BASE + timedelta(hours=2)).isoformat()},
        ],
    )

    body = query_logs(token, pid)
    expect(body["total"] == 3, f"total 应为 3，实际 {body['total']}")
    expect(body["page"] == 1, f"默认 page 应为 1，实际 {body['page']}")
    expect(body["page_size"] == 20, f"默认 page_size 应为 20，实际 {body['page_size']}")

    items = body["items"]
    expect(len(items) == 3, f"应返回 3 条，实际 {len(items)}")
    times = [i["created_at"] for i in items]
    expect(
        times == sorted(times, reverse=True),
        f"必须按时间倒序，实际顺序 {times}",
    )

    item = items[0]
    expect(
        set(item.keys()) == LIST_FIELDS,
        f"列表项字段集合变了：多的是 {sorted(set(item.keys()) - LIST_FIELDS)}，"
        f"少的是 {sorted(LIST_FIELDS - set(item.keys()))}",
    )
    expect(
        "input" not in item and "output" not in item,
        "列表项带上了 input / output —— 大文本会让一页响应膨胀到几 MB",
    )

    # 100 输入 + 200 输出，deepseek-chat 单价 1 / 2 元每百万 = 0.0005
    expect(
        item["estimated_cost"] == 0.0005,
        f"成本应是 0.0005，实际 {item['estimated_cost']!r}",
    )
    expect(item["total_tokens"] == 300, f"total_tokens 应为 300，实际 {item['total_tokens']}")


def check_pagination() -> None:
    token = register_and_login("bob")
    created = new_project(token, "分页测试")
    pid, key = created["id"], created["api_key"]

    seed_logs(
        key,
        [
            {"timestamp": (BASE + timedelta(minutes=i)).isoformat()}
            for i in range(25)
        ],
    )

    seen: list[int] = []
    for page in (1, 2, 3):
        body = query_logs(token, pid, page=page, page_size=10)
        expect(
            body["total"] == 25,
            f"第 {page} 页的 total 应恒为 25，实际 {body['total']}",
        )
        expect(body["page"] == page, f"回显的 page 不对：{body['page']}")
        expected_len = 10 if page < 3 else 5
        expect(
            len(body["items"]) == expected_len,
            f"第 {page} 页应有 {expected_len} 条，实际 {len(body['items'])}",
        )
        seen.extend(i["id"] for i in body["items"])

    expect(len(seen) == 25, f"三页合计应有 25 条，实际 {len(seen)}")
    expect(
        len(set(seen)) == 25,
        "翻页出现了重复记录 —— 同毫秒记录的排序不稳定，第二排序键 id 可能没生效",
    )

    # 超出总页数：空数组 + 仍然正确的 total
    body = query_logs(token, pid, page=4, page_size=10)
    expect(body["items"] == [], f"第 4 页应为空数组，实际 {body['items']}")
    expect(body["total"] == 25, f"空页的 total 也要是 25，实际 {body['total']}")


def check_filters() -> None:
    token = register_and_login("carol")
    created = new_project(token, "筛选测试")
    pid, key = created["id"], created["api_key"]

    seed_logs(
        key,
        [
            # t=09-01, deepseek-chat, success, total=300
            {"timestamp": (BASE + timedelta(days=0)).isoformat()},
            # t=09-02, deepseek-chat, error, total=500
            {
                "timestamp": (BASE + timedelta(days=1)).isoformat(),
                "status": "error",
                "error": "Request timeout after 30s",
                "input_tokens": 500,
                "output_tokens": 0,
            },
            # t=09-03, gpt-4o, success, total=3000
            {
                "timestamp": (BASE + timedelta(days=2)).isoformat(),
                "provider": "openai",
                "model": "gpt-4o",
                "input_tokens": 1000,
                "output_tokens": 2000,
            },
            # t=09-04, deepseek-chat, success, total=0
            {
                "timestamp": (BASE + timedelta(days=3)).isoformat(),
                "input_tokens": 0,
                "output_tokens": 0,
            },
        ],
    )

    cases = [
        ("只按模型", {"model": "deepseek-chat"}, 3),
        ("模型换成另一个", {"model": "gpt-4o"}, 1),
        ("只按状态 success", {"status": "success"}, 3),
        ("只按状态 error", {"status": "error"}, 1),
        ("只按开始时间（09-03 起）", {"start_date": BASE + timedelta(days=2)}, 2),
        ("只按结束时间（09-02 止）", {"end_date": BASE + timedelta(days=1)}, 2),
        (
            "时间区间 09-02 ~ 09-03",
            {
                "start_date": BASE + timedelta(days=1),
                "end_date": BASE + timedelta(days=2),
            },
            2,
        ),
        ("Token 下限 500", {"min_tokens": 500}, 2),
        ("Token 上限 500", {"max_tokens": 500}, 3),
        ("Token 区间 300 ~ 500", {"min_tokens": 300, "max_tokens": 500}, 2),
        ("模型 + 状态组合", {"model": "deepseek-chat", "status": "success"}, 2),
        (
            "模型 + 状态 + 时间全组合",
            {
                "model": "deepseek-chat",
                "status": "success",
                "start_date": BASE + timedelta(days=1),
            },
            1,
        ),
    ]

    for label, params, expected in cases:
        body = query_logs(token, pid, **params)
        expect(
            body["total"] == expected,
            f"{label} 应命中 {expected} 条，实际 {body['total']}（{params}）",
        )


def check_search() -> None:
    token = register_and_login("dave")
    created = new_project(token, "搜索测试")
    pid, key = created["id"], created["api_key"]

    seed_logs(
        key,
        [
            {
                "input": "我想问 Redis 的持久化机制",
                "output": "RDB 和 AOF 两种方式",
            },
            {
                "input": "PostgreSQL 的索引怎么选",
                "output": "B-tree 是最常用的",
            },
            {
                "provider": "openai",
                "model": "gpt-4o",
                "input": "无关内容",
                "output": "也无关",
            },
        ],
    )

    cases = [
        ("搜 input（小写匹配大写，验证 ILIKE）", {"q": "redis"}, 1),
        ("搜 output", {"q": "RDB"}, 1),
        ("搜 model 名称", {"q": "gpt-4o"}, 1),
        ("跨字段命中：三条里两条的 input 含「的」", {"q": "的"}, 2),
        ("搜不到", {"q": "这个词不该存在"}, 0),
    ]

    for label, params, expected in cases:
        body = query_logs(token, pid, **params)
        expect(
            body["total"] == expected,
            f"{label} 应命中 {expected} 条，实际 {body['total']}",
        )

    # 空搜索词必须跳过：比的是「和不过滤时结果一致」，而不是某个写死的数字
    baseline = query_logs(token, pid)["total"]
    for blank in ("", "   "):
        body = query_logs(token, pid, q=blank)
        expect(
            body["total"] == baseline,
            f"q={blank!r} 应等同于不过滤（{baseline} 条），实际 {body['total']} 条",
        )


def check_search_escape() -> None:
    token = register_and_login("erin")
    created = new_project(token, "转义测试")
    pid, key = created["id"], created["api_key"]

    seed_logs(
        key,
        [
            {"input": "命中率 50% 左右"},
            {"input": "编号 50x 的样本"},
            {"input": "user_id 字段说明"},
            {"input": "userXid 字段说明"},
        ],
    )

    # % 不转义的话会被当通配符，把所有「50 开头的」都搜出来
    body = query_logs(token, pid, q="50%")
    expect(
        body["total"] == 1,
        f"搜「50%」应只命中字面量那一条，实际 {body['total']} 条 —— % 被当成通配符了",
    )

    # _ 是 LIKE 的单字符通配符，不转义的话 user_id 会连 userXid 一起搜出来
    body = query_logs(token, pid, q="user_id")
    expect(
        body["total"] == 1,
        f"搜「user_id」应只命中字面量那一条，实际 {body['total']} 条 —— _ 被当成单字符通配符了",
    )

    body = query_logs(token, pid, q="%")
    expect(
        body["total"] == 1,
        f"只搜一个「%」应命中含百分号的那条，实际 {body['total']} 条",
    )


def check_detail() -> None:
    token = register_and_login("frank")
    created = new_project(token, "详情测试")
    pid, key = created["id"], created["api_key"]

    ok_id = seed_logs(
        key,
        [{"input": "什么是 Redis？", "output": "Redis 是一种开源的键值数据库……"}],
    )[0]
    failed_id = seed_logs(
        key,
        [
            {
                "status": "error",
                "output": None,
                "error": "Request timeout after 30s",
                "latency": 30000,
            }
        ],
    )[0]

    body = client.get(f"/api/v1/logs/{ok_id}", headers=auth(token)).json()
    expect(
        set(body.keys()) == DETAIL_FIELDS,
        f"详情字段集合不符：多的是 {sorted(set(body.keys()) - DETAIL_FIELDS)}，"
        f"少的是 {sorted(DETAIL_FIELDS - set(body.keys()))}",
    )
    expect(body["input"] == "什么是 Redis？", f"input 不对：{body['input']!r}")
    expect(
        body["output"] == "Redis 是一种开源的键值数据库……", f"output 不对：{body['output']!r}"
    )
    expect(body["project_id"] == pid, f"project_id 不对：{body['project_id']}")
    expect(body["provider"] == "deepseek", f"provider 不对：{body['provider']}")
    expect(body["error_message"] is None, f"成功调用不该有错误信息：{body['error_message']}")

    body = client.get(f"/api/v1/logs/{failed_id}", headers=auth(token)).json()
    expect(body["status"] == "error", f"状态不对：{body['status']}")
    expect(
        body["error_message"] == "Request timeout after 30s",
        f"失败调用的错误信息要能看到，实际 {body['error_message']!r}",
    )
    expect(body["output"] is None, f"失败调用的 output 应为 null，实际 {body['output']!r}")
    expect(body["latency"] == 30000, f"耗时不对：{body['latency']}")


def check_isolation() -> None:
    token_a = register_and_login("grace")
    token_b = register_and_login("heidi")
    project_a = new_project(token_a, "A 的项目")
    project_b = new_project(token_b, "B 的项目")

    seed_logs(
        project_b["api_key"],
        [{"timestamp": BASE.isoformat(), "input": "B 的机密数据"}],
    )
    b_log_id = query_logs(token_b, project_b["id"])["items"][0]["id"]

    # A 查 B 的项目：不存在和不属于自己走同一条路径，都是 404
    r = client.get(
        f"/api/v1/projects/{project_b['id']}/logs", headers=auth(token_a)
    )
    expect(r.status_code == 404, f"查别人的项目日志应 404，实际 {r.status_code}：{r.text}")

    r = client.get("/api/v1/projects/999999/logs", headers=auth(token_a))
    expect(r.status_code == 404, f"查不存在的项目应 404，实际 {r.status_code}")

    # A 查 B 的日志详情：路径里没有 project_id 兜底，全靠 get_own_log 的 join
    r = client.get(f"/api/v1/logs/{b_log_id}", headers=auth(token_a))
    expect(
        r.status_code == 404,
        f"查别人的日志详情应 404，实际 {r.status_code}：{r.text}",
    )
    expect("B 的机密数据" not in r.text, "404 响应里泄露了别人日志的内容！")

    r = client.get("/api/v1/logs/999999", headers=auth(token_a))
    expect(r.status_code == 404, f"查不存在的日志应 404，实际 {r.status_code}")

    # B 自己能查到（证明上面的 404 是隔离生效，不是接口坏了）
    r = client.get(f"/api/v1/logs/{b_log_id}", headers=auth(token_b))
    expect(r.status_code == 200, f"B 查自己的日志应 200，实际 {r.status_code}：{r.text}")

    # 未登录一律 401
    r = client.get(f"/api/v1/projects/{project_b['id']}/logs")
    expect(r.status_code == 401, f"未登录查列表应 401，实际 {r.status_code}")
    r = client.get(f"/api/v1/logs/{b_log_id}")
    expect(r.status_code == 401, f"未登录查详情应 401，实际 {r.status_code}")

    # 列表里不该混进别人的数据：A 的项目建了日志之后，只能看到自己的
    seed_logs(project_a["api_key"], [{"input": "A 自己的数据"}])
    body = query_logs(token_a, project_a["id"])
    expect(body["total"] == 1, f"A 只该看到自己那 1 条，实际 {body['total']}")
    expect(body["items"][0]["id"] != b_log_id, "A 的列表里出现了 B 的日志！")


def check_validation() -> None:
    token = register_and_login("ivan")
    created = new_project(token, "校验测试")
    pid, key = created["id"], created["api_key"]

    seed_logs(key, [{"timestamp": BASE.isoformat()}])

    cases = [
        ("page=0", {"page": 0}),
        ("page 为负", {"page": -1}),
        ("page_size=0", {"page_size": 0}),
        ("page_size 超过上限 100", {"page_size": 101}),
        ("min_tokens 大于 max_tokens", {"min_tokens": 500, "max_tokens": 100}),
        (
            "start_date 晚于 end_date",
            {
                "start_date": (BASE + timedelta(days=5)).isoformat(),
                "end_date": BASE.isoformat(),
            },
        ),
        ("status 非法值", {"status": "timeout"}),
        ("min_tokens 为负", {"min_tokens": -1}),
        ("q 超长（101 字符）", {"q": "x" * 101}),
        ("model 超长", {"model": "x" * 101}),
    ]

    for label, params in cases:
        r = client.get(
            f"/api/v1/projects/{pid}/logs", headers=auth(token), params=params
        )
        expect(
            r.status_code == 422,
            f"{label} 应返回 422，实际 {r.status_code}：{r.text}",
        )


def check_timezone() -> None:
    token = register_and_login("judy")
    created = new_project(token, "时区测试")
    pid, key = created["id"], created["api_key"]

    at = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    seed_logs(key, [{"timestamp": at.isoformat()}])

    # 不带时区的 end_date 按 UTC 解释 → 与日志时间相等，应命中。
    # 若被按本机时区（UTC+8）解释，end 会变成 08-31T16:00Z，这条就查不到了
    body = query_logs(token, pid, end_date=datetime(2026, 9, 1, 0, 0))
    expect(
        body["total"] == 1,
        f"不带时区的 end_date 应按 UTC 解释，此类请求应命中 1 条，实际 {body['total']}",
    )

    # 反向：start 比日志晚 1 秒（naive → UTC），应一条都不命中
    body = query_logs(token, pid, start_date=datetime(2026, 9, 1, 0, 0, 1))
    expect(
        body["total"] == 0,
        f"start_date 比日志时间晚 1 秒应 0 条，实际 {body['total']} —— "
        "不带时区的参数可能被按本机时区解释了",
    )


CHECKS = [
    ("列表：倒序 / 字段集合 / 不含大文本 / 成本", check_list_basic),
    ("分页：切片正确、total 恒定、翻页不重不漏、越界返回空", check_pagination),
    ("筛选：模型 / 状态 / 时间 / Token 及组合", check_filters),
    ("搜索：input / output / model 三处命中 + 空词跳过", check_search),
    ("搜索转义：% 和 _ 不当通配符", check_search_escape),
    ("详情：全字段 + 失败调用的错误信息", check_detail),
    ("★ 用户隔离：跨项目 404、跨用户 404、未登录 401", check_isolation),
    ("边界校验（422 而不是 500）", check_validation),
    ("时间参数：不带时区按 UTC 解释（与上报一致）", check_timezone),
]


def main() -> int:
    print("=" * 62)
    print("AI Monitor — 调用日志查询验收（阶段 6）")
    print("=" * 62)
    print(f"测试库：{mask_password(TEST_DATABASE_URL)}")
    print()

    try:
        reset_db()
    except OperationalError as exc:
        print("❌ 连不上测试库\n")
        print(f"原始错误：{exc.orig}\n")
        print("在容器里建库（用 ai_monitor，不是 postgres）：")
        print('   docker exec -it <容器名> psql -U ai_monitor -d ai_monitor -h 127.0.0.1 \\')
        print('     -c "CREATE DATABASE ai_monitor_test OWNER ai_monitor;"')
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
    print("阶段 6 调用日志查询完成 —— 列表、筛选、搜索、详情与用户隔离都已就位")
    return 0


if __name__ == "__main__":
    sys.exit(main())
