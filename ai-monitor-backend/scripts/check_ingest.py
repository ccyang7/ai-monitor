"""接口验收：用真实 HTTP 请求跑完阶段 4（数据接入）的全部验收清单。"""

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
from app.models import Model  # noqa: E402
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

# 测试专用的价格：整数好算账。
# 刻意不用 scripts/init_models.py 里的那份 —— 常量改了不该把验收断言一起带跑
BUILTIN_MODELS = [
    ("deepseek", "deepseek-chat", "1", "2"),
    ("openai", "gpt-4o", "10", "30"),
]


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
        # 内置模型是上报算成本的前提，每轮检查都从「有价格」的状态开始
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


def read_log(log_id: int) -> dict:
    with TestSession() as db:
        row = db.execute(
            text("SELECT * FROM llm_requests WHERE id = :i"), {"i": log_id}
        ).mappings().first()
    expect(row is not None, f"日志 {log_id} 没写进数据库")
    return dict(row)


def check_ingest_basic() -> None:
    token = register_and_login("alice")
    created = new_project(token, "上报测试")
    pid, key = created["id"], created["api_key"]

    r = ingest(key)
    expect(r.status_code == 201, f"上报应返回 201，实际 {r.status_code}：{r.text}")

    body = r.json()
    expect(
        body["project_id"] == pid,
        f"请求体里没有 projectId，归属必须由 Key 推导。应落 {pid}，实际 {body['project_id']}",
    )
    expect("created_at" in body, f"响应里应有 created_at：{body}")

    log = read_log(body["id"])
    expect(log["project_id"] == pid, f"库里的归属不对：{log['project_id']}")
    expect(log["provider"] == "deepseek", f"provider 没落库：{log['provider']}")
    expect(log["model"] == "deepseek-chat", f"model 没落库：{log['model']}")
    expect(log["input"] == "什么是 Redis？", "input 没落库")
    expect(log["output"] == "Redis 是一种开源的键值数据库……", "output 没落库")
    expect(log["input_tokens"] == 100, f"input_tokens 不对：{log['input_tokens']}")
    expect(log["output_tokens"] == 200, f"output_tokens 不对：{log['output_tokens']}")
    expect(
        log["total_tokens"] == 300,
        f"接入方没传 total_tokens，应由 input + output 补齐为 300，实际 {log['total_tokens']}",
    )
    expect(log["latency"] == 1500, f"latency 不对：{log['latency']}")
    expect(log["status"] == "success", f"status 不对：{log['status']}")
    expect(log["error_message"] is None, f"成功调用不该有错误信息：{log['error_message']}")

    # 成本 = 100/1e6*1 + 200/1e6*2 = 0.0005
    expect(
        log["estimated_cost"] == Decimal("0.00050000"),
        f"成本快照不对，应是 0.0005，实际 {log['estimated_cost']}",
    )
    expect(
        abs(datetime.now(timezone.utc) - log["created_at"]) < timedelta(minutes=1),
        f"没传 timestamp 时应取服务端时间，实际落库 {log['created_at']}",
    )

    # 上报之后首页要能立刻看到「最近调用」—— last_called_at 由 llm_requests 聚合而来
    items = client.get("/api/v1/projects", headers=auth(token)).json()["items"]
    target = next(i for i in items if i["id"] == pid)
    expect(
        target["last_called_at"] is not None,
        "上报之后项目列表的 last_called_at 必须有值，否则首页会一直显示「还没有数据上报」",
    )


def check_ingest_minimal() -> None:
    token = register_and_login("bob")
    key = new_project(token, "宽进严出")["api_key"]

    # 只给必填字段：宽进严出的「宽」，不该因为非关键字段缺失就丢日志
    r = client.post(
        "/api/v1/logs",
        headers=api_key_header(key),
        json={"provider": "deepseek", "model": "deepseek-chat", "latency": 800, "status": "success"},
    )
    expect(r.status_code == 201, f"只传必填字段应成功，实际 {r.status_code}：{r.text}")

    log = read_log(r.json()["id"])
    expect(log["input"] is None, f"没传 input 应为 NULL，实际 {log['input']!r}")
    expect(log["output"] is None, f"没传 output 应为 NULL，实际 {log['output']!r}")
    expect(log["input_tokens"] == 0, f"没传 token 应默认 0，实际 {log['input_tokens']}")
    expect(log["total_tokens"] == 0, f"两个 token 都是 0 时 total 应为 0，实际 {log['total_tokens']}")


