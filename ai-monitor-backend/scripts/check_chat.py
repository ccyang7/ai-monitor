"""AI 助手接口验收：真实调用 LLM，验证工具调用、数字对账、会话隔离与错误映射。

注意：这个脚本会真的调用 DeepSeek（花钱、耗时，一轮提问十几秒），
所以只保留必要的 7 次提问 —— 每次提问都对应一条要断言的验收点。
"""

import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.ai.tools as ai_tools  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from main import app  # noqa: E402

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or (
    settings.database_url.rsplit("/", 1)[0] + "/ai_monitor_test"
)

_DB_NAME = TEST_DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
if not _DB_NAME.endswith("_test"):
    print(f"拒绝执行：测试库名必须是 *_test 结尾，当前是「{_DB_NAME}」。")
    sys.exit(1)

test_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

# 工具由 Agent 在进程内直接调用，不走 FastAPI 依赖注入 —— 把会话工厂换成测试库的
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

# 在 main() 里用 with 赋值。TestClient 不用上下文管理器时，每个请求都跑在新建的事件循环里，
# 而 ChatOpenAI 内部的 httpx 异步客户端在第一次调用时就绑定了当时的循环 ——
# 第二个请求复用它直接抛「Event loop is closed」。with 让所有请求共用一个常驻循环
client = TestClient(app)

BUILTIN_MODELS = [
    ("deepseek", "deepseek-chat", "1", "2"),
    ("openai", "gpt-4o", "10", "30"),
]

# 对账用的固定数据：9 月 10 日的 7 次调用、每次 300 Token，一共 2100
CALLS = 7
TOKENS_PER_CALL = 300
TOTAL_TOKENS = CALLS * TOKENS_PER_CALL
DAY = datetime(2026, 9, 10, 2, 0, tzinfo=timezone.utc)  # 北京时间 10:00
DAY_STR = "2026-09-10"

PROJECT = "数据对账"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digits(text: str) -> str:
    # 模型可能写成 2,100 或者 2100，去掉所有非数字再比对，避免被千分位坑到
    return re.sub(r"\D", "", text)


def show(label: str, reply: str) -> None:
    print(f"   · {label}：{reply[:160]}{'…' if len(reply) > 160 else ''}")


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


def seed_logs(key: str, rows: list[dict]) -> None:
    for row in rows:
        payload = {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "input": "什么是 Redis？",
            "output": "Redis 是一种键值数据库……",
            "input_tokens": 100,
            "output_tokens": 200,
            "latency": 1000,
            "status": "success",
            **row,
        }
        r = client.post("/api/v1/logs", headers=api_key_header(key), json=payload)
        expect(r.status_code == 201, f"造数据上报失败 {r.status_code}：{r.text}")


def a_user(username: str) -> tuple[int, str]:
    token = register_and_login(username)
    r = client.get("/api/v1/users/me", headers=auth(token))
    expect(r.status_code == 200, f"/users/me 应返回 200，实际 {r.status_code}：{r.text}")
    return r.json()["id"], token


def ask(token: str, message: str, thread_id: str | None = None, **extra):
    body = {"message": message, **extra}
    if thread_id is not None:
        body["thread_id"] = thread_id
    return client.post("/api/v1/chat", headers=auth(token), json=body)


def check_unauthorized() -> None:
    """用例 1：未登录。"""
    r = client.post("/api/v1/chat", json={"message": "我有哪些项目？"})
    expect(r.status_code == 401, f"未登录应返回 401，实际 {r.status_code}：{r.text}")


def check_basic_question() -> None:
    """用例 2 + 4：正常提问会调工具；thread_id 由服务端拼上前缀。"""
    uid, token = a_user("alice")
    new_project(token, "问答助手")

    r = ask(token, "我有哪些项目？")
    expect(r.status_code == 200, f"提问应返回 200，实际 {r.status_code}：{r.text}")
    data = r.json()
    show("回答", data["reply"])

    expect(bool(data["reply"].strip()), "reply 不能是空的")
    tools = [item["tool"] for item in data["tool_calls"]]
    expect(
        "list_projects" in tools,
        f"问「我有哪些项目」应调用 list_projects，实际调用了 {tools}",
    )

    thread_id = data["thread_id"]
    prefix = f"user_{uid}_"
    expect(
        thread_id.startswith(prefix),
        f"thread_id 应带 {prefix} 前缀，实际 {thread_id}",
    )
    suffix = thread_id[len(prefix):]
    try:
        uuid.UUID(suffix)
    except ValueError:
        raise AssertionError(f"不传 thread_id 时应补一个 uuid，实际拼出 {thread_id}") from None


def check_numbers_match() -> None:
    """用例 3：回答里的数字与手工造的数据一致（对账式断言）。"""
    _, token = a_user("alice")
    project = new_project(token, PROJECT)
    seed_logs(
        project["api_key"],
        [
            {"timestamp": (DAY + timedelta(minutes=i)).isoformat()}
            for i in range(CALLS)
        ],
    )

    r = ask(token, f"项目「{PROJECT}」在 {DAY_STR} 这一天有多少次调用？总共用了多少 Token？")
    expect(r.status_code == 200, f"提问应返回 200，实际 {r.status_code}：{r.text}")
    data = r.json()
    show("回答", data["reply"])

    tools = [item["tool"] for item in data["tool_calls"]]
    expect(
        "get_token_usage" in tools or "get_project_overview" in tools,
        f"问用量应调用统计类工具，实际调用了 {tools}",
    )
    numbers = digits(data["reply"])
    expect(
        str(TOTAL_TOKENS) in numbers,
        f"回答里的 Token 总量应等于 {TOTAL_TOKENS}，实际回答：{data['reply']!r}",
    )


