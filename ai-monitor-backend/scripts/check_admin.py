"""管理后台验收：管理员依赖链、内置模型与用户的增删改、级联删除的范围边界。"""

import os
import re
import sys
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


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

BUILTIN_MODELS = [
    ("deepseek", "deepseek-chat", "1", "2", None, "active"),
    ("openai", "gpt-4o", "10", "30", None, "active"),
    # 已禁用的内置模型：管理员必须仍能看到它，否则禁用之后无法恢复
    ("legacy", "retired-model", "0", "0", None, "disabled"),
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
                "TRUNCATE users, projects, api_keys, models, llm_requests,"
                " assistant_chats RESTART IDENTITY CASCADE"
            )
        )
        for (
            provider,
            name,
            input_price,
            output_price,
            cache_hit_price,
            status,
        ) in BUILTIN_MODELS:
            conn.execute(
                text(
                    "INSERT INTO models (provider, model_name, input_price,"
                    " output_price, cache_hit_price, is_builtin, status)"
                    " VALUES (:p, :n, :i, :o, :c, true, :s)"
                ),
                {
                    "p": provider,
                    "n": name,
                    "i": input_price,
                    "o": output_price,
                    "c": cache_hit_price,
                    "s": status,
                },
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


def promote_to_admin(username: str) -> None:
    # 第一个管理员就是用这条 SQL 生成的；这里也顺便验证了「改完立刻生效」——
    # get_current_user 每次请求都从库里读 user，token 不用重新签发
    with test_engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET role = 'admin' WHERE username = :u"),
            {"u": username},
        )


def check_requires_login() -> None:
    r = client.get("/api/v1/admin/models")
    expect(r.status_code == 401, f"未登录应返回 401，实际 {r.status_code}：{r.text}")


def check_plain_user_forbidden() -> None:
    token = register_and_login("normie")
    r = client.get("/api/v1/admin/models", headers=auth(token))
    expect(r.status_code == 403, f"普通用户应返回 403，实际 {r.status_code}：{r.text}")
    expect(
        "管理员" in r.json()["detail"],
        f"403 的 detail 应说明需要管理员权限，实际：{r.json()}",
    )

    r = client.post(
        "/api/v1/admin/models",
        headers=auth(token),
        json={
            "provider": "x",
            "model_name": "x",
            "input_price": "1",
            "output_price": "1",
        },
    )
    expect(
        r.status_code == 403,
        f"普通用户不能新增内置模型，实际 {r.status_code}：{r.text}",
    )


def check_admin_lists_builtin_models() -> None:
    token = register_and_login("root")
    promote_to_admin("root")

    r = client.get("/api/v1/admin/models", headers=auth(token))
    expect(r.status_code == 200, f"管理员应返回 200，实际 {r.status_code}：{r.text}")
    body = r.json()
    expect(isinstance(body, list), f"应返回数组，实际 {type(body).__name__}")

    names = [m["model_name"] for m in body]
    expect(
        set(names) == {"deepseek-chat", "gpt-4o", "retired-model"},
        f"内置模型列表不对（应含已禁用的那条）：{names}",
    )
    expect(names == sorted(names), f"应按 model_name 升序，实际 {names}")
    expect(all(m["is_builtin"] for m in body), "管理员列表里混进了用户自建模型")
    expect(
        all(m["used_by_projects"] == [] for m in body),
        "管理员列表不应带用户的项目名 —— 那属于用户数据",
    )
    expect(
        any(m["status"] == "disabled" for m in body),
        "已禁用的内置模型也必须列出来，否则禁用之后无法恢复",
    )