def check_cost_rules() -> None:
    token = register_and_login("carol")
    created = new_project(token, "成本测试")
    pid, key = created["id"], created["api_key"]

    # ① 整数账：1,000,000 输入 + 500,000 输出，deepseek-chat 是 1 / 2 元每百万
    r = ingest(key, model="deepseek-chat", input_tokens=1_000_000, output_tokens=500_000)
    expect(
        read_log(r.json()["id"])["estimated_cost"] == Decimal("2.00000000"),
        "1M 输入 + 0.5M 输出应恰好 2 元",
    )

    # ② 接入方显式传了 total_tokens 就以它为准，不要自己重算
    r = ingest(key, input_tokens=100, output_tokens=200, total_tokens=999)
    expect(
        read_log(r.json()["id"])["total_tokens"] == 999,
        "传了 total_tokens 就不该用 input + output 覆盖它",
    )

    # ③ 未知模型：日志照常写入，成本留空 —— 数据完整性优先于成本计算
    r = ingest(key, model="someone-elses-model", provider="unknown")
    expect(r.status_code == 201, f"未知模型不该被拒绝，实际 {r.status_code}：{r.text}")
    log = read_log(r.json()["id"])
    expect(
        log["estimated_cost"] is None,
        f"匹配不到价格时成本应为 NULL，实际 {log['estimated_cost']}",
    )
    expect(log["model"] == "someone-elses-model", "未知模型的日志也要完整落库")

    # ④ 用户自建模型生效
    with TestSession() as db:
        db.add(
            Model(
                owner_id=pid_owner(db, pid),
                provider="deepseek",
                model_name="deepseek-chat",
                input_price=Decimal("100"),
                output_price=Decimal("200"),
                is_builtin=False,
            )
        )
        db.commit()

    r = ingest(key, input_tokens=1_000_000, output_tokens=0)
    own_price_log_id = r.json()["id"]
    expect(
        read_log(own_price_log_id)["estimated_cost"] == Decimal("100.00000000"),
        "自建模型的价格应优先生效（同名时自建比内置更贴合用户意图）",
    )

    # ⑤ 别人的自建模型对本项目无效：应退回内置价格
    other_token = register_and_login("dave")
    other_created = new_project(other_token, "别人的项目")
    with TestSession() as db:
        db.add(
            Model(
                owner_id=other_created["id"],
                provider="deepseek",
                model_name="deepseek-chat",
                input_price=Decimal("9999"),
                output_price=Decimal("9999"),
                is_builtin=False,
            )
        )
        db.commit()

    r = ingest(key, input_tokens=1_000_000, output_tokens=0)
    expect(
        read_log(r.json()["id"])["estimated_cost"] == Decimal("100.00000000"),
        "别人自建模型的价格不该被我的项目用到，应继续用自己的那份",
    )

    # ⑥ 价格改动只影响之后的调用：改价后新写的要用新价，
    #    而改价前写好的那条记录成本原样不动（快照的意义所在）
    with TestSession() as db:
        db.execute(
            text("UPDATE models SET input_price = 500 WHERE owner_id = :o"),
            {"o": pid_owner(db, pid)},
        )
        db.commit()

    r = ingest(key, input_tokens=1_000_000, output_tokens=0)
    expect(
        read_log(r.json()["id"])["estimated_cost"] == Decimal("500.00000000"),
        "改价后新写入的调用要用新价格",
    )
    expect(
        read_log(own_price_log_id)["estimated_cost"] == Decimal("100.00000000"),
        "改价把历史记录的成本也改了 —— estimated_cost 必须是写入时的快照",
    )


def pid_owner(db, project_id: int) -> int:
    return db.execute(
        text("SELECT owner_id FROM projects WHERE id = :p"), {"p": project_id}
    ).scalar()


