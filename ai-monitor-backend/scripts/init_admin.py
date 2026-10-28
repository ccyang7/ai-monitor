"""创建管理员账号；用户名已存在时只提升角色，可反复执行。"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402
from app.security import MAX_PASSWORD_BYTES, hash_password  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="创建管理员账号；用户名已存在时只提升角色，不修改密码。"
    )
    parser.add_argument("--username", required=True, help="登录名，3-50 个字符")
    parser.add_argument("--email", help="可选邮箱")
    parser.add_argument(
        "--password",
        help="不传则交互输入；传参只建议用于自动化，密码会留在 shell 历史里",
    )
    return parser.parse_args()


def read_password(provided: str | None) -> str | None:
    if provided is None:
        password = getpass.getpass("管理员密码（输入时不显示）：")
        if password != getpass.getpass("再输入一次确认："):
            print("❌ 两次输入的密码不一致")
            return None
    else:
        password = provided

    if len(password) < 6:
        print("❌ 密码至少 6 个字符")
        return None
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        print(f"❌ 密码超过 {MAX_PASSWORD_BYTES} 字节（一个汉字算 3 字节）")
        return None
    return password


def main() -> int:
    args = parse_args()
    username = args.username.strip()

    if not 3 <= len(username) <= 50:
        print("❌ 用户名长度必须是 3-50 个字符")
        return 1

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))

        if user is not None:
            if user.role == "admin":
                print(f"✅ {username} 已经是管理员，无需修改")
                return 0

            # 已有账号只提升角色：重置密码会让对方正在使用的登录口令失效
            user.role = "admin"
            db.commit()
            print(f"✅ 已将 {username}（id={user.id}）提升为管理员，密码未修改")
            return 0

        password = read_password(args.password)
        if password is None:
            return 1

        user = User(
            username=username,
            password_hash=hash_password(password),
            email=args.email or None,
            role="admin",
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            print("❌ 用户名或邮箱已被占用")
            return 1

        print(f"✅ 已创建管理员 {username}（id={user.id}）")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OperationalError as exc:
        print(
            "❌ 连不上数据库，先确认 DATABASE_URL"
            "（本地在 ai-monitor-backend/.env.development，生产由 compose 注入）"
        )
        print(f"   原始错误：{exc.orig}")
        sys.exit(1)
    except ProgrammingError as exc:
        print("❌ users 表不存在，先执行建表脚本：")
        print("   psql -U ai_monitor -d ai_monitor -f sql/schema.sql")
        print(f"   原始错误：{exc.orig}")
        sys.exit(1)
