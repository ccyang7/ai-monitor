"""AI 助手流式接口验收：逐帧核对 SSE 事件顺序、内容分拣与错误映射。

真实调用模型的只有用例 2；其余用例要么在接口层就被挡下，要么连不上模型（要的就是连不上）。
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402
from langchain.agents import create_agent  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.ai.stream as stream_module  # noqa: E402
import app.ai.tools as ai_tools  # noqa: E402
from app.ai.tools import ALL_TOOLS  # noqa: E402
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

# 在 main() 里用 with 赋值。不用上下文管理器时每个请求跑在新建的事件循环里，
# 而 ChatOpenAI 内部的 httpx 异步客户端绑定了第一次调用时的循环 —— 第二个请求必炸
client = TestClient(app)

STREAM_URL = "/api/v1/chat/stream"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def reset_db() -> None:
    with test_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, projects, api_keys, models, llm_requests"
                " RESTART IDENTITY CASCADE"
            )
        )


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


def a_user(username: str) -> tuple[int, str]:
    token = register_and_login(username)
    r = client.get("/api/v1/users/me", headers=auth(token))
    expect(r.status_code == 200, f"/users/me 应返回 200，实际 {r.status_code}：{r.text}")
    return r.json()["id"], token


def read_events(response) -> list[dict]:
    events = []
    for line in response.iter_lines():
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[len("data: ") :]))
    return events


def ask_stream(token: str, message: str):
    return client.stream(
        "POST",
        STREAM_URL,
        headers=auth(token),
        json={"message": message},
    )


def check_unauthorized() -> None:
    """用例 1：未登录。"""
    r = client.post(STREAM_URL, json={"message": "你好"})
    expect(r.status_code == 401, f"未登录应返回 401，实际 {r.status_code}：{r.text}")


def check_event_order() -> None:
    """用例 2：事件顺序、工具状态成对、正文增量里没有工具参数碎片。"""
    uid, token = a_user("alice")

    with ask_stream(token, "我有哪些项目？") as r:
        expect(r.status_code == 200, f"应返回 200，实际 {r.status_code}")
        expect(
            r.headers["content-type"].startswith("text/event-stream"),
            f"Content-Type 应是 text/event-stream，实际 {r.headers['content-type']}",
        )
        expect(
            r.headers.get("cache-control") == "no-cache",
            f"应带 Cache-Control: no-cache，实际 {r.headers.get('cache-control')}",
        )
        events = read_events(r)

    print(f"   · 收到 {len(events)} 帧：{''.join(e['type'][0] for e in events)}")
    for event in events[:4]:
        print(f"     {json.dumps(event, ensure_ascii=False)[:110]}")

    types = [e["type"] for e in events]
    expect(bool(events), "一帧事件都没收到")
    expect(types[-1] == "done", f"最后一帧应是 done，实际是 {types[-1]}：{events[-1]}")
    expect("error" not in types, f"不该有 error 事件：{events}")

    starts = [e for e in events if e["type"] == "status" and e["stage"] == "tool_start"]
    expect(
        bool(starts),
        f"至少应有一条 tool_start，实际事件类型序列：{''.join(t[0] for t in types)}",
    )
    ends = [e for e in events if e["type"] == "status" and e["stage"] == "tool_end"]
    expect(
        len(starts) == len(ends),
        f"tool_start 与 tool_end 应成对，实际 {len(starts)} : {len(ends)}",
    )
    expect(
        all("label" in s and s["label"] for s in starts),
        f"tool_start 应带中文 label，实际 {starts}",
    )
    for start, end in zip(starts, ends):
        expect(
            start["name"] == end["name"],
            f"配对顺序应一致，实际 {start['name']} : {end['name']}",
        )

    reply = "".join(e["text"] for e in events if e["type"] == "delta")
    print(f"   · 拼出的回答：{reply[:120]}")
    expect(bool(reply.strip()), "delta 拼出来是空的")
    # 工具调用的参数碎片若漏进 delta，这里会冒出 {"name": ... 这样的 JSON 残片
    expect('{"' not in reply, f"回答里混进了工具参数的 JSON 碎片：{reply!r}")
    expect('"error"' not in reply, f"回答里混进了工具返回的 JSON：{reply!r}")

    thread_id = events[-1]["thread_id"]
    expect(
        thread_id.startswith(f"user_{uid}_"),
        f"thread_id 应带 user_{uid}_ 前缀，实际 {thread_id}",
    )


def check_no_api_key() -> None:
    """用例 3：未配 Key 时流还没建立，可以正经返回 503 而不是 error 事件。"""
    _, token = a_user("alice")
    original = settings.llm_api_key
    settings.llm_api_key = ""
    try:
        r = client.post(STREAM_URL, headers=auth(token), json={"message": "你好"})
        expect(r.status_code == 503, f"未配 Key 应返回 503，实际 {r.status_code}：{r.text}")
    finally:
        settings.llm_api_key = original


def check_recursion_error_event() -> None:
    """用例 4：中途出错改不了状态码，只能是 200 + error 事件（与非流式的 504 是两种契约）。"""
    _, token = a_user("alice")
    original = settings.assistant_max_tool_rounds
    settings.assistant_max_tool_rounds = 0  # 上限 1 步，第一个模型节点就走不完
    try:
        with ask_stream(token, "我有哪些项目？") as r:
            expect(
                r.status_code == 200,
                f"流式接口的状态码在开流前就定了，应是 200，实际 {r.status_code}",
            )
            events = read_events(r)
    finally:
        settings.assistant_max_tool_rounds = original

    types = [e["type"] for e in events]
    print(f"   · 收到 {len(events)} 帧：{types}")
    expect(bool(events), "一帧事件都没收到")
    expect(
        events[-1]["type"] == "error",
        f"最后一帧应是 error，实际 {events[-1]}",
    )
    expect("超时" in events[-1]["message"], f"应是友好提示，实际 {events[-1]}")
    expect("done" not in types, f"error 之后不该再发 done：{types}")


def check_llm_unavailable_event() -> None:
    """用例 5：模型端连不上 —— 同样是流内 error 事件，不是 503。"""
    _, token = a_user("alice")
    original = stream_module.agent
    # 借调一个连不上的 agent：换掉 stream 模块里的引用，接口层读的就是它
    stream_module.agent = create_agent(
        model=ChatOpenAI(
            model=settings.llm_model,
            base_url="http://127.0.0.1:9/v1",
            api_key="unreachable",
            timeout=5,
            max_retries=0,
        ),
        tools=ALL_TOOLS,
        context_schema=None,
        checkpointer=InMemorySaver(),
    )
    try:
        with ask_stream(token, "我有哪些项目？") as r:
            expect(r.status_code == 200, f"应是 200，实际 {r.status_code}")
            events = read_events(r)
    finally:
        stream_module.agent = original

    types = [e["type"] for e in events]
    print(f"   · 收到 {len(events)} 帧：{types}")
    expect(bool(events), "一帧事件都没收到")
    expect(events[-1]["type"] == "error", f"最后一帧应是 error，实际 {events[-1]}")
    expect(
        "暂时不可用" in events[-1]["message"],
        f"应是友好提示，实际 {events[-1]}",
    )
    # 原始报错里带着 base_url 和 Key，绝不能透传给前端
    expect(
        "127.0.0.1" not in events[-1]["message"],
        f"错误信息里泄露了端点：{events[-1]}",
    )


CHECKS = [
    ("用例 1：未登录 401", check_unauthorized),
    ("★ 用例 2：事件顺序 / 工具成对 / delta 分拣", check_event_order),
    ("用例 3：未配 Key 返回 503（流未开始）", check_no_api_key),
    ("★ 用例 4：超轮次 → 200 + error 事件", check_recursion_error_event),
    ("★ 用例 5：模型连不上 → 200 + error 事件", check_llm_unavailable_event),
]


def main() -> int:
    global client

    print("=" * 62)
    print("AI Monitor — AI 助手流式接口验收（批次 3）")
    print("=" * 62)
    print(f"测试库：{TEST_DATABASE_URL.rsplit('@', 1)[-1]}")
    print(f"模型：{settings.llm_model}")

    if not settings.llm_api_key:
        print("\n❌ .env 里没配 LLM_API_KEY，用例 2 必须真实调用模型才能跑。")
        return 1

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
    print("批次 3 完成 —— 流式接口已接入，事件顺序与错误映射均已验证")
    return 0


if __name__ == "__main__":
    sys.exit(main())