def check_timestamp() -> None:
    token = register_and_login("erin")
    key = new_project(token, "时间测试")["api_key"]

    # ① 带时区的 timestamp 原样落库
    sent = datetime(2026, 9, 1, 3, 30, tzinfo=timezone.utc)
    r = ingest(key, timestamp=sent.isoformat())
    stored = read_log(r.json()["id"])["created_at"]
    expect(stored == sent, f"传了 timestamp 就该用它，实际 {stored}")

    # ② 不带时区的按 UTC 解释，不能按服务器本地时区 —— 差 8 小时是常见症状
    r = ingest(key, timestamp="2026-09-02T03:30:00")
    stored = read_log(r.json()["id"])["created_at"]
    expect(
        stored == datetime(2026, 9, 2, 3, 30, tzinfo=timezone.utc),
        f"不带时区的 timestamp 应约定为 UTC，实际 {stored}",
    )


def check_auth() -> None:
    token = register_and_login("frank")
    created = new_project(token, "认证测试")
    key = created["api_key"]

    r = ingest(key)
    expect(r.status_code == 201, "正常 Key 应能上报")
    unauthorized_detail = ingest("garbage").json()["detail"]

    # 缺头必须 401：APIKeyHeader 的 auto_error 打开时这里会是 403
    r = client.post(
        "/api/v1/logs",
        json={"provider": "deepseek", "model": "deepseek-chat", "latency": 1, "status": "success"},
    )
    expect(r.status_code == 401, f"不带 Key 应返回 401，实际 {r.status_code}")
    expect(r.json()["detail"] == unauthorized_detail, "各种认证失败的错误信息必须一致，防 Key 探测")

    r = ingest("proj_not-a-real-key")
    expect(r.status_code == 401, f"无效 Key 应返回 401，实际 {r.status_code}")
    expect(r.json()["detail"] == unauthorized_detail, "无效 Key 的错误信息不一致")
    expect("proj_not-a-real-key" not in r.text, "401 响应里回显了 Key 内容！")

    # 吊销后立刻失效
    key_id = client.get(
        f"/api/v1/projects/{created['id']}/api-keys", headers=auth(token)
    ).json()[0]["id"]
    r = client.delete(f"/api/v1/api-keys/{key_id}", headers=auth(token))
    expect(r.status_code == 204, f"吊销 Key 应返回 204，实际 {r.status_code}")

    r = ingest(key)
    expect(r.status_code == 401, f"吊销的 Key 应返回 401，实际 {r.status_code}")
    expect(r.json()["detail"] == unauthorized_detail, "吊销 Key 的错误信息不一致")

    # 过期
    fresh = new_project(token, "过期测试")
    with TestSession() as db:
        db.execute(
            text("UPDATE api_keys SET expired_at = :t WHERE project_id = :p"),
            {"t": datetime.now(timezone.utc) - timedelta(hours=1), "p": fresh["id"]},
        )
        db.commit()

    r = ingest(fresh["api_key"])
    expect(r.status_code == 401, f"过期的 Key 应返回 401，实际 {r.status_code}")
    expect(r.json()["detail"] == unauthorized_detail, "过期 Key 的错误信息不一致")

    # 项目被删后，Key 随外键级联消失，同样 401
    gone = new_project(token, "待删除")
    r = client.delete(f"/api/v1/projects/{gone['id']}", headers=auth(token))
    expect(r.status_code == 204, f"删除项目应返回 204，实际 {r.status_code}")

    r = ingest(gone["api_key"])
    expect(r.status_code == 401, f"项目已删除时 Key 应返回 401，实际 {r.status_code}")


