"""AI 助手用量验收：统计口径、两条链路落库、管理端聚合，全程不调用真实模型。"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import openai  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.messages import (  # noqa: E402
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    ToolMessage,
)
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import OperationalError, ProgrammingError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.ai.stream as stream_module  # noqa: E402
import app.api.chat as chat_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.services.assistant_chat import (  # noqa: E402
    TurnUsage,
    current_turn,
    record_chat,
)
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

REAL_CHAT_AGENT = chat_module.agent
REAL_STREAM_AGENT = stream_module.agent

# 上一轮留下的历史：本轮的统计绝对不能把这 150 token 算进来
HISTORY = [
    HumanMessage(content="上一轮的问题"),
    AIMessage(
        content="上一轮的回答",
        usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    ),
]

# 本轮：一次工具调用 + 两次模型调用，token 分别是 30 和 45
TURN = [
    HumanMessage(content="这一轮的问题"),
    AIMessage(
        content="",
        tool_calls=[{"name": "list_projects", "args": {}, "id": "call_1"}],
        usage_metadata={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    ),
    ToolMessage(content="[]", tool_call_id="call_1"),
    AIMessage(
        content="这一轮的回答",
        usage_metadata={"input_tokens": 40, "output_tokens": 5, "total_tokens": 45},
    ),
]


class FakeAgent:
    """替身 Agent：两条链路都不碰真实模型，结果与错因由用例指定。"""

    def __init__(self, result=None, chunks=None, error=None):
        self.result = result
        self.chunks = chunks or []
        self.error = error

    async def ainvoke(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        return self.result

    async def astream(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        for chunk in self.chunks:
            yield chunk


def mask_password(url: str) -> str:
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", url)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def reset_db() -> None:
    with test_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, projects, api_keys, models, llm_requests,"
                " assistant_chats RESTART IDENTITY CASCADE"
            )
        )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register_and_login(username: str, password: str = "pw123456") -> str:
    r = client.post(
        "/api/v1/auth/register", json={"username": username, "password": password}
    )
    expect(r.status_code == 201, f"注册 {username} 应返回 201：{r.text}")

    r = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    expect(r.status_code == 200, f"登录 {username} 应返回 200：{r.text}")
    return r.json()["access_token"]


def promote_to_admin(username: str) -> None:
    with test_engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET role = 'admin' WHERE username = :u"),
            {"u": username},
        )


def user_id_of(username: str) -> int:
    with test_engine.begin() as conn:
        return conn.execute(
            text("SELECT id FROM users WHERE username = :u"), {"u": username}
        ).scalar_one()


def fetch_records() -> list[dict]:
    with test_engine.begin() as conn:
        return [
            dict(row)
            for row in conn.execute(
                text(
                    "SELECT user_id, thread_id, model, input_tokens, output_tokens,"
                    " total_tokens, tool_call_count, latency, status, error_message"
                    " FROM assistant_chats ORDER BY id"
                )
            ).mappings()
        ]


def parse_frames(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def check_turn_math() -> None:
    usage = TurnUsage()
    for message in current_turn(HISTORY + TURN):
        usage.add_message(message)

    expect(
        usage.input_tokens == 50,
        f"输入 token 应只算本轮 10+40=50，实际 {usage.input_tokens}"
        "（把上一轮的算进来会得到 150）",
    )
    expect(usage.output_tokens == 25, f"输出 token 应是 25，实际 {usage.output_tokens}")
    expect(usage.total_tokens == 75, f"总 token 应是 75，实际 {usage.total_tokens}")
    expect(
        usage.tool_call_count == 1,
        f"工具次数应是 1，实际 {usage.tool_call_count}",
    )

    # 流式没有工具调用消息，工具次数由 updates 数；这里只喂 usage 片
    streamed = TurnUsage()
    streamed.add_usage(AIMessageChunk(content="", usage_metadata={"input_tokens": 11, "output_tokens": 2, "total_tokens": 13}))
    streamed.add_usage(AIMessageChunk(content="", usage_metadata={"input_tokens": 20, "output_tokens": 3, "total_tokens": 23}))
    expect(
        (streamed.input_tokens, streamed.output_tokens, streamed.total_tokens)
        == (31, 5, 36),
        f"流式逐片累加不对：{streamed}",
    )


def check_record_writes_row() -> None:
    register_and_login("alice")
    alice_id = user_id_of("alice")

    db = TestSession()
    try:
        record_chat(
            db,
            user_id=alice_id,
            thread_id="user_1_abc",
            usage=TurnUsage(input_tokens=1, output_tokens=2, total_tokens=3),
            latency_ms=1234,
            status="success",
        )
    finally:
        db.close()

    rows = fetch_records()
    expect(len(rows) == 1, f"应写入 1 行，实际 {len(rows)}")
    row = rows[0]
    expect(row["user_id"] == alice_id, f"user_id 不对：{row['user_id']}")
    expect(row["thread_id"] == "user_1_abc", f"thread_id 不对：{row['thread_id']}")
    expect(row["model"] == settings.llm_model, f"model 应取当前配置：{row['model']}")
    expect(
        (row["input_tokens"], row["output_tokens"], row["total_tokens"]) == (1, 2, 3),
        f"token 落库不对：{row}",
    )
    expect(row["latency"] == 1234, f"耗时落库不对：{row['latency']}")
    expect(row["status"] == "success", f"状态不对：{row['status']}")
    expect(row["error_message"] is None, "成功记录的 error_message 必须是 NULL")


def check_non_stream_records_turn() -> None:
    token = register_and_login("alice")
    chat_module.agent = FakeAgent(result={"messages": HISTORY + TURN})
    try:
        r = client.post(
            "/api/v1/chat",
            headers=auth(token),
            json={"message": "这一轮的问题", "thread_id": "abc"},
        )
    finally:
        chat_module.agent = REAL_CHAT_AGENT

    expect(r.status_code == 200, f"提问应返回 200，实际 {r.status_code}：{r.text}")
    body = r.json()
    expect(
        body["thread_id"] == f"user_{user_id_of('alice')}_abc",
        f"thread_id 应带服务端前缀：{body['thread_id']}",
    )
    expect(
        len(body["tool_calls"]) == 1,
        f"响应只能带本轮的工具调用，实际 {len(body['tool_calls'])} 次"
        "（把历史也算进来会是 1 次，但 token 会翻倍）",
    )

    rows = fetch_records()
    expect(len(rows) == 1, f"应写入 1 行，实际 {len(rows)}")
    row = rows[0]
    expect(
        (row["input_tokens"], row["output_tokens"], row["total_tokens"]) == (50, 25, 75),
        f"记录必须只算本轮（50/25/75），实际 {row['input_tokens']}/{row['output_tokens']}/{row['total_tokens']}",
    )
    expect(row["tool_call_count"] == 1, f"工具次数应是 1，实际 {row['tool_call_count']}")
    expect(row["status"] == "success", f"状态应是 success：{row['status']}")


def check_non_stream_error_still_recorded() -> None:
    token = register_and_login("alice")
    chat_module.agent = FakeAgent(error=openai.OpenAIError("upstream down"))
    try:
        r = client.post("/api/v1/chat", headers=auth(token), json={"message": "问"})
    finally:
        chat_module.agent = REAL_CHAT_AGENT

    expect(r.status_code == 503, f"上游不可用应返回 503，实际 {r.status_code}：{r.text}")
    expect(
        "AI 服务暂时不可用" == r.json()["detail"],
        f"错误信息不该透传上游原文：{r.json()}",
    )

    rows = fetch_records()
    expect(len(rows) == 1, f"失败的提问也要留一行，实际 {len(rows)} 行")
    expect(rows[0]["status"] == "error", f"状态应是 error：{rows[0]['status']}")
    expect(
        rows[0]["error_message"] == "AI 服务暂时不可用",
        f"失败原因不对：{rows[0]['error_message']}",
    )


def check_stream_records_turn() -> None:
    token = register_and_login("alice")

    tool_start = {
        "type": "updates",
        "data": {
            "model": {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "list_projects", "args": {}, "id": "c1"}],
                    )
                ]
            }
        },
    }
    tool_end = {
        "type": "updates",
        "data": {
            "tools": {
                "messages": [ToolMessage(content="[]", tool_call_id="c1", name="list_projects")]
            }
        },
    }
    stream_module.agent = FakeAgent(
        chunks=[
            {"type": "messages", "data": (AIMessageChunk(content="你"), {"langgraph_node": "model"})},
            tool_start,
            tool_end,
            {
                "type": "messages",
                "data": (
                    AIMessageChunk(
                        content="好",
                        usage_metadata={"input_tokens": 11, "output_tokens": 2, "total_tokens": 13},
                    ),
                    {"langgraph_node": "model"},
                ),
            },
            {
                "type": "messages",
                "data": (
                    AIMessageChunk(
                        content="的",
                        usage_metadata={"input_tokens": 20, "output_tokens": 3, "total_tokens": 23},
                    ),
                    {"langgraph_node": "model"},
                ),
            },
        ]
    )
    try:
        r = client.post("/api/v1/chat/stream", headers=auth(token), json={"message": "问"})
    finally:
        stream_module.agent = REAL_STREAM_AGENT

    expect(r.status_code == 200, f"流式应返回 200，实际 {r.status_code}：{r.text}")
    frames = parse_frames(r.text)
    expect(
        [f["type"] for f in frames] == ["delta", "status", "status", "delta", "delta", "done"],
        f"SSE 帧顺序不对：{[f['type'] for f in frames]}",
    )
    expect(
        "".join(f.get("text", "") for f in frames if f["type"] == "delta") == "你好的",
        f"正文拼接不对：{frames}",
    )
    expect(
        frames[-1]["thread_id"].startswith("user_"),
        f"done 帧要带服务端拼好的 thread_id：{frames[-1]}",
    )

    rows = fetch_records()
    expect(len(rows) == 1, f"流式也要落一行，实际 {len(rows)} 行")
    row = rows[0]
    expect(
        (row["input_tokens"], row["output_tokens"], row["total_tokens"]) == (31, 5, 36),
        f"流式 token 累加不对（11+20/2+3/13+23）："
        f"{row['input_tokens']}/{row['output_tokens']}/{row['total_tokens']}",
    )
    expect(row["tool_call_count"] == 1, f"工具次数应是 1，实际 {row['tool_call_count']}")
    expect(row["status"] == "success", f"状态应是 success：{row['status']}")


def check_stream_error_still_recorded() -> None:
    token = register_and_login("alice")
    stream_module.agent = FakeAgent(error=openai.OpenAIError("upstream down"))
    try:
        r = client.post("/api/v1/chat/stream", headers=auth(token), json={"message": "问"})
    finally:
        stream_module.agent = REAL_STREAM_AGENT

    frames = parse_frames(r.text)
    expect(
        [f["type"] for f in frames] == ["error"],
        f"流式失败应只发一个 error 帧，实际 {[f['type'] for f in frames]}",
    )
    expect(
        frames[0]["message"] == "AI 服务暂时不可用",
        f"错误文案不该透传上游原文：{frames[0]}",
    )

    rows = fetch_records()
    expect(len(rows) == 1, f"流式失败也要留一行，实际 {len(rows)} 行")
    expect(rows[0]["status"] == "error", f"状态应是 error：{rows[0]['status']}")
    expect(
        rows[0]["error_message"] == "AI 服务暂时不可用",
        f"失败原因不对：{rows[0]['error_message']}",
    )


def insert_chats(user_id: int, rows: list[dict]) -> None:
    with test_engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    "INSERT INTO assistant_chats"
                    " (user_id, thread_id, model, input_tokens, output_tokens,"
                    "  total_tokens, tool_call_count, latency, status, error_message, created_at)"
                    " VALUES (:u, :t, 'deepseek-chat', 0, 0, :tokens, :tools, :latency,"
                    "         :status, :err, :created)"
                ),
                {"u": user_id, "t": f"user_{user_id}_x", "err": None, **row},
            )


def check_admin_usage_requires_admin() -> None:
    token = register_and_login("normie")
    r = client.get("/api/v1/admin/assistant", headers=auth(token))
    expect(r.status_code == 403, f"普通用户应返回 403，实际 {r.status_code}：{r.text}")


def check_admin_usage_aggregation() -> None:
    admin_token = register_and_login("root")
    promote_to_admin("root")
    register_and_login("alice")
    register_and_login("bob")
    alice_id = user_id_of("alice")
    bob_id = user_id_of("bob")

    now = datetime.now(timezone.utc)
    insert_chats(
        alice_id,
        [
            {"tokens": 100, "tools": 1, "latency": 100, "status": "success", "created": now - timedelta(days=1)},
            {"tokens": 200, "tools": 0, "latency": 200, "status": "error", "created": now - timedelta(days=1)},
            {"tokens": 300, "tools": 2, "latency": 300, "status": "success", "created": now - timedelta(days=2)},
        ],
    )
    # bob 的记录在 10 天前：days=7 时必须被排除
    insert_chats(
        bob_id,
        [
            {"tokens": 50, "tools": 0, "latency": 500, "status": "success", "created": now - timedelta(days=10)},
        ],
    )

    r = client.get("/api/v1/admin/assistant", headers=auth(admin_token))
    expect(r.status_code == 200, f"用量接口应返回 200，实际 {r.status_code}：{r.text}")
    body = r.json()
    expect(body["total"] == 2, f"应统计 2 位用户，实际 {body['total']}")

    by_name = {row["username"]: row for row in body["items"]}
    expect(set(by_name) == {"alice", "bob"}, f"用户集合不对：{sorted(by_name)}")

    expect(
        set(by_name["alice"])
        == {
            "user_id",
            "username",
            "chat_count",
            "total_tokens",
            "tool_call_count",
            "avg_latency",
            "error_count",
            "last_used_at",
        },
        f"响应字段集合不对（不能出现 thread_id、提问内容之类的字段）：{sorted(by_name['alice'])}",
    )

    alice_row = by_name["alice"]
    expect(alice_row["chat_count"] == 3, f"alice 提问数应是 3，实际 {alice_row['chat_count']}")
    expect(alice_row["total_tokens"] == 600, f"alice token 应是 600，实际 {alice_row['total_tokens']}")
    expect(alice_row["tool_call_count"] == 3, f"alice 工具次数应是 3，实际 {alice_row['tool_call_count']}")
    expect(alice_row["avg_latency"] == 200, f"alice 平均耗时应是 200，实际 {alice_row['avg_latency']}")
    expect(alice_row["error_count"] == 1, f"alice 失败数应是 1，实际 {alice_row['error_count']}")

    summary = body["summary"]
    expect(summary["total_chats"] == 4, f"总提问数应是 4，实际 {summary['total_chats']}")
    expect(summary["total_tokens"] == 650, f"总 token 应是 650，实际 {summary['total_tokens']}")
    expect(summary["error_count"] == 1, f"总失败数应是 1，实际 {summary['error_count']}")
    expect(summary["active_users"] == 2, f"活跃用户应是 2，实际 {summary['active_users']}")

    r = client.get("/api/v1/admin/assistant", params={"days": 7}, headers=auth(admin_token))
    body = r.json()
    expect(body["total"] == 1, f"近 7 天应只剩 alice 一人，实际 {body['total']}")
    expect(
        body["summary"]["total_chats"] == 3,
        f"近 7 天提问数应是 3，实际 {body['summary']['total_chats']}",
    )
    expect(
        body["summary"]["total_tokens"] == 600,
        f"近 7 天 token 应是 600，实际 {body['summary']['total_tokens']}",
    )

    first = client.get(
        "/api/v1/admin/assistant", params={"page": 1, "page_size": 1}, headers=auth(admin_token)
    ).json()
    second = client.get(
        "/api/v1/admin/assistant", params={"page": 2, "page_size": 1}, headers=auth(admin_token)
    ).json()
    expect(first["items"][0]["username"] == "alice", "第一页应是提问最多的 alice")
    expect(second["items"][0]["username"] == "bob", "第二页应是 bob")
    expect(
        first["summary"]["total_chats"] == 4,
        "汇总必须按全量算，不能只算当前页",
    )

    r = client.get(
        "/api/v1/admin/assistant", params={"page_size": 101}, headers=auth(admin_token)
    )
    expect(r.status_code == 422, f"page_size 超过 100 应返回 422，实际 {r.status_code}")


def check_admin_usage_empty() -> None:
    admin_token = register_and_login("root")
    promote_to_admin("root")

    r = client.get("/api/v1/admin/assistant", headers=auth(admin_token))
    expect(r.status_code == 200, f"没有记录时也应返回 200，实际 {r.status_code}：{r.text}")
    body = r.json()
    expect(body["items"] == [], f"没有记录时 items 应是空数组：{body['items']}")
    expect(body["total"] == 0, f"total 应是 0，实际 {body['total']}")
    expect(
        body["summary"] == {
            "total_chats": 0,
            "total_tokens": 0,
            "error_count": 0,
            "active_users": 0,
        },
        f"没有记录时汇总应全为 0：{body['summary']}",
    )


CHECKS = [
    ("一轮提问的 token 与工具次数只算本轮", check_turn_math),
    ("record_chat 写入一行（model 取当前配置）", check_record_writes_row),
    ("非流式：落库本轮用量，不重复算历史", check_non_stream_records_turn),
    ("非流式：上游失败也留一行 error", check_non_stream_error_still_recorded),
    ("流式：累加 token 与工具次数后落库", check_stream_records_turn),
    ("流式：失败也留一行 error", check_stream_error_still_recorded),
    ("普通用户读不到助手用量 → 403", check_admin_usage_requires_admin),
    ("管理端按用户聚合、时间过滤与分页", check_admin_usage_aggregation),
    ("没有记录时返回空结果而不是报错", check_admin_usage_empty),
]


def main() -> int:
    print("=" * 62)
    print("AI Monitor — AI 助手用量验收")
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
        print("❌ 测试库里还没有表，或者还没跑 007_assistant_chats.sql\n")
        print(f"原始错误：{exc.orig}\n")
        return 1

    failures: list[str] = []

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
    chat_module.agent = REAL_CHAT_AGENT
    stream_module.agent = REAL_STREAM_AGENT
    reset_db()

    print("=" * 62)
    if failures:
        print(f"❌ {len(failures)}/{len(CHECKS)} 项未通过：")
        for name in failures:
            print(f"     · {name}")
        return 1

    print(f"全部通过 ✅ {len(CHECKS)}/{len(CHECKS)}")
    print("助手用量已闭环 —— 两条链路都记账，管理端只看数字不碰正文")
    return 0


if __name__ == "__main__":
    sys.exit(main())