def check_admin_creates_builtin_model() -> None:
    token = register_and_login("root")
    promote_to_admin("root")

    r = client.post(
        "/api/v1/admin/models",
        headers=auth(token),
        json={
            "provider": "deepseek",
            "model_name": "deepseek-v5",
            "input_price": "2.5",
            "output_price": "8",
            "cache_hit_price": "0.5",
        },
    )
    expect(r.status_code == 201, f"新增内置模型应返回 201，实际 {r.status_code}：{r.text}")
    body = r.json()
    expect(body["is_builtin"] is True, f"is_builtin 应是 true：{body}")
    expect(body["status"] == "active", f"新模型状态应是 active：{body['status']}")

    with test_engine.begin() as conn:
        owner_id = conn.execute(
            text("SELECT owner_id FROM models WHERE model_name = 'deepseek-v5'")
        ).scalar_one()
    expect(owner_id is None, f"内置模型的 owner_id 必须是 NULL，实际 {owner_id!r}")


def check_builtin_name_unique_including_disabled() -> None:
    token = register_and_login("root")
    promote_to_admin("root")

    # retired-model 是 disabled 的那条。内置唯一索引不筛 status，
    # 重名校验也必须不筛 —— 两边口径不一致就会以 500 失败
    r = client.post(
        "/api/v1/admin/models",
        headers=auth(token),
        json={
            "provider": "legacy",
            "model_name": "retired-model",
            "input_price": "0",
            "output_price": "0",
        },
    )
    expect(
        r.status_code == 400,
        f"与已禁用的内置模型重名应返回 400（不是 500），实际 {r.status_code}：{r.text}",
    )


def check_admin_updates_builtin_model() -> None:
    token = register_and_login("root")
    promote_to_admin("root")

    with test_engine.begin() as conn:
        model_id = conn.execute(
            text("SELECT id FROM models WHERE model_name = 'deepseek-chat'")
        ).scalar_one()

    r = client.patch(
        f"/api/v1/admin/models/{model_id}",
        headers=auth(token),
        json={"input_price": "1.5"},
    )
    expect(r.status_code == 200, f"改价应返回 200，实际 {r.status_code}：{r.text}")
    body = r.json()
    expect(body["input_price"] == 1.5, f"input_price 没改成 1.5：{body['input_price']}")
    expect(
        body["output_price"] == 2.0,
        f"没传的字段不能被清掉：{body['output_price']}",
    )

    user_token = register_and_login("user1")
    r = client.post(
        "/api/v1/models",
        headers=auth(user_token),
        json={
            "provider": "private",
            "model_name": "my-private-llm",
            "input_price": "1",
            "output_price": "1",
        },
    )
    expect(r.status_code == 201, f"用户自建模型应成功：{r.text}")
    own_id = r.json()["id"]

    r = client.patch(
        f"/api/v1/admin/models/{own_id}",
        headers=auth(token),
        json={"input_price": "9"},
    )
    expect(
        r.status_code == 404,
        f"管理员不能修改用户的自建模型，实际 {r.status_code}：{r.text}",
    )


def check_global_unpriced_models() -> None:
    token = register_and_login("root")
    promote_to_admin("root")
    user_token = register_and_login("user1")

    r = client.post(
        "/api/v1/projects", headers=auth(user_token), json={"name": "接入项目"}
    )
    expect(r.status_code == 201, f"建项目应成功：{r.text}")
    project_id = r.json()["id"]

    with test_engine.begin() as conn:
        for _ in range(2):
            conn.execute(
                text(
                    "INSERT INTO llm_requests (project_id, provider, model, latency, status)"
                    " VALUES (:p, 'x', 'mystery-model', 100, 'success')"
                ),
                {"p": project_id},
            )

    r = client.get("/api/v1/admin/models/unpriced", headers=auth(token))
    expect(r.status_code == 200, f"未收录列表应返回 200，实际 {r.status_code}：{r.text}")
    body = r.json()
    expect(len(body) == 1, f"应只有 1 个未收录模型，实际：{body}")
    expect(body[0]["model_name"] == "mystery-model", f"模型名不对：{body[0]}")
    expect(body[0]["call_count"] == 2, f"调用次数应是 2，实际 {body[0]['call_count']}")
    expect(body[0]["user_count"] == 1, f"用户数应是 1，实际 {body[0]['user_count']}")


