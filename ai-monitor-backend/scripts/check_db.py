"""数据库地基验收：连通性、表结构、表权限、三级写入冒烟。"""

import re
import sys
from pathlib import Path

# 把项目根目录（ai-monitor-backend/）加进模块搜索路径 ——
# Python 默认只加了「脚本所在目录」scripts/，那样 import app 会找不到
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 终端默认按 GBK 解码程序输出，不强制成 UTF-8 中文就会乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402
from app.models import ApiKey, Project, User  # noqa: E402

# DEBUG=true 时 engine 配了 echo=True，会把每条 SQL 都打出来把结论冲乱，这里关掉
engine.echo = False

MODELS = {"users": User, "projects": Project, "api_keys": ApiKey}


def mask_password(url: str) -> str:
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", url)


def describe_column(column: dict) -> str:
    type_name = str(column["type"])
    # PG 的 timestamptz 在 inspector 里显示成光秃秃的 TIMESTAMP，
    # 时区信息在单独的属性上。补个 TZ 后缀，免得误以为没带时区
    if getattr(column["type"], "timezone", False):
        type_name += " TZ"
    return type_name


def smoke_test() -> bool:
    # 不只看权限位：权限位很容易两头猜错（序列的 USAGE 是「无」但 UPDATE 是「有」时，
    # nextval() 照样能取到 id）。插一条再回滚是唯一绕不过去的验收。
    # 顺序必须是 users → projects → api_keys，外键指向决定
    print("写一条试试（事务内进行，最后回滚，不会留下脏数据）")

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, password_hash)"
                    " VALUES (:u, :p) RETURNING id"
                ),
                {"u": "__smoke_test__", "p": "x"},
            ).scalar()
            project_id = conn.execute(
                text(
                    "INSERT INTO projects (owner_id, name)"
                    " VALUES (:o, :n) RETURNING id"
                ),
                {"o": user_id, "n": "__smoke_test__"},
            ).scalar()
            conn.execute(
                text(
                    "INSERT INTO api_keys (project_id, key_hash, key_prefix)"
                    " VALUES (:p, :h, :pre)"
                ),
                {"p": project_id, "h": "__smoke_test__", "pre": "smoke"},
            )
        except Exception as exc:
            print(f"   ❌ 写入失败：{type(exc).__name__}: {exc}\n")
            print("   报错里出现 'permission denied for table xxx' 是表权限问题，")
            print("   出现 'permission denied for sequence xxx' 是序列权限问题。")
            return False
        finally:
            trans.rollback()

    print(f"   ✓ 用户 → 项目 → 密钥 三级写入全部成功（id: {user_id} → {project_id}）")
    print("   ✓ 已回滚，测试数据没有留下")
    print()
    return True


