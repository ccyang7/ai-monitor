"""接口验收：模型管理的可见性、增删改、禁用、跨用户隔离与未配价提示。"""

import os
import re
import sys
from decimal import Decimal
from pathlib import Path

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

# 测试专用的内置模型：整数好算账，deepseek-chat 带缓存价、gpt-4o 不带 ——
# 列表要能区分「有缓存档」和「无缓存档」两种展示
BUILTIN_MODELS = [
    ("deepseek", "deepseek-chat", "1", "2", "0.5"),
    ("openai", "gpt-4o", "10", "30", None),
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
        for provider, name, input_price, output_price, cache_hit_price in BUILTIN_MODELS:
            conn.execute(
                text(
                    "INSERT INTO models (provider, model_name, input_price,"
                    " output_price, cache_hit_price, is_builtin)"
                    " VALUES (:p, :n, :i, :o, :c, true)"
                ),
                {
                    "p": provider,
                    "n": name,
                    "i": input_price,
                    "o": output_price,
                    "c": cache_hit_price,
                },
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


def post_model(token: str, **overrides):
    payload = {
        "provider": "deepseek",
        "model_name": "my-model",
        "input_price": 5,
        "output_price": 15,
        "cache_hit_price": None,
    }
    payload.update(overrides)
    return client.post("/api/v1/models", headers=auth(token), json=payload)


def list_models(token: str) -> list[dict]:
    r = client.get("/api/v1/models", headers=auth(token))
    expect(r.status_code == 200, f"模型列表应返回 200，实际 {r.status_code}：{r.text}")
    return r.json()["items"]


def list_unpriced(token: str) -> list[dict]:
    r = client.get("/api/v1/models", headers=auth(token))
    expect(r.status_code == 200, f"模型列表应返回 200，实际 {r.status_code}：{r.text}")
    body = r.json()
    # 键必须在，哪怕值是空列表 —— 前端直接读它渲染，缺键就是契约违约
    expect("unpriced_models" in body, "响应必须始终带 unpriced_models 键")
    return body["unpriced_models"]


def find_builtin(token: str, model_name: str) -> dict:
    return next(i for i in list_models(token) if i["model_name"] == model_name)


def ingest(key: str, model: str, input_tokens: int = 100, output_tokens: int = 200):
    payload = {
        "provider": "deepseek",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency": 1000,
        "status": "success",
    }
    return client.post("/api/v1/logs", headers=api_key_header(key), json=payload)


def read_cost(log_id: int) -> Decimal | None:
    with TestSession() as db:
        return db.execute(
            text("SELECT estimated_cost FROM llm_requests WHERE id = :i"),
            {"i": log_id},
        ).scalar()


def check_list_visibility() -> None:
    alice = register_and_login("alice")
    bob = register_and_login("bob")

    r = post_model(alice, model_name="alice-model")
    expect(r.status_code == 201, f"建自建模型应 201，实际 {r.status_code}：{r.text}")
    post_model(bob, model_name="bob-model")

    items = list_models(alice)
    names = [i["model_name"] for i in items]

    expect("deepseek-chat" in names and "gpt-4o" in names, f"内置模型人人可见，实际只有 {names}")
    expect("alice-model" in names, "自己的自建模型应可见")
    expect("bob-model" not in names, "不该看到别人的自建模型")
    expect(len(items) == 3, f"alice 应看到 2 内置 + 1 自建，实际 {len(items)} 条：{names}")

    by_name = {i["model_name"]: i for i in items}
    expect(
        by_name["deepseek-chat"]["cache_hit_price"] == 0.5,
        f"有缓存档的模型要带回真实数字，实际 {by_name['deepseek-chat']['cache_hit_price']}",
    )
    expect(
        by_name["gpt-4o"]["cache_hit_price"] is None,
        "无缓存档必须回 null（与「价格是 0」是两回事）",
    )
    expect(by_name["deepseek-chat"]["is_builtin"] is True, "内置标记不对")
    expect(by_name["alice-model"]["is_builtin"] is False, "自建标记不对")

    # 内置排在前：列表顺序是「系统基准价在前，自己的模型在后」
    expect(
        names.index("deepseek-chat") < names.index("alice-model"),
        f"内置模型应排在自建之前，实际顺序 {names}",
    )


def check_used_by_projects() -> None:
    alice = register_and_login("alice")
    project_a = new_project(alice, "A 项目")
    new_project(alice, "B 项目")

    # A 项目用过 deepseek-chat；B 项目从没用过
    r = ingest(project_a["api_key"], "deepseek-chat")
    expect(r.status_code == 201, f"上报应 201，实际 {r.status_code}：{r.text}")

    # 另一个用户的项目也用同一个模型 —— 这是「事实」，但不该出现在 alice 的列表里
    carol = register_and_login("carol")
    project_c = new_project(carol, "C 项目")
    ingest(project_c["api_key"], "deepseek-chat")

    by_name = {i["model_name"]: i for i in list_models(alice)}
    expect(
        by_name["deepseek-chat"]["used_by_projects"] == ["A 项目"],
        f"只应包含自己用过的项目（且不能是别人家的 C 项目），"
        f"实际 {by_name['deepseek-chat']['used_by_projects']}",
    )
    expect(
        by_name["gpt-4o"]["used_by_projects"] == [],
        "没人用过的模型应是空列表，不是 null",
    )


def check_create() -> None:
    alice = register_and_login("alice")

    r = post_model(
        alice, model_name="priced", input_price="3.5", output_price="7", cache_hit_price="0.7"
    )
    expect(r.status_code == 201, f"建模型应 201，实际 {r.status_code}：{r.text}")
    body = r.json()
    expect(body["cache_hit_price"] == 0.7, f"缓存价应回读 0.7，实际 {body['cache_hit_price']}")
    expect(body["is_builtin"] is False, "自建模型的 is_builtin 应是 false")
    expect(body["used_by_projects"] == [], "刚建的模型不该有在用的项目")

    r = post_model(alice, model_name="no-cache")
    expect(r.status_code == 201, f"不传缓存价应 201，实际 {r.status_code}：{r.text}")
    expect(r.json()["cache_hit_price"] is None, "不传缓存价 = 无缓存档")

    # 与内置同名是合法的 —— 这正是「改内置价走副本」的流程：副本只有同名，
    # 上报才能匹配到它；两条之间的取舍由「自建优先」排序兜底
    r = post_model(alice, model_name="deepseek-chat")
    expect(
        r.status_code == 201,
        f"与内置模型同名应 201（副本语义），实际 {r.status_code}：{r.text}",
    )

    # 但自己列表里不能有两条同名 —— 价格取哪个会变成歧义
    r = post_model(alice, model_name="priced")
    expect(r.status_code == 400, f"与自己已有模型同名应 400，实际 {r.status_code}：{r.text}")
    expect("同名" in r.json()["detail"], f"错误信息应提到同名：{r.json()['detail']}")

    cases = [
        ("输入价为负", {"model_name": "neg", "input_price": -1}),
        ("输出价为负", {"model_name": "neg2", "output_price": "-0.01"}),
        ("缓存价为负", {"model_name": "neg3", "cache_hit_price": "-0.5"}),
        ("小数位超过 6 位", {"model_name": "precise", "input_price": "1.1234567"}),
        ("名字超长", {"model_name": "x" * 101}),
        ("缺 model_name", {"model_name": None}),
    ]
    for label, override in cases:
        payload = {
            "provider": "deepseek",
            "model_name": "x",
            "input_price": 1,
            "output_price": 1,
            "cache_hit_price": None,
        }
        payload.update(override)
        payload = {k: v for k, v in payload.items() if v is not None}
        r = client.post("/api/v1/models", headers=auth(alice), json=payload)
        expect(
            r.status_code == 422,
            f"{label}应返回 422（入口拦住），实际 {r.status_code}：{r.text}",
        )


def check_update() -> None:
    alice = register_and_login("alice")
    bob = register_and_login("bob")

    alice_model_id = post_model(alice, model_name="alice-model").json()["id"]
    bob_model_id = post_model(bob, model_name="bob-model").json()["id"]
    builtin_id = find_builtin(alice, "deepseek-chat")["id"]

    # 改内置 → 404：内置模型所有用户共享，改一次影响全平台的成本基准
    r = client.patch(
        f"/api/v1/models/{builtin_id}", headers=auth(alice), json={"input_price": 0.01}
    )
    expect(r.status_code == 404, f"改内置模型应 404（不是 403，不暴露它是内置的），实际 {r.status_code}")

    r = client.patch(
        f"/api/v1/models/{bob_model_id}", headers=auth(alice), json={"input_price": 0.01}
    )
    expect(r.status_code == 404, f"改别人的模型应 404，实际 {r.status_code}")

    # 改自己的 → 200，没传的字段不许被动
    r = client.patch(
        f"/api/v1/models/{alice_model_id}",
        headers=auth(alice),
        json={"input_price": "8.5", "cache_hit_price": "1.25"},
    )
    expect(r.status_code == 200, f"改自己的模型应 200，实际 {r.status_code}：{r.text}")
    body = r.json()
    expect(body["input_price"] == 8.5, f"input_price 应改成 8.5，实际 {body['input_price']}")
    expect(body["cache_hit_price"] == 1.25, f"缓存价应改成 1.25，实际 {body['cache_hit_price']}")
    expect(body["output_price"] == 15, f"没传的 output_price 不该被动，实际 {body['output_price']}")

    # cache_hit_price 传 null = 清空缓存档（exclude_unset 分得清「没传」和「传 null」）
    r = client.patch(
        f"/api/v1/models/{alice_model_id}", headers=auth(alice), json={"cache_hit_price": None}
    )
    expect(r.status_code == 200, f"清空缓存价应 200，实际 {r.status_code}：{r.text}")
    expect(r.json()["cache_hit_price"] is None, "传 null 应把缓存档清成「无」")

    # 其余字段传 null → 422 而不是 500（NOT NULL 列不能放行）
    for field in ("provider", "model_name", "input_price", "output_price"):
        r = client.patch(
            f"/api/v1/models/{alice_model_id}", headers=auth(alice), json={field: None}
        )
        expect(
            r.status_code == 422,
            f"{field} 显式传 null 应 422（NOT NULL 列），实际 {r.status_code}：{r.text}",
        )

    # 查重只在自己的自建里：与内置同名（副本语义）、与别人的同名，都不该拦
    r = client.patch(
        f"/api/v1/models/{alice_model_id}", headers=auth(alice), json={"model_name": "gpt-4o"}
    )
    expect(r.status_code == 200, f"改成与内置同名应成功（这就是副本），实际 {r.status_code}")

    r = client.patch(
        f"/api/v1/models/{alice_model_id}", headers=auth(alice), json={"model_name": "bob-model"}
    )
    expect(r.status_code == 200, f"别人的自建不占用我的命名空间，实际 {r.status_code}")

    # 名字没变的重提交 → 200：查重必须排除自己
    r = client.patch(
        f"/api/v1/models/{alice_model_id}",
        headers=auth(alice),
        json={"model_name": "bob-model"},
    )
    expect(r.status_code == 200, f"改成自己当前的名字应成功（查重排除自己），实际 {r.status_code}")

    # 自己已有同名第二条 → 400
    second_id = post_model(alice, model_name="second").json()["id"]
    r = client.patch(
        f"/api/v1/models/{second_id}", headers=auth(alice), json={"model_name": "bob-model"}
    )
    expect(r.status_code == 400, f"与自己已有的自建同名应 400，实际 {r.status_code}")


def check_delete() -> None:
    alice = register_and_login("alice")
    created = new_project(alice, "禁用测试")

    model_id = post_model(
        alice, model_name="to-disable", input_price="10", output_price="20"
    ).json()["id"]

    # 先用它上报一次：成本按 10 / 20 算
    r = ingest(created["api_key"], "to-disable", input_tokens=1_000_000, output_tokens=0)
    old_log_id = r.json()["id"]
    expect(
        read_cost(old_log_id) == Decimal("10.00000000"),
        f"禁用前应能匹配到价格，实际 {read_cost(old_log_id)}",
    )

    r = client.delete(f"/api/v1/models/{model_id}", headers=auth(alice))
    expect(r.status_code == 204, f"删除应返回 204，实际 {r.status_code}：{r.text}")

    ids = [i["id"] for i in list_models(alice)]
    expect(model_id not in ids, "禁用后不该出现在列表里")

    r = client.get(f"/api/v1/models/{model_id}", headers=auth(alice))
    expect(r.status_code == 404, f"禁用后详情应 404，实际 {r.status_code}")

    # 再上报：匹配不到价格，成本留空 —— 但历史那条的快照一分不动
    r = ingest(created["api_key"], "to-disable", input_tokens=1_000_000, output_tokens=0)
    expect(
        read_cost(r.json()["id"]) is None,
        "禁用后不该再匹配到价格（新上报成本应为 null）",
    )
    expect(
        read_cost(old_log_id) == Decimal("10.00000000"),
        "禁用不该动历史记录的快照成本",
    )

    # 删内置 → 404
    builtin_id = find_builtin(alice, "deepseek-chat")["id"]
    r = client.delete(f"/api/v1/models/{builtin_id}", headers=auth(alice))
    expect(r.status_code == 404, f"删内置模型应 404，实际 {r.status_code}")


def check_price_isolation() -> None:
    alice = register_and_login("alice")
    bob = register_and_login("bob")
    alice_project = new_project(alice, "alice 的项目")
    bob_project = new_project(bob, "bob 的项目")

    # alice 走「改内置价」的副本流程：建一个同名 gpt-4o，价格改掉
    r = post_model(alice, provider="openai", model_name="gpt-4o", input_price="999", output_price="999")
    expect(r.status_code == 201, f"建同名副本应 201，实际 {r.status_code}：{r.text}")

    # bob 上报 gpt-4o：仍按内置 10 / 30 算 —— 别人的副本对 bob 无效
    r = ingest(bob_project["api_key"], "gpt-4o", input_tokens=1_000_000, output_tokens=0)
    expect(
        read_cost(r.json()["id"]) == Decimal("10.00000000"),
        "alice 的副本不该影响 bob 的成本，bob 仍按内置价（1M × 10）",
    )

    # alice 自己上报：自建优先，按 999 算
    r = ingest(alice_project["api_key"], "gpt-4o", input_tokens=1_000_000, output_tokens=0)
    expect(
        read_cost(r.json()["id"]) == Decimal("999.00000000"),
        "同名时自建优先（副本更贴合本人的实际情况）",
    )

    # alice 列表里两条 gpt-4o 并存；「在用的项目」两条都显示 alice 的项目 ——
    # 匹配是 llm_requests.model ↔ model_name 字符串，与成本匹配同一套口径
    gpt4o = [i for i in list_models(alice) if i["model_name"] == "gpt-4o"]
    expect(len(gpt4o) == 2, f"副本与内置同名应共存在列表里，实际 {len(gpt4o)} 条")
    expect(
        all(i["used_by_projects"] == ["alice 的项目"] for i in gpt4o),
        f"同名模型在成本上就是同一个，应显示同一批项目：{gpt4o}",
    )


def check_unpriced_models() -> None:
    alice = register_and_login("alice")
    project_a = new_project(alice, "A 项目")
    project_b = new_project(alice, "B 项目")

    # 空态：什么都没上报过
    expect(list_unpriced(alice) == [], "没上报过任何模型时，提示清单应为空")

    # 上报平台里没有的模型名 → 出现在提示里，并且确实算不出成本
    r = ingest(project_a["api_key"], "mystery-model")
    expect(r.status_code == 201, f"上报应 201，实际 {r.status_code}：{r.text}")
    expect(
        read_cost(r.json()["id"]) is None,
        "未配价的模型成本就该是 null（页面上显示成「—」）",
    )
    expect(
        list_unpriced(alice)
        == [{"model_name": "mystery-model", "used_by_projects": ["A 项目"]}],
        f"上报过的未配价模型应出现在提示里，实际 {list_unpriced(alice)}",
    )

    # 上报内置模型名 → 不算未配价（内置就是那份兜底价）
    ingest(project_a["api_key"], "deepseek-chat")
    expect(
        [u["model_name"] for u in list_unpriced(alice)] == ["mystery-model"],
        "内置模型不该进提示清单",
    )

    # 两个项目用同一个新模型 → 项目名聚合；多个未配价模型 → 按名排序
    ingest(project_b["api_key"], "mystery-model")
    ingest(project_b["api_key"], "another-model")
    unpriced = list_unpriced(alice)
    expect(
        [u["model_name"] for u in unpriced] == ["another-model", "mystery-model"],
        f"多个未配价模型应按名字排序，实际 {[u['model_name'] for u in unpriced]}",
    )
    expect(
        unpriced[1]["used_by_projects"] == ["A 项目", "B 项目"],
        f"同一模型被多个项目用过要看全，实际 {unpriced[1]['used_by_projects']}",
    )

    # 补价闭环：建一条同名模型 → 提示消失、新上报立即计费
    r = post_model(alice, model_name="mystery-model", input_price="5", output_price="15")
    expect(r.status_code == 201, f"补价建模型应 201，实际 {r.status_code}：{r.text}")
    expect(
        [u["model_name"] for u in list_unpriced(alice)] == ["another-model"],
        "补完价的模型应从提示里消失",
    )
    r = ingest(project_a["api_key"], "mystery-model", input_tokens=1_000_000, output_tokens=0)
    expect(
        read_cost(r.json()["id"]) == Decimal("5.00000000"),
        f"补价后新上报应按新价格计费，实际 {read_cost(r.json()['id'])}",
    )

    # 与「在用的项目」联动：刚补价建的模型立刻显示之前上报过的项目
    row = next(i for i in list_models(alice) if i["model_name"] == "mystery-model")
    expect(
        row["used_by_projects"] == ["A 项目", "B 项目"],
        f"补价建的模型应立刻显示在用的项目，实际 {row['used_by_projects']}",
    )

    # 禁用 → 名字重回提示（新上报又匹配不到了），历史快照不动
    r = client.delete(f"/api/v1/models/{row['id']}", headers=auth(alice))
    expect(r.status_code == 204, f"禁用应 204，实际 {r.status_code}：{r.text}")
    expect(
        "mystery-model" in [u["model_name"] for u in list_unpriced(alice)],
        "禁用后该模型应重回提示清单",
    )

    # 禁用后重建同名 → 必须 201。唯一索引只约束 active 行，禁用行不占名字；
    # 索引不带 status 过滤那会儿，应用层放行、INSERT 撞索引，用户拿到 500
    r = post_model(alice, model_name="mystery-model", input_price="6", output_price="16")
    expect(
        r.status_code == 201,
        f"禁用后重建同名应 201（唯一性只管 active 行），实际 {r.status_code}：{r.text}",
    )


def check_unpriced_isolation() -> None:
    alice = register_and_login("alice")
    bob = register_and_login("bob")
    alice_project = new_project(alice, "alice 的项目")
    bob_project = new_project(bob, "bob 的项目")

    # bob 的项目上报一个平台里没有的模型名 —— 这是 bob 的事实，alice 不该看见
    ingest(bob_project["api_key"], "secret-model")
    expect(list_unpriced(alice) == [], "别人项目上报的模型名不该出现在我的提示里")
    expect(
        [u["model_name"] for u in list_unpriced(bob)] == ["secret-model"],
        "bob 自己应看得到",
    )

    # bob 自建了同名的、已配好价的模型 —— 也救不了 alice：成本匹配只认
    # 内置 + 自己的，别人的价格对自己不生效（同 find_priced_model 的口径）
    r = post_model(bob, model_name="secret-model", input_price="8", output_price="18")
    expect(r.status_code == 201, f"bob 建模型应 201，实际 {r.status_code}：{r.text}")

    r = ingest(alice_project["api_key"], "secret-model")
    expect(
        read_cost(r.json()["id"]) is None,
        "别人的同名价格不该给自己的上报用上",
    )
    expect(
        [u["model_name"] for u in list_unpriced(alice)] == ["secret-model"],
        "alice 上报后，这个名字仍应留在 alice 的提示里",
    )
    expect(list_unpriced(bob) == [], "bob 配好价之后，他自己的提示应清空")


CHECKS = [
    ("★ 列表可见性：内置 + 自己的自建，看不到别人的", check_list_visibility),
    ("used_by_projects：只含自己的项目（别人用过的也不出现）", check_used_by_projects),
    ("创建：含/不含缓存价、重名 400、负价与超精度 422", check_create),
    ("★ 修改：内置 404 / 别人的 404 / 自己的 200 / null 清缓存价", check_update),
    ("★ 禁用：列表消失、上报不再匹配、历史快照不动", check_delete),
    ("★ 价格隔离：同名副本只影响自己（改内置价走副本的实证）", check_price_isolation),
    ("★ 未配价提示：出现、口径与补价闭环（含禁用后重建同名不 500）", check_unpriced_models),
    ("★ 未配价隔离：别人的上报与别人的价格都不算数", check_unpriced_isolation),
]


def main() -> int:
    print("=" * 62)
    print("AI Monitor — 模型管理验收")
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
        print("❌ 测试库里还没有 models 表\n")
        print(f"原始错误：{exc.orig}\n")
        print("把建表脚本跑在测试库上（001、002、004、005 都要）：")
        print("   psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 \\")
        print("     -f sql/004_cache_pricing.sql")
        print("     -f sql/005_models_unique_active.sql")
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
    print("模型管理就位 —— 可见性、增删改、禁用、跨用户隔离、未配价提示都已验证")
    return 0


if __name__ == "__main__":
    sys.exit(main())