def check_admin_is_still_a_user() -> None:
    token = register_and_login("root2")
    promote_to_admin("root2")

    r = client.get("/api/v1/users/me", headers=auth(token))
    expect(r.status_code == 200, f"管理员读自己的信息应返回 200，实际 {r.status_code}")
    expect(r.json()["role"] == "admin", f"role 应是 admin，实际 {r.json()['role']!r}")


def check_user_list_requires_admin() -> None:
    token = register_and_login("normie")
    r = client.get("/api/v1/admin/users", headers=auth(token))
    expect(r.status_code == 403, f"普通用户应返回 403，实际 {r.status_code}：{r.text}")


def check_admin_user_list_with_activity() -> None:
    admin_token = register_and_login("root")
    promote_to_admin("root")

    user_token = register_and_login("alice")
    project_ids = []
    for name in ("项目甲", "项目乙"):
        r = client.post("/api/v1/projects", headers=auth(user_token), json={"name": name})
        expect(r.status_code == 201, f"建项目应成功：{r.text}")
        project_ids.append(r.json()["id"])

    # 项目甲两条、项目乙一条：project_count 必须按项目去重，不能数成日志条数
    with test_engine.begin() as conn:
        for project_id in (project_ids[0], project_ids[0], project_ids[1]):
            conn.execute(
                text(
                    "INSERT INTO llm_requests (project_id, provider, model, latency, status)"
                    " VALUES (:p, 'x', 'm', 100, 'success')"
                ),
                {"p": project_id},
            )

    register_and_login("idler")

    r = client.get("/api/v1/admin/users", headers=auth(admin_token))
    expect(r.status_code == 200, f"用户列表应返回 200，实际 {r.status_code}：{r.text}")
    body = r.json()
    expect(body["total"] == 3, f"应有 3 个用户，实际 {body['total']}")

    by_name = {u["username"]: u for u in body["items"]}
    expect(
        set(by_name) == {"root", "alice", "idler"},
        f"用户集合不对：{sorted(by_name)}",
    )
    expect(
        set(by_name["alice"])
        == {
            "id",
            "username",
            "email",
            "role",
            "created_at",
            "project_count",
            "last_called_at",
        },
        f"响应字段集合不对（不能带出 password_hash 或项目名）：{sorted(by_name['alice'])}",
    )
    expect(
        by_name["alice"]["project_count"] == 2,
        f"alice 的项目数应是 2（按项目去重，不是日志条数），实际 {by_name['alice']['project_count']}",
    )
    expect(
        by_name["alice"]["last_called_at"] is not None,
        "alice 有日志，last_called_at 不该是 null",
    )
    expect(
        by_name["idler"]["project_count"] == 0,
        f"没有项目的用户项目数应是 0，实际 {by_name['idler']['project_count']}",
    )
    expect(
        by_name["idler"]["last_called_at"] is None,
        "没有上报过的用户 last_called_at 必须是 null，不能用注册时间冒充",
    )
    expect(by_name["root"]["role"] == "admin", f"管理员自己在列表里也要标记 admin")

    ids = [u["id"] for u in body["items"]]
    expect(ids == sorted(ids, reverse=True), f"应按注册时间倒序，实际 id 顺序 {ids}")