def main() -> int:
    print("=" * 62)
    print("AI Monitor — 数据库连通性检查")
    print("=" * 62)
    print(f"连接目标：{mask_password(settings.database_url)}")
    print()

    try:
        with engine.connect() as conn:
            pg_version = conn.execute(text("SELECT version()")).scalar_one()
    except OperationalError as exc:
        print("❌ 连不上数据库\n")
        print(f"原始错误：{exc.orig}\n")
        print("按这个顺序排查：")
        print("  1. 虚拟机里的 PostgreSQL 在跑吗？ → sudo systemctl status postgresql")
        print("  2. .env 里的 DATABASE_URL 填对了吗？（IP / 密码 / 库名）")
        print("  3. 虚拟机允许远程连接吗？ → postgresql.conf 里 listen_addresses = '*'")
        print("  4. 虚拟机允许你这台机器访问吗？ → pg_hba.conf 加一行 host all all 0.0.0.0/0 scram-sha-256")
        print("  5. 虚拟机防火墙放行 5432 端口了吗？")
        return 1

    print("✅ 连接成功")
    print(f"   {pg_version.split(',')[0]}")
    print()

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing = [name for name in MODELS if name not in existing_tables]

    if missing:
        print(f"❌ 缺少这些表：{', '.join(missing)}\n")
        print("在虚拟机上执行建表脚本：")
        print("   psql -U ai_monitor -d ai_monitor -f 001_init.sql")
        return 1

    print("✅ 三张表都在")
    print()

    # 不能省：inspector 查的是系统表，表是别人建的、当前用户毫无权限时上面也照样全绿
    with engine.connect() as conn:
        denied = []
        for table_name in MODELS:
            granted = conn.execute(
                text(
                    "SELECT has_table_privilege(:t, 'SELECT'),"
                    "       has_table_privilege(:t, 'INSERT'),"
                    "       has_table_privilege(:t, 'UPDATE'),"
                    "       has_table_privilege(:t, 'DELETE')"
                ),
                {"t": table_name},
            ).one()

            # 序列权限是单独一套，has_table_privilege 查不到。
            # nextval() 认 USAGE 和 UPDATE 中的任意一个（前者是新写法，
            # 后者是 PG 10 之前的老写法）—— 只查 USAGE 会把配好的人误判成没配
            sequence = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table_name}
            ).scalar()
            sequence_ok = True
            if sequence is not None:
                sequence_ok = conn.execute(
                    text(
                        "SELECT has_sequence_privilege(:s, 'USAGE')"
                        "    OR has_sequence_privilege(:s, 'UPDATE')"
                    ),
                    {"s": sequence},
                ).scalar()

            if not all(granted) or not sequence_ok:
                denied.append((table_name, granted, sequence_ok))

    if denied:
        print("❌ 当前用户对这些表没有完整读写权限")
        for table_name, granted, sequence_ok in denied:
            flags = " / ".join(
                f"{name}={'有' if ok else '无'}"
                for name, ok in zip(("SELECT", "INSERT", "UPDATE", "DELETE"), granted)
            )
            if not sequence_ok:
                flags += "  /  自增序列=无"
            print(f"   {table_name:<12}{flags}")
        print()

        # 缺哪个提示哪个 —— 混成一句「没权限」，用户会去改一个已经改好的地方
        missing_tables = [t for t, granted, _ in denied if not all(granted)]
        missing_sequences = [t for t, granted, seq in denied if all(granted) and not seq]

        if missing_tables:
            print(f"① 表权限缺失：{', '.join(missing_tables)}")
            print("   原因：表是别的用户建的（owner 不是当前用户）。")
            print("         CREATE DATABASE ... OWNER ai_monitor 只给了「数据库本身」，")
            print("         库里已有的表归建它的那个人，不归库 owner。")
            print("   修复（PG 跑在 Docker 里，容器名用 docker ps 查）：")
            print("     docker exec -it <容器名> psql -U postgres -d ai_monitor \\")
            print("       -c \"GRANT ALL ON ALL TABLES IN SCHEMA public TO ai_monitor;\"")
            print()

        if missing_sequences:
            print(f"② 自增序列权限缺失：{', '.join(missing_sequences)}")
            print("   表和序列是两套独立的权限系统：GRANT ALL ON ALL TABLES 管不到序列。")
            print("   插入时 PG 要调 nextval() 从序列取 id，它认 USAGE 和 UPDATE")
            print("   两个权限中的任意一个，两个都没有才会报：")
            print("     permission denied for sequence users_id_seq")
            print("   修复（注意是 SEQUENCES，不是 TABLES）：")
            print("     docker exec -it <容器名> psql -U postgres -d ai_monitor \\")
            print("       -c \"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO ai_monitor;\"")
            print()

        print("另一条路：把 owner 改成 ai_monitor，一次到位。")
        print("         （改表 owner 会连带改掉它自增序列的 owner，不用单独处理）")
        print("     docker exec -it <容器名> psql -U postgres -d ai_monitor \\")
        print("       -c \"ALTER TABLE users OWNER TO ai_monitor;\" \\")
        print("       -c \"ALTER TABLE projects OWNER TO ai_monitor;\" \\")
        print("       -c \"ALTER TABLE api_keys OWNER TO ai_monitor;\"")
        print()
        print("   删表重建也行（表里没数据时最干净），")
        print("   重建时要用 ai_monitor 执行 001_init.sql，别再用 postgres。")
        return 1

    print("✅ 读写权限正常")
    print()

    if not smoke_test():
        return 1

    drifted = []

    for table_name, model in MODELS.items():
        columns = inspector.get_columns(table_name, comment=True)
        db_columns = {col["name"] for col in columns}
        model_columns = {col.name for col in model.__table__.columns}

        print(f"   {table_name}（{len(columns)} 个字段）")
        for col in columns:
            nullable = "可空" if col["nullable"] else "必填"
            comment = col.get("comment") or ""
            print(f"       {col['name']:<14}{describe_column(col):<22}{nullable:<5} {comment}")

        only_in_db = db_columns - model_columns
        only_in_model = model_columns - db_columns
        if only_in_db or only_in_model:
            drifted.append(table_name)
            print("       ⚠️ 与 ORM 模型不一致：")
            if only_in_db:
                print(f"          数据库里有、模型里没有：{', '.join(sorted(only_in_db))}")
            if only_in_model:
                print(f"          模型里有、数据库里没有：{', '.join(sorted(only_in_model))}")
        else:
            print("       ✓ 字段与 ORM 模型一致")
        print()

    if drifted:
        print("=" * 62)
        print(f"⚠️ 这些表和 ORM 模型对不上：{', '.join(drifted)}")
        print("   改表结构要同时改两处：sql/001_init.sql 和 app/models/")
        return 1

    print("=" * 62)
    print("地基就绪 ✅ 表结构、权限、ORM 映射全部正常")
    print("接口行为是否正常，用 scripts/check_api.py 验（那是另一套检查）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
