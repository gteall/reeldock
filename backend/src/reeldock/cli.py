import argparse
import getpass
import os
import sys

from reeldock.database import Database
from reeldock.runtime import Runtime
from reeldock.security import init_admin


def main():
    parser = argparse.ArgumentParser(description="ReelDock local administration")
    parser.add_argument("command", choices=["init-admin", "migrate"])
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read password from stdin for container initialization; never argv.",
    )
    args = parser.parse_args()
    runtime = Runtime()
    runtime.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    db = Database(runtime.database_path)
    try:
        db.migrate()
        if args.command == "init-admin":
            password = (
                sys.stdin.readline().rstrip("\n")
                if args.password_stdin
                else getpass.getpass("管理员密码（至少12位）: ")
            )
            init_admin(db, args.username, password)
            print("管理员已初始化。")
        else:
            print("数据库迁移完成。")
    except ValueError as error:
        parser.exit(1, str(error) + "\n")
    finally:
        db.close()


if __name__ == "__main__":
    main()