def check_admin_user_list_search_and_paging() -> None:
    admin_token = register_and_login("root")
    promote_to_admin("root")
    for name in ("alice", "bob", "carol"):
        register_and_login(name)

    r = client.get("/api/v1/admin/users", params={"q": "ali"}, headers=auth(admin_token))
    expect(r.status_code == 200, f"搜索应返回 200，实际 {r.status_code}：{r.text}")
    expect(
        [u["username"] for u in r.json()["items"]] == ["alice"],
        f"搜索 ali 应只命中 alice，实际 {r.json()['items']}",
    )

    # % 若没被转义会变成通配符，匹配到所有用户
    r = client.get("/api/v1/admin/users", params={"q": "%"}, headers=auth(admin_token))
    expect(
        r.json()["total"] == 0,
        f"「%」必须当普通字符搜索（LIKE 转义失效），实际命中 {r.json()['total']}",
    )

    first = client.get(
        "/api/v1/admin/users", params={"page": 1, "page_size": 2}, headers=auth(admin_token)
    ).json()
    second = client.get(
        "/api/v1/admin/users", params={"page": 2, "page_size": 2}, headers=auth(admin_token)
    ).json()
    expect(first["total"] == 4, f"总数应是 4，实际 {first['total']}")
    expect(len(first["items"]) == 2 and len(second["items"]) == 2, "每页应有 2 条")
    ids_first = {u["id"] for u in first["items"]}
    ids_second = {u["id"] for u in second["items"]}
    expect(not (ids_first & ids_second), "两页出现了同一个用户")
    expect(len(ids_first | ids_second) == 4, "分页合起来没有覆盖全部用户")

    r = client.get(
        "/api/v1/admin/users", params={"page_size": 101}, headers=auth(admin_token)
    )
    expect(r.status_code == 422, f"page_size 超过 100 应返回 422，实际 {r.status_code}")


def user_id_of(username: str) -> int:
    with test_engine.begin() as conn:
        return conn.execute(
            text("SELECT id FROM users WHERE username = :u"), {"u": username}
        ).scalar_one()


def model_id_of(model_name: str) -> int:
    with test_engine.begin() as conn:
        return conn.execute(
            text("SELECT id FROM models WHERE model_name = :n AND is_builtin"),
            {"n": model_name},
        ).scalar_one()


def check_admin_deletes_user_cascade() -> None:
    admin_token = register_and_login("root")
    promote_to_admin("root")
    victim_token = register_and_login("victim")
    bystander_token = register_and_login("bystander")
    victim_id = user_id_of("victim")
    bystander_id = user_id_of("bystander")

    r = client.post(
        "/api/v1/projects", headers=auth(victim_token), json={"name": "待删项目"}
    )
    expect(r.status_code == 201, f"victim 建项目应成功：{r.text}")
    victim_project_id = r.json()["id"]

    r = client.post(
        "/api/v1/projects", headers=auth(bystander_token), json={"name": "旁观者的项目"}
    )
    expect(r.status_code == 201, f"bystander 建项目应成功：{r.text}")
    bystander_project_id = r.json()["id"]

    r = client.post(
        "/api/v1/models",
        headers=auth(victim_token),
        json={
            "provider": "private",
            "model_name": "victim-private",
            "input_price": "1",
            "output_price": "1",
        },
    )
    expect(r.status_code == 201, f"victim 建自建模型应成功：{r.text}")

    # 四张关联表各留一条，删用户后必须全部归零
    with test_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO api_keys (project_id, key_hash, key_prefix)"
                " VALUES (:p, 'hash-victim', 'proj_vict')"
            ),
            {"p": victim_project_id},
        )
        conn.execute(
            text(
                "INSERT INTO llm_requests (project_id, provider, model, latency, status)"
                " VALUES (:p, 'x', 'm', 100, 'success')"
            ),
            {"p": victim_project_id},
        )
        conn.execute(
            text(
                "INSERT INTO assistant_chats (user_id, thread_id, model, latency, status)"
                " VALUES (:u, 'user_victim_x', 'm', 100, 'success')"
            ),
            {"u": victim_id},
        )
        conn.execute(
            text(
                "INSERT INTO assistant_chats (user_id, thread_id, model, latency, status)"
                " VALUES (:u, 'user_bystander_x', 'm', 100, 'success')"
            ),
            {"u": bystander_id},
        )

    r = client.delete(f"/api/v1/admin/users/{victim_id}", headers=auth(admin_token))
    expect(r.status_code == 204, f"删除用户应返回 204，实际 {r.status_code}：{r.text}")

    with test_engine.begin() as conn:
        def count(sql: str, **params) -> int:
            return conn.execute(text(sql), params).scalar_one()

        expect(count("SELECT count(*) FROM users WHERE id = :id", id=victim_id) == 0,
               "用户行没被删掉")
        expect(
            count("SELECT count(*) FROM projects WHERE owner_id = :id", id=victim_id) == 0,
            "项目没被级联删除",
        )
        expect(
            count(
                "SELECT count(*) FROM llm_requests WHERE project_id = :id",
                id=victim_project_id,
            )
            == 0,
            "调用日志没被级联删除",
        )
        expect(
            count("SELECT count(*) FROM api_keys WHERE project_id = :id", id=victim_project_id)
            == 0,
            "API Key 没被级联删除",
        )
        expect(
            count("SELECT count(*) FROM assistant_chats WHERE user_id = :id", id=victim_id)
            == 0,
            "助手用量没被级联删除",
        )
        expect(
            count("SELECT count(*) FROM models WHERE owner_id = :id", id=victim_id) == 0,
            "用户的自建模型没被级联删除",
        )
        # 别人的数据一条都不能少 —— 级联删除的范围必须止于被删用户
        expect(
            count("SELECT count(*) FROM projects WHERE owner_id = :id", id=bystander_id) == 1,
            "删用户时误删了别人的项目",
        )
        expect(
            count(
                "SELECT count(*) FROM assistant_chats WHERE user_id = :id", id=bystander_id
            )
            == 1,
            "删用户时误删了别人的助手用量",
        )

    r = client.get("/api/v1/users/me", headers=auth(victim_token))
    expect(r.status_code == 401, "被删用户的旧 JWT 必须立刻失效")