def check_isolation() -> None:
    token_a = register_and_login("grace")
    token_b = register_and_login("heidi")
    project_a = new_project(token_a, "A 的项目")
    project_b = new_project(token_b, "B 的项目")

    for _ in range(3):
        r = ingest(project_a["api_key"])
        expect(r.status_code == 201, f"上报应成功，实际 {r.status_code}")

    with TestSession() as db:
        rows = db.execute(
            text("SELECT project_id, COUNT(*) AS c FROM llm_requests GROUP BY project_id")
        ).all()

    expect(len(rows) == 1, f"库里应只有 1 个项目有日志，实际有 {len(rows)} 个")
    expect(
        rows[0].project_id == project_a["id"],
        f"A 的 Key 上报的数据落到了项目 {rows[0].project_id}（A 是 {project_a['id']}）",
    )
    expect(rows[0].c == 3, f"A 应收到 3 条，实际 {rows[0].c}")

    # B 的项目一条都没有 —— 拿 A 的 Key 刷不出 B 的数据
    with TestSession() as db:
        count_b = db.execute(
            text("SELECT COUNT(*) FROM llm_requests WHERE project_id = :p"),
            {"p": project_b["id"]},
        ).scalar()
    expect(count_b == 0, f"B 的项目不该有数据，实际 {count_b} 条")


def check_failed_call() -> None:
    token = register_and_login("ivan")
    key = new_project(token, "失败上报")["api_key"]

    # 失败调用也要能上报：失败数据是后面异常分析的重要来源
    r = ingest(key, status="error", output=None, error="Request timeout after 30s", latency=30000)
    expect(r.status_code == 201, f"失败调用也应能上报，实际 {r.status_code}：{r.text}")

    log = read_log(r.json()["id"])
    expect(log["status"] == "error", f"status 不对：{log['status']}")
    expect(log["error_message"] == "Request timeout after 30s", "错误信息没落库")
    expect(log["output"] is None, "失败的调用不该有输出")

    # 成功却带了 error 信息也不报错 —— 宽进，不因为字段冗余拒绝数据
    r = ingest(key, status="success", error="不该出现的信息")
    expect(r.status_code == 201, f"冗余字段不该被拒，实际 {r.status_code}")


def check_validation() -> None:
    token = register_and_login("judy")
    key = new_project(token, "校验测试")["api_key"]

    cases = [
        ("缺 provider", {"provider": None}),
        ("缺 model", {"model": None}),
        ("空 model", {"model": ""}),
        ("缺 latency", {"latency": None}),
        ("缺 status", {"status": None}),
        ("latency 为负", {"latency": -1}),
        ("input_tokens 为负", {"input_tokens": -1}),
        ("total_tokens 为负", {"total_tokens": -1}),
        ("status 非法值", {"status": "timeout"}),
        ("model 超长", {"model": "x" * 101}),
    ]

    for label, override in cases:
        payload = {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "latency": 1000,
            "status": "success",
        }
        payload.update(override)
        payload = {k: v for k, v in payload.items() if v is not None}
        r = client.post("/api/v1/logs", headers=api_key_header(key), json=payload)
        expect(
            r.status_code == 422,
            f"{label}应返回 422（入口拦住，而不是写库时才炸），实际 {r.status_code}：{r.text}",
        )

    # 被拒的上报不能留下半条记录
    with TestSession() as db:
        count = db.execute(text("SELECT COUNT(*) FROM llm_requests")).scalar()
    expect(count == 0, f"校验失败的上报不该写库，实际有 {count} 条")


CHECKS = [
    ("上报成功 / 字段落库 / 项目由 Key 推导", check_ingest_basic),
    ("宽进严出：只传必填字段也能上报", check_ingest_minimal),
    ("成本快照：内置价格 / 自建优先 / 未知模型留空 / 改价不动历史", check_cost_rules),
    ("timestamp：不传取服务端时间、不带时区按 UTC 算", check_timestamp),
    ("★ 认证：缺 Key / 无效 / 吊销 / 过期一律 401", check_auth),
    ("★ 项目隔离：数据只落自己的项目", check_isolation),
    ("失败调用也记录完整", check_failed_call),
    ("边界值校验（422 而不是 500）", check_validation),
]


def main() -> int:
    print("=" * 62)
    print("AI Monitor — 数据接入验收（阶段 4）")
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
    print("阶段 4 数据接入完成 —— 上报、鉴权、成本快照、项目隔离都已就位")
    return 0


if __name__ == "__main__":
    sys.exit(main())
