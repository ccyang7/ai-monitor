"""接口验收：用真实 HTTP 请求跑完阶段 3 的全部验收清单。"""

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

import jwt  # noqa: E402
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


def mask_password(url: str) -> str:
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", url)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def reset_db() -> None:
    with test_engine.begin() as conn:
        conn.execute(text("TRUNCATE users, projects, api_keys RESTART IDENTITY CASCADE"))


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


def new_project(token: str, name: str, **extra) -> dict:
    r = client.post("/api/v1/projects", headers=auth(token), json={"name": name, **extra})
    expect(r.status_code == 201, f"创建项目「{name}」应返回 201，实际 {r.status_code}：{r.text}")
    return r.json()


def check_health() -> None:
    r = client.get("/health")
    expect(r.status_code == 200, f"/health 应返回 200，实际 {r.status_code}")
    expect(r.json() == {"status": "ok"}, f"/health 响应不对：{r.json()}")


def check_register_and_login() -> None:
    r = client.post(
        "/api/v1/auth/register", json={"username": "alice", "password": "pw123456"}
    )
    expect(r.status_code == 201, f"注册应返回 201，实际 {r.status_code}：{r.text}")
    expect(
        "password_hash" not in r.text,
        "注册响应里出现了 password_hash —— 响应模型泄露了敏感字段",
    )

    r = client.post(
        "/api/v1/auth/register", json={"username": "alice", "password": "pw123456"}
    )
    expect(r.status_code == 400, f"重复用户名应返回 400，实际 {r.status_code}")

    r = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "pw123456"}
    )
    expect(r.status_code == 200, f"登录应返回 200，实际 {r.status_code}")
    body = r.json()
    expect(body["token_type"] == "bearer", f"token_type 不对：{body['token_type']}")
    expect(
        body["expires_in"] == 604800,
        f"expires_in 应是 7 天的秒数 604800，实际 {body['expires_in']}",
    )

    r = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "wrong-password"}
    )
    expect(r.status_code == 401, f"密码错误应返回 401，实际 {r.status_code}")
    wrong_password_detail = r.json()["detail"]

    # 两个错误信息必须一字不差，否则攻击者能拿它枚举系统里有哪些用户名
    r = client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "wrong-password"}
    )
    expect(r.status_code == 401, f"用户不存在应返回 401，实际 {r.status_code}")
    expect(
        r.json()["detail"] == wrong_password_detail,
        "「用户不存在」和「密码错误」的提示不一样，可以被用来枚举用户名",
    )


def check_current_user() -> None:
    token = register_and_login("bob")
    r = client.get("/api/v1/users/me", headers=auth(token))
    expect(r.status_code == 200, f"/users/me 应返回 200，实际 {r.status_code}")
    body = r.json()
    expect(body["username"] == "bob", f"返回的用户不对：{body['username']}")
    expect("password_hash" not in r.text, "/users/me 泄露了 password_hash")


