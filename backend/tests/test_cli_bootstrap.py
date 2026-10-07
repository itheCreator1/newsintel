from types import TracebackType

import pytest

from app import cli


class _FakeSession:
    def __init__(self, existing_user: object | None) -> None:
        self.existing_user = existing_user

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def scalar(self, _statement: object) -> object | None:
        return self.existing_user


def _setup(
    monkeypatch: pytest.MonkeyPatch, *, existing_user: object | None, env: dict[str, str]
) -> list[tuple[str, str | None]]:
    created: list[tuple[str, str | None]] = []

    async def fake_create_user(username: str, password: str | None = None) -> None:
        created.append((username, password))

    monkeypatch.setattr(cli, "session_factory", lambda: _FakeSession(existing_user))
    monkeypatch.setattr(cli, "create_user", fake_create_user)
    for name in ("NEWSINTEL_ADMIN_USERNAME", "NEWSINTEL_ADMIN_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return created


FIRST_ACCOUNT = {"NEWSINTEL_ADMIN_USERNAME": "admin", "NEWSINTEL_ADMIN_PASSWORD": "a-long-password"}


@pytest.mark.asyncio
async def test_bootstrap_admin_creates_the_first_account(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _setup(monkeypatch, existing_user=None, env=FIRST_ACCOUNT)
    await cli.bootstrap_admin()
    assert created == [("admin", "a-long-password")]


@pytest.mark.asyncio
async def test_bootstrap_admin_leaves_an_install_with_users_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    created = _setup(monkeypatch, existing_user="user-id", env=FIRST_ACCOUNT)
    await cli.bootstrap_admin()
    assert created == []
    assert "Users already exist" in capsys.readouterr().out


@pytest.mark.parametrize(
    "env",
    [{}, {"NEWSINTEL_ADMIN_USERNAME": "admin"}, {"NEWSINTEL_ADMIN_PASSWORD": "a-long-password"}],
)
@pytest.mark.asyncio
async def test_bootstrap_admin_skips_without_both_variables(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], env: dict[str, str]
) -> None:
    created = _setup(monkeypatch, existing_user=None, env=env)
    await cli.bootstrap_admin()
    assert created == []
    assert "No first-run account configured" in capsys.readouterr().out
