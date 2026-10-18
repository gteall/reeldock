import argparse
import getpass
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from contextlib import nullcontext
from pathlib import Path

from reeldock.backup import backup, restore
from reeldock.database import Database
from reeldock.runtime import Runtime
from reeldock.security import init_admin
from reeldock.state import StateError, head_revision, inspect_state, state_lock


def main():
    parser = argparse.ArgumentParser(description="ReelDock local administration")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("init-admin")
    admin.add_argument("--username", default="admin")
    admin.add_argument("--password-stdin", action="store_true")
    commands.add_parser("migrate")
    commands.add_parser("serve")
    commands.add_parser("healthcheck")
    save = commands.add_parser("backup")
    save.add_argument("--output", required=True, type=Path)
    load = commands.add_parser("restore")
    load.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    runtime = Runtime()
    os.umask(0o077)
    try:
        if args.command == "serve":
            from reeldock.app import create_app
            from reeldock.server import serve

            serve(create_app(runtime))
            return
        if args.command == "healthcheck":
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:8000/api/health", timeout=2) as response:
                if json.load(response).get("status") != "ok":
                    raise StateError("healthcheck_failed")
            return
        if args.command == "backup":
            print(json.dumps(backup(runtime.data_dir, args.output)))
            return
        if args.command == "restore":
            print(json.dumps(restore(args.input, runtime.data_dir)))
            return
        checked = inspect_state(runtime.database_path, runtime.data_dir / "master.key")
        online_admin = args.command == "init-admin" and checked["schema"] == head_revision()
        with nullcontext() if online_admin else state_lock(runtime.data_dir):
            inspect_state(runtime.database_path, runtime.data_dir / "master.key")
            db = Database(runtime.database_path)
            try:
                if not online_admin:
                    db.migrate()
                if args.command == "init-admin":
                    password = (
                        sys.stdin.readline().rstrip("\n")
                        if args.password_stdin
                        else getpass.getpass("管理员密码（至少8位）: ")
                    )
                    init_admin(db, args.username, password)
                    print("管理员已初始化。")
                else:
                    print("数据库迁移完成。")
            finally:
                db.close()
    except (StateError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    except (OSError, sqlite3.Error, urllib.error.URLError):
        parser.exit(1, "state_operation_failed\n")


if __name__ == "__main__":
    main()