def check_two_turns_context() -> None:
    """用例 5：同一 thread_id 连问两轮，第二轮记得第一轮的结论。"""
    _, token = a_user("alice")
    project = new_project(token, PROJECT)
    seed_logs(
        project["api_key"],
        [{"timestamp": (DAY + timedelta(minutes=i)).isoformat()} for i in range(CALLS)],
    )
    thread = str(uuid.uuid4())

    r1 = ask(token, f"项目「{PROJECT}」在 {DAY_STR} 有多少次调用？", thread_id=thread)
    expect(r1.status_code == 200, f"第一轮应返回 200，实际 {r1.status_code}：{r1.text}")
    show("第一轮", r1.json()["reply"])

    r2 = ask(token, "把这个数字乘以二，直接告诉我结果。", thread_id=thread)
    expect(r2.status_code == 200, f"第二轮应返回 200，实际 {r2.status_code}：{r2.text}")
    reply = r2.json()["reply"]
    show("第二轮", reply)
    expect(
        str(CALLS * 2) in digits(reply),
        f"第二轮应记得第一轮查到的 {CALLS} 次并算出 {CALLS * 2}，实际：{reply!r}",
    )


def check_thread_isolation() -> None:
    """用例 6：B 拿 A 的 thread_id 提问，落到 B 自己的新会话里。"""
    _, token_a = a_user("alice")
    _, token_b = a_user("bob")
    thread = str(uuid.uuid4())

    r1 = ask(token_a, "记住：我们的暗号是「紫色独角兽」，回复「好的」即可。", thread_id=thread)
    expect(r1.status_code == 200, f"alice 提问应返回 200，实际 {r1.status_code}：{r1.text}")

    r2 = ask(token_b, "我刚才告诉你的暗号是什么？", thread_id=thread)
    expect(r2.status_code == 200, f"bob 提问应返回 200，实际 {r2.status_code}：{r2.text}")
    reply = r2.json()["reply"]
    show("bob 的回答", reply)
    expect(
        "紫色独角兽" not in reply,
        f"bob 用同一个 thread_id 却读到了 alice 的对话历史！回答：{reply!r}",
    )


def check_no_api_key() -> None:
    """用例 7：未配 Key 时返回 503，且不影响其他接口。"""
    _, token = a_user("alice")
    original = settings.llm_api_key
    settings.llm_api_key = ""
    try:
        r = ask(token, "我有哪些项目？")
        expect(r.status_code == 503, f"未配 Key 应返回 503，实际 {r.status_code}：{r.text}")

        r = client.get("/api/v1/projects", headers=auth(token))
        expect(
            r.status_code == 200,
            f"未配 Key 不该影响其他接口，项目列表返回了 {r.status_code}",
        )
    finally:
        settings.llm_api_key = original


def check_message_too_long() -> None:
    """用例 8：超长 message 被参数校验拦下。"""
    _, token = a_user("alice")
    r = ask(token, "啊" * 2001)
    expect(r.status_code == 422, f"超长 message 应返回 422，实际 {r.status_code}：{r.text}")


def check_recursion_limit() -> None:
    """补充：工具轮次超限 → 504 + 友好提示，而不是 500。"""
    _, token = a_user("alice")
    original = settings.assistant_max_tool_rounds
    settings.assistant_max_tool_rounds = 0  # 上限 1 步，第一个模型节点就走不完
    try:
        r = ask(token, "我有哪些项目？")
        expect(
            r.status_code == 504,
            f"轮次超限应返回 504，实际 {r.status_code}：{r.text}",
        )
        expect(
            "超时" in r.json()["detail"],
            f"应返回友好提示，实际 {r.json()}",
        )
    finally:
        settings.assistant_max_tool_rounds = original


CHECKS = [
    ("用例 1：未登录 401", check_unauthorized),
    ("用例 2 / 4：正常提问 + thread_id 前缀", check_basic_question),
    ("用例 3：回答数字与数据对账", check_numbers_match),
    ("用例 5：同一 thread_id 连问两轮", check_two_turns_context),
    ("★ 用例 6：跨用户 thread_id 不串会话", check_thread_isolation),
    ("用例 7：未配 Key 返回 503", check_no_api_key),
    ("用例 8：超长 message 422", check_message_too_long),
    ("补充：轮次超限返回 504", check_recursion_limit),
]


def main() -> int:
    global client

    print("=" * 62)
    print("AI Monitor — AI 助手接口验收（批次 2）")
    print("=" * 62)
    print(f"测试库：{TEST_DATABASE_URL.rsplit('@', 1)[-1]}")
    print(f"模型：{settings.llm_model}")

    if not settings.llm_api_key:
        print("\n❌ .env 里没配 LLM_API_KEY，这个脚本必须真实调用模型才能跑。")
        return 1

    print("真实调用 DeepSeek，一轮提问约十几秒，请耐心等待。\n")

    failures: list[str] = []

    with TestClient(app) as client:
        for name, check in CHECKS:
            reset_db()
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

    app.dependency_overrides.clear()
    reset_db()

    print("=" * 62)
    if failures:
        print(f"❌ {len(failures)}/{len(CHECKS)} 项未通过：")
        for name in failures:
            print(f"     · {name}")
        return 1

    print(f"全部通过 ✅ {len(CHECKS)}/{len(CHECKS)}")
    print("批次 2 完成 —— Agent 已接入，工具调用、数字对账、会话隔离均已验证")
    return 0


if __name__ == "__main__":
    sys.exit(main())
