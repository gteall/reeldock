import io

import pytest
from sqlalchemy import select

from reeldock import cli
from reeldock.database import Database
from reeldock.models import Admin, AuthSession, Configuration, Event, Task
from reeldock.security import init_admin, reset_admin_password

OLD_PASSWORD = "local-test-password-strong"
NEW_PASSWORD = "pass1234"


def run_reset(monkeypatch, runtime, *options):
    monkeypatch.setattr(cli, "Runtime", lambda: runtime)
    monkeypatch.setattr("sys.argv", ["reeldock", "reset-password", *options])
    cli.main()


def login(client, password, username="admin"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_online_cli_reset_preserves_data_and_revokes_all_sessions(
    client, runtime, config_body, monkeypatch, capsys
):
    assert client.put("/api/config", json=config_body).status_code == 200
    task = client.post(
        "/api/tasks",
        json={"package_path": "/incoming/Example", "idempotency_key": "before-reset"},
    ).json()["task_id"]
    key = (runtime.data_dir / "master.key").read_bytes()
    db = client.app.state.db
    with db.sessions.begin() as session:
        encrypted = session.get(Configuration, 1).encrypted
        session.get(Admin, 1).username = "nas-admin"
    first_cookie = dict(client.cookies)
    client.cookies.clear()
    assert login(client, OLD_PASSWORD, "nas-admin").status_code == 200
    second_cookie = dict(client.cookies)
    answers = iter([NEW_PASSWORD, NEW_PASSWORD])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: next(answers))

    def no_migration(self):
        pytest.fail("Online password reset must not migrate or acquire the service lock")

    monkeypatch.setattr(Database, "migrate", no_migration)
    run_reset(monkeypatch, runtime)
    output = capsys.readouterr()
    assert "密码已重置" in output.out
    assert NEW_PASSWORD not in output.out + output.err
    for cookie in (first_cookie, second_cookie):
        client.cookies.clear()
        client.cookies.update(cookie)
        assert client.get("/api/auth/session").status_code == 401
    assert login(client, OLD_PASSWORD, "nas-admin").status_code == 401
    assert login(client, NEW_PASSWORD, "nas-admin").status_code == 200
    assert client.get("/api/config").json()["revision"] == 1
    with db.sessions.begin() as session:
        assert session.get(Configuration, 1).encrypted == encrypted
        assert session.get(Task, task).status == "queued"
        event = session.scalar(select(Event).where(Event.code == "administrator_password_reset"))
        assert event.details == {}
    assert (runtime.data_dir / "master.key").read_bytes() == key


def test_stdin_reset_clears_login_lockout(client, runtime, monkeypatch, capsys):
    for _ in range(5):
        assert login(client, "wrong-password").status_code == 401
    assert login(client, OLD_PASSWORD).status_code == 429
    monkeypatch.setattr("sys.stdin", io.StringIO(NEW_PASSWORD + "\r\n"))
    run_reset(monkeypatch, runtime, "--password-stdin")
    assert NEW_PASSWORD not in capsys.readouterr().out
    assert login(client, NEW_PASSWORD).status_code == 200


@pytest.mark.parametrize("password", ["", "1234567"])
def test_short_or_empty_password_keeps_existing_password_and_session(
    client, runtime, monkeypatch, password, capsys
):
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    with pytest.raises(SystemExit) as error:
        run_reset(monkeypatch, runtime, "--password-stdin")
    assert error.value.code == 1
    assert "密码至少 8 位" in capsys.readouterr().err
    assert client.get("/api/auth/session").status_code == 200
    assert login(client, OLD_PASSWORD).status_code == 200


def test_confirmation_mismatch_keeps_existing_password(client, runtime, monkeypatch, capsys):
    answers = iter([NEW_PASSWORD, "different-password"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: next(answers))
    with pytest.raises(SystemExit) as error:
        run_reset(monkeypatch, runtime)
    assert error.value.code == 1
    assert "两次输入的密码不一致" in capsys.readouterr().err
    assert client.get("/api/auth/session").status_code == 200
    assert login(client, OLD_PASSWORD).status_code == 200


def test_cancelled_prompt_keeps_existing_password(client, runtime, monkeypatch, capsys):
    def cancel(_):
        raise EOFError

    monkeypatch.setattr(cli.getpass, "getpass", cancel)
    with pytest.raises(SystemExit) as error:
        run_reset(monkeypatch, runtime)
    assert error.value.code == 1
    assert "已取消" in capsys.readouterr().err
    assert client.get("/api/auth/session").status_code == 200


def test_reset_does_not_create_missing_admin(runtime, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(NEW_PASSWORD + "\n"))
    with pytest.raises(SystemExit) as error:
        run_reset(monkeypatch, runtime, "--password-stdin")
    assert error.value.code == 1
    assert "请先运行 init-admin" in capsys.readouterr().err
    db = Database(runtime.database_path)
    try:
        with db.sessions.begin() as session:
            assert session.get(Admin, 1) is None
    finally:
        db.close()


def test_offline_cli_reset(runtime, monkeypatch):
    db = Database(runtime.database_path)
    db.migrate()
    try:
        init_admin(db, "admin", OLD_PASSWORD)
        monkeypatch.setattr("sys.stdin", io.StringIO(NEW_PASSWORD + "\n"))
        run_reset(monkeypatch, runtime, "--password-stdin")
        with pytest.raises(ValueError, match="reset-password"):
            init_admin(db, "admin", OLD_PASSWORD)
    finally:
        db.close()


def test_login_verified_before_reset_cannot_create_session(client, monkeypatch):
    from reeldock.security import passwords

    verify = passwords.verify

    def reset_during_verification(password, hashed):
        valid = verify(password, hashed)
        reset_admin_password(client.app.state.db, NEW_PASSWORD)
        return valid

    with monkeypatch.context() as patch:
        patch.setattr(passwords, "verify", reset_during_verification)
        assert login(client, OLD_PASSWORD).status_code == 401
    with client.app.state.db.sessions.begin() as session:
        assert session.scalar(select(AuthSession)) is None
    assert login(client, NEW_PASSWORD).status_code == 200