def check_admin_cannot_delete_self() -> None:
    token = register_and_login("root")
    promote_to_admin("root")
    root_id = user_id_of("root")

    r = client.delete(f"/api/v1/admin/users/{root_id}", headers=auth(token))
    expect(r.status_code == 400, f"删除自己应返回 400，实际 {r.status_code}：{r.text}")
    expect("自己" in r.json()["detail"], f"错误信息应说清原因：{r.json()}")

    r = client.get("/api/v1/users/me", headers=auth(token))
    expect(r.status_code == 200, "被拒绝之后管理员本人必须毫发无损")

    # 删别人（包括另一个管理员）是允许的：操作者还在，管理员不会被删光
    register_and_login("root2")
    promote_to_admin("root2")
    r = client.delete(
        f"/api/v1/admin/users/{user_id_of('root2')}", headers=auth(token)
    )
    expect(r.status_code == 204, f"删除另一个管理员应成功，实际 {r.status_code}：{r.text}")


def check_admin_delete_user_guards() -> None:
    admin_token = register_and_login("root")
    promote_to_admin("root")
    user_token = register_and_login("normie")

    r = client.delete(
        f"/api/v1/admin/users/{user_id_of('normie')}", headers=auth(user_token)
    )
    expect(r.status_code == 403, f"普通用户不能删号，实际 {r.status_code}：{r.text}")

    r = client.delete("/api/v1/admin/users/999999", headers=auth(admin_token))
    expect(r.status_code == 404, f"删不存在的用户应返回 404，实际 {r.status_code}：{r.text}")