def check_auth_required() -> None:
    r = client.get("/api/v1/users/me")
    expect(r.status_code == 401, f"不带 token 应返回 401，实际 {r.status_code}")

    r = client.get("/api/v1/users/me", headers=auth("this-is-not-a-jwt"))
    expect(r.status_code == 401, f"无效 token 应返回 401，实际 {r.status_code}")

    expired = jwt.encode(
        {
            "sub": "1",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    r = client.get("/api/v1/users/me", headers=auth(expired))
    expect(r.status_code == 401, f"过期 token 应返回 401，实际 {r.status_code}")

    forged = jwt.encode(
        {
            "sub": "1",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        "attacker-secret",
        algorithm="HS256",
    )
    r = client.get("/api/v1/users/me", headers=auth(forged))
    expect(r.status_code == 401, f"伪造签名的 token 应返回 401，实际 {r.status_code}")

    token = register_and_login("ghost")
    with TestSession() as db:
        db.execute(text("DELETE FROM users WHERE username = 'ghost'"))
        db.commit()
    r = client.get("/api/v1/users/me", headers=auth(token))
    expect(r.status_code == 401, f"用户已删除时应返回 401，实际 {r.status_code}")


def check_project_crud() -> None:
    token = register_and_login("carol")

    created = new_project(token, "知识问答", description="测试项目", tech_stack="Python")
    expect(created["name"] == "知识问答", f"项目名不对：{created['name']}")
    expect(created["status"] == "active", f"新项目状态应是 active：{created['status']}")
    expect(created["tech_stack"] == "Python", "tech_stack 没保存上")
    expect(
        created["api_key"].startswith("proj_"),
        f"API Key 应以 proj_ 开头：{created['api_key']}",
    )
    pid = created["id"]

    r = client.get(f"/api/v1/projects/{pid}", headers=auth(token))
    expect(r.status_code == 200, f"项目详情应返回 200，实际 {r.status_code}")
    expect(r.json()["id"] == pid, "详情返回了别的项目")

    r = client.get("/api/v1/projects", headers=auth(token))
    expect(r.status_code == 200, f"项目列表应返回 200，实际 {r.status_code}")
    body = r.json()
    expect(body["total"] == 1, f"应只有 1 个项目，实际 {body['total']}")
    item = body["items"][0]
    expect(
        "last_called_at" in item and item["last_called_at"] is None,
        "没上报过数据的项目，last_called_at 应是 null —— 不能拿项目创建时间冒充",
    )
    expect(item["has_anomaly"] is False, "has_anomaly 在阶段 9 之前应是 false")

    r = client.post(
        "/api/v1/projects", headers=auth(token), json={"name": "知识问答"}
    )
    expect(r.status_code == 400, f"重名项目应返回 400，实际 {r.status_code}")
    expect("同名" in r.json()["detail"], f"错误信息应是重名相关：{r.json()['detail']}")

    r = client.delete(f"/api/v1/projects/{pid}", headers=auth(token))
    expect(r.status_code == 204, f"删除应返回 204，实际 {r.status_code}")

    r = client.get(f"/api/v1/projects/{pid}", headers=auth(token))
    expect(r.status_code == 404, f"删掉的项目应查不到（404），实际 {r.status_code}")


def check_project_quota() -> None:
    token = register_and_login("dave")

    pids = [new_project(token, f"项目{i}")["id"] for i in range(3)]

    r = client.post("/api/v1/projects", headers=auth(token), json={"name": "第四个"})
    expect(r.status_code == 400, f"超出上限应返回 400，实际 {r.status_code}")
    expect(
        "上限" in r.json()["detail"],
        f"必须是因超出上限被拒 —— 否则「因重名被拒」也会假通过。"
        f"实际信息：{r.json()['detail']}",
    )

    r = client.delete(f"/api/v1/projects/{pids[0]}", headers=auth(token))
    expect(r.status_code == 204, f"删除应返回 204，实际 {r.status_code}")
    new_project(token, "删除后新建的项目")


def check_project_update() -> None:
    token = register_and_login("ivan")
    created = new_project(token, "待改名", description="原描述", tech_stack="Python")
    pid = created["id"]

    # ① PATCH 的核心语义：没传的字段绝不能被清空
    r = client.patch(
        f"/api/v1/projects/{pid}", headers=auth(token), json={"description": None}
    )
    expect(r.status_code == 200, f"编辑项目应返回 200，实际 {r.status_code}：{r.text}")
    body = r.json()
    expect(body["description"] is None, f"description 应被清空，实际 {body['description']!r}")
    expect(
        body["name"] == "待改名" and body["tech_stack"] == "Python",
        f"只传了 description，name / tech_stack 必须原样保留。实际：{body}",
    )

    # ② 空 body 是空操作，不是错误
    r = client.patch(f"/api/v1/projects/{pid}", headers=auth(token), json={})
    expect(r.status_code == 200, f"空 body 应返回 200（空操作），实际 {r.status_code}：{r.text}")

    # ③ 改成自己当前的名字必须成功。查重漏了「排除自己」的话，用户打开编辑框
    #    什么都不改、直接保存，就会收到「同名项目已存在」
    r = client.patch(f"/api/v1/projects/{pid}", headers=auth(token), json={"name": "待改名"})
    expect(
        r.status_code == 200,
        f"改成自己当前的名字应成功（查重必须排除自己），实际 {r.status_code}：{r.text}",
    )

    # ④ 撞上自己的另一个项目才是真重名
    new_project(token, "自己另一个项目")
    r = client.patch(
        f"/api/v1/projects/{pid}", headers=auth(token), json={"name": "自己另一个项目"}
    )
    expect(r.status_code == 400, f"改成自己已有的项目名应返回 400，实际 {r.status_code}")
    expect("同名" in r.json()["detail"], f"错误信息应是重名相关：{r.json()['detail']}")

    # ⑤ 查重只在自己项目范围内：撞上别人的项目名应该放行
    other_token = register_and_login("judy")
    new_project(other_token, "别人的项目名")
    r = client.patch(
        f"/api/v1/projects/{pid}", headers=auth(token), json={"name": "别人的项目名"}
    )
    expect(
        r.status_code == 200,
        f"撞上别人的项目名不该被拒（重名只在自己项目之间判断），"
        f"实际 {r.status_code}：{r.text}",
    )

    # ⑥ updated_at 必须真的往前走。模型的 onupdate 只在 ORM 真的发出 UPDATE 时生效，
    #    改完不进库是查不出来的 —— 接口照样返回 200
    def read_updated_at() -> datetime:
        with TestSession() as db:
            return db.execute(
                text("SELECT updated_at FROM projects WHERE id = :p"), {"p": pid}
            ).scalar()

    before = read_updated_at()
    r = client.patch(
        f"/api/v1/projects/{pid}", headers=auth(token), json={"tech_stack": "Java"}
    )
    expect(r.status_code == 200, f"编辑应返回 200，实际 {r.status_code}")
    expect(r.json()["tech_stack"] == "Java", "tech_stack 没改成 Java")
    after = read_updated_at()
    expect(after > before, f"改了字段，updated_at 却没往前走：{before} -> {after}")

    # ⑦ 入口必须拦住，不能落到数据库变成 500
    for label, payload in [
        ("101 字的项目名", {"name": "x" * 101}),
        ("空项目名", {"name": ""}),
        ("显式传 name=null（等于想清空项目名）", {"name": None}),
        ("51 字的 tech_stack", {"tech_stack": "x" * 51}),
    ]:
        r = client.patch(f"/api/v1/projects/{pid}", headers=auth(token), json=payload)
        expect(r.status_code == 422, f"{label}应返回 422，实际 {r.status_code}：{r.text}")

    # ⑧ 本模块第三个越权入口
    r = client.patch(
        f"/api/v1/projects/{pid}", headers=auth(other_token), json={"name": "偷改"}
    )
    expect(r.status_code == 404, f"改他人的项目应返回 404，实际 {r.status_code}")
    still = client.get(f"/api/v1/projects/{pid}", headers=auth(token)).json()
    expect(still["name"] != "偷改", "他人的编辑请求竟然真的改掉了我的项目！")

    r = client.patch("/api/v1/projects/99999999", headers=auth(token), json={"name": "x"})
    expect(r.status_code == 404, f"编辑不存在的项目应返回 404，实际 {r.status_code}")


def check_isolation() -> None:
    token_a = register_and_login("eve")
    token_b = register_and_login("mallory")

    a_pid = new_project(token_a, "Eve 的项目")["id"]

    other = client.get(f"/api/v1/projects/{a_pid}", headers=auth(token_b))
    missing = client.get("/api/v1/projects/99999999", headers=auth(token_b))

    expect(
        other.status_code == 404,
        f"访问他人项目应返回 404（不是 403），实际 {other.status_code}",
    )
    expect(
        other.json() == missing.json(),
        f"「他人的项目」和「不存在的项目」响应体必须完全一样才能防枚举。"
        f"\n      他人项目：{other.json()}\n      不存在：  {missing.json()}",
    )

    r = client.delete(f"/api/v1/projects/{a_pid}", headers=auth(token_b))
    expect(r.status_code == 404, f"删除他人项目应返回 404，实际 {r.status_code}")

    r = client.get(f"/api/v1/projects/{a_pid}", headers=auth(token_a))
    expect(r.status_code == 200, "B 的删除请求竟然真的删掉了 A 的项目！")

    # 列表是聚合区越权的高发地，单独查一遍
    items = client.get("/api/v1/projects", headers=auth(token_b)).json()["items"]
    expect(
        a_pid not in [i["id"] for i in items],
        "B 的项目列表里出现了 A 的项目 —— 列表查询漏了 owner_id 过滤",
    )

    r = client.get(f"/api/v1/projects/{a_pid}/api-keys", headers=auth(token_b))
    expect(r.status_code == 404, f"访问他人项目的 Key 列表应返回 404，实际 {r.status_code}")


def check_api_keys() -> None:
    token = register_and_login("frank")
    created = new_project(token, "Key 测试")
    pid, plaintext = created["id"], created["api_key"]

    r = client.get(f"/api/v1/projects/{pid}/api-keys", headers=auth(token))
    expect(r.status_code == 200, f"Key 列表应返回 200，实际 {r.status_code}")
    keys = r.json()
    expect(len(keys) == 1, f"创建项目时应自动签发 1 把 Key，实际 {len(keys)}")
    expect("api_key" not in keys[0], "Key 列表接口泄露了明文！明文只能在创建时返回一次")
    expect("key_hash" not in keys[0], "Key 列表接口泄露了哈希！")
    expect(
        plaintext.startswith(keys[0]["key_prefix"]),
        f"key_prefix 应是明文的前缀。明文 {plaintext[:16]}… / 前缀 {keys[0]['key_prefix']}",
    )

    # 只能绕过接口直接查库：从响应里看不出存的是哈希还是明文
    with TestSession() as db:
        stored = db.execute(
            text("SELECT key_hash FROM api_keys WHERE project_id = :p"), {"p": pid}
        ).scalar()
    expect(stored != plaintext, "数据库里存了明文 Key！")
    expect(
        len(stored) == 64 and all(c in "0123456789abcdef" for c in stored),
        f"key_hash 应该是 64 位十六进制的 SHA-256，实际：{stored}",
    )

    r = client.post(f"/api/v1/projects/{pid}/api-keys", headers=auth(token))
    expect(r.status_code == 201, f"新建 Key 应返回 201，实际 {r.status_code}")
    second_plaintext = r.json()["api_key"]
    expect(second_plaintext != plaintext, "新建的 Key 和原来那把一样，随机数没起作用")
    expect(second_plaintext.startswith("proj_"), "新 Key 也应以 proj_ 开头")

    keys = client.get(f"/api/v1/projects/{pid}/api-keys", headers=auth(token)).json()
    expect(len(keys) == 2, f"应有 2 把有效 Key，实际 {len(keys)}")

    first_key_id = keys[-1]["id"]
    r = client.delete(f"/api/v1/api-keys/{first_key_id}", headers=auth(token))
    expect(r.status_code == 204, f"吊销 Key 应返回 204，实际 {r.status_code}")

    remaining = client.get(f"/api/v1/projects/{pid}/api-keys", headers=auth(token)).json()
    expect(len(remaining) == 1, f"吊销后应剩 1 把有效 Key，实际 {len(remaining)}")

    # 第二个越权入口：路径里没有 project_id，很容易漏掉归属校验
    other_token = register_and_login("grace")
    other_pid = new_project(other_token, "别人的项目")["id"]
    other_key_id = client.get(
        f"/api/v1/projects/{other_pid}/api-keys", headers=auth(other_token)
    ).json()[0]["id"]

    r = client.delete(f"/api/v1/api-keys/{other_key_id}", headers=auth(token))
    expect(r.status_code == 404, f"吊销他人的 Key 应返回 404，实际 {r.status_code}")

    still_there = client.get(
        f"/api/v1/projects/{other_pid}/api-keys", headers=auth(other_token)
    ).json()
    expect(len(still_there) == 1, "他人的 Key 竟然被吊销了！")


def check_validation() -> None:
    token = register_and_login("heidi")

    r = client.post("/api/v1/projects", headers=auth(token), json={"name": "x" * 101})
    expect(
        r.status_code == 422,
        f"101 字的项目名应返回 422（入口拦住），实际 {r.status_code}。"
        "如果是 500，说明 schema 的 max_length 没和数据库 VARCHAR 对齐",
    )

    r = client.post("/api/v1/projects", headers=auth(token), json={"name": ""})
    expect(r.status_code == 422, f"空项目名应返回 422，实际 {r.status_code}")

    r = client.post(
        "/api/v1/projects",
        headers=auth(token),
        json={"name": "正常项目", "tech_stack": "x" * 51},
    )
    expect(r.status_code == 422, f"51 字的 tech_stack 应返回 422，实际 {r.status_code}")

    r = client.post("/api/v1/auth/register", json={"username": "shortpw", "password": "123"})
    expect(r.status_code == 422, f"3 位密码应返回 422，实际 {r.status_code}")

    r = client.post("/api/v1/auth/register", json={"username": "ab", "password": "pw123456"})
    expect(r.status_code == 422, f"2 个字符的用户名应返回 422，实际 {r.status_code}")

    # 入口不挡的话 bcrypt 会抛 ValueError，变成 500
    r = client.post(
        "/api/v1/auth/register", json={"username": "longpw", "password": "a" * 73}
    )
    expect(
        r.status_code == 422,
        f"73 字节的密码应返回 422，实际 {r.status_code}。"
        "如果是 500，说明没挡 bcrypt 的 72 字节上限",
    )

    # 25 个汉字 = 75 字节 > 72 —— 上限是字节不是字符
    r = client.post(
        "/api/v1/auth/register", json={"username": "cnpw", "password": "密" * 25}
    )
    expect(
        r.status_code == 422,
        f"25 个汉字的密码（75 字节）应返回 422，实际 {r.status_code}。"
        "注意 bcrypt 的上限是字节不是字符，一个汉字 3 字节",
    )

    # 空 email 归一成 NULL，两个空串不该撞唯一约束。
    # 用户名别用短的：撞的会是 username 的 min_length，跟 email 无关
    r1 = client.post(
        "/api/v1/auth/register",
        json={"username": "emptyemail1", "password": "pw123456", "email": ""},
    )
    r2 = client.post(
        "/api/v1/auth/register",
        json={"username": "emptyemail2", "password": "pw123456", "email": ""},
    )
    expect(
        r1.status_code == 201 and r2.status_code == 201,
        f"两个空 email 不该冲突（应归一成 NULL）。"
        f"实际 {r1.status_code} / {r2.status_code}，"
        f"出错字段：{r1.json().get('detail', r1.text)}",
    )


CHECKS = [
    ("健康检查回归（阶段 2 的东西没坏）", check_health),
    ("注册与登录", check_register_and_login),
    ("当前用户信息 /users/me", check_current_user),
    ("未登录访问一律 401", check_auth_required),
    ("项目创建 / 查看 / 列表 / 删除", check_project_crud),
    ("项目数量上限与配额释放", check_project_quota),
    ("项目编辑（PATCH 语义与重名边界）", check_project_update),
    ("★ 用户隔离：拿不到别人的项目", check_isolation),
    ("API Key 生成 / 列表 / 吊销 / 只存哈希", check_api_keys),
    ("边界值校验（422 而不是 500）", check_validation),
]


def main() -> int:
    print("=" * 62)
    print("AI Monitor — API 功能验收")
    print("=" * 62)
    print(f"测试库：{mask_password(TEST_DATABASE_URL)}")
    print()

    try:
        reset_db()
    except OperationalError as exc:
        print("❌ 连不上测试库\n")
        print(f"原始错误：{exc.orig}\n")
        print("在容器里建库（用 ai_monitor，不是 postgres）：")
        print("   docker exec -it <容器名> psql -U ai_monitor -d ai_monitor -h 127.0.0.1 \\")
        print("     -c \"CREATE DATABASE ai_monitor_test OWNER ai_monitor;\"")
        print()
        print("然后把建表脚本跑在这个库上：")
        print("   docker exec -i <容器名> psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 \\")
        print("     -f /tmp/schema.sql")
        return 1
    except ProgrammingError as exc:
        print("❌ 测试库里还没有表\n")
        print(f"原始错误：{exc.orig}\n")
        print("在测试库上跑一遍建表脚本：")
        print("   docker exec -i <容器名> psql -U ai_monitor -d ai_monitor_test -h 127.0.0.1 \\")
        print("     -f /tmp/schema.sql")
        return 1

    failures: list[str] = []

    for name, check in CHECKS:
        reset_db()  # 每项检查都从空库开始
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
    print("阶段 3 全部完成 —— 认证、项目 CRUD + 编辑、API Key、用户隔离都已就位")
    return 0


if __name__ == "__main__":
    sys.exit(main())