def check_admin_deletes_builtin_model() -> None:
    admin_token = register_and_login("root")
    promote_to_admin("root")
    user_token = register_and_login("alice")
    r = client.post("/api/v1/projects", headers=auth(user_token), json={"name": "p"})
    project_id = r.json()["id"]
    gpt_id = model_id_of("gpt-4o")

    # 先有一条历史调用：删模型不能动历史日志，而它应当出现在「未收录」里
    with test_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO llm_requests (project_id, provider, model, latency, status)"
                " VALUES (:p, 'openai', 'gpt-4o', 100, 'success')"
            ),
            {"p": project_id},
        )

    r = client.delete(f"/api/v1/admin/models/{gpt_id}", headers=auth(admin_token))
    expect(r.status_code == 204, f"删除内置模型应返回 204，实际 {r.status_code}：{r.text}")

    names = [m["model_name"] for m in client.get(
        "/api/v1/admin/models", headers=auth(admin_token)
    ).json()]
    expect("gpt-4o" not in names, f"删掉的模型还在目录里：{names}")

    with test_engine.begin() as conn:
        kept = conn.execute(
            text("SELECT count(*) FROM llm_requests WHERE model = 'gpt-4o'")
        ).scalar_one()
    expect(kept == 1, "删除模型不能动历史日志")

    unpriced = client.get("/api/v1/admin/models/unpriced", headers=auth(admin_token)).json()
    expect(
        any(item["model_name"] == "gpt-4o" and item["call_count"] == 1 for item in unpriced),
        f"删掉价格后该模型应进入未收录列表：{unpriced}",
    )

    r = client.delete(f"/api/v1/admin/models/{gpt_id}", headers=auth(admin_token))
    expect(r.status_code == 404, f"重复删除应返回 404，实际 {r.status_code}：{r.text}")


def check_admin_delete_model_guards() -> None:
    admin_token = register_and_login("root")
    promote_to_admin("root")
    user_token = register_and_login("alice")

    r = client.post(
        "/api/v1/models",
        headers=auth(user_token),
        json={
            "provider": "private",
            "model_name": "alice-private",
            "input_price": "1",
            "output_price": "1",
        },
    )
    expect(r.status_code == 201, f"用户自建模型应成功：{r.text}")
    own_id = r.json()["id"]

    r = client.delete(f"/api/v1/admin/models/{own_id}", headers=auth(admin_token))
    expect(
        r.status_code == 404,
        f"管理员不能借 id 删用户的自建模型，实际 {r.status_code}：{r.text}",
    )

    r = client.delete(
        f"/api/v1/admin/models/{model_id_of('gpt-4o')}", headers=auth(user_token)
    )
    expect(r.status_code == 403, f"普通用户不能删内置模型，实际 {r.status_code}：{r.text}")

    r = client.delete("/api/v1/admin/models/999999", headers=auth(admin_token))
    expect(r.status_code == 404, f"删不存在的模型应返回 404，实际 {r.status_code}：{r.text}")


CHECKS = [
    ("未登录访问管理接口 → 401", check_requires_login),
    ("普通用户访问管理接口 → 403", check_plain_user_forbidden),
    ("管理员读到内置模型（含已禁用）", check_admin_lists_builtin_models),
    ("管理员新增内置模型（owner_id 为 NULL）", check_admin_creates_builtin_model),
    ("内置重名检查覆盖已禁用行", check_builtin_name_unique_including_disabled),
    ("管理员改价，且改不到用户自建模型", check_admin_updates_builtin_model),
    ("全局未收录模型按调用量统计", check_global_unpriced_models),
    ("管理员仍是普通用户，/users/me 正常", check_admin_is_still_a_user),
    ("普通用户读不到用户列表 → 403", check_user_list_requires_admin),
    ("用户列表带项目数与最近调用，不含隐私字段", check_admin_user_list_with_activity),
    ("用户搜索（LIKE 转义）与分页", check_admin_user_list_search_and_paging),
    ("删除用户：级联清空本人数据，不碰别人", check_admin_deletes_user_cascade),
    ("删除用户：不能删自己，可以删其他管理员", check_admin_cannot_delete_self),
    ("删除用户：普通用户 403、不存在 404", check_admin_delete_user_guards),
    ("删除内置模型：历史日志保留，之后进入未收录", check_admin_deletes_builtin_model),
    ("删除模型：不能删用户自建模型，普通用户 403", check_admin_delete_model_guards),
]


def main() -> int:
    print("=" * 62)
    print("AI Monitor — 管理后台验收")
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
        print("❌ 测试库里还没有表，或者还没跑 006_admin_role.sql\n")
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
    print("管理后台地基就位 —— 管理员依赖链、内置模型列表、用户侧接口零改动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
