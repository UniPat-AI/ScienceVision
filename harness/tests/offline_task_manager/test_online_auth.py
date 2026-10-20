from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tools.offline_task_manager import online_auth
from tools.offline_task_manager.contract import ContractError
from tools.offline_task_manager.online_auth import (
    MAX_PASSWORD_LENGTH,
    change_expert_password,
    create_credentials,
    create_session,
    list_online_users,
    load_credentials,
    parse_session,
    reset_expert_password,
    verify_password,
)


def test_credentials_are_new_private_and_sessions_are_signed(imported_workflow: dict) -> None:
    root: Path = imported_workflow["tmp"] / "auth"
    credentials = root / "credentials.json"
    plaintext = root / "handoff.csv"
    secret_path = root / "session.key"
    result = create_credentials(
        imported_workflow["store"],
        credentials_path=credentials,
        plaintext_path=plaintext,
        session_secret_path=secret_path,
    )
    assert result["user_count"] == 3
    assert credentials.stat().st_mode & 0o077 == 0
    assert plaintext.stat().st_mode & 0o077 == 0
    assert secret_path.stat().st_mode & 0o077 == 0
    users = load_credentials(credentials)
    rows = list(csv.DictReader(plaintext.read_text(encoding="utf-8").splitlines()))
    expert = next(row for row in rows if row["username"] == "expert-a")
    assert users["admin"]["must_change_password"] is False
    assert users["expert-a"]["must_change_password"] is True
    assert users["expert-a"]["password_version"] == 1
    assert verify_password(expert["password"], users["expert-a"])
    assert not verify_password(expert["password"] + "x", users["expert-a"])
    token, session = create_session(users["expert-a"], secret_path.read_bytes(), ttl_seconds=60)
    assert parse_session(token, secret_path.read_bytes(), now=session["issued_at"])["expert_id"] == "expert-a"
    assert parse_session(token + "x", secret_path.read_bytes()) is None
    assert parse_session(token, b"x" * 32) is None


def test_v1_credentials_without_activation_fields_remain_active(
    imported_workflow: dict,
) -> None:
    root: Path = imported_workflow["tmp"] / "legacy-auth"
    credentials = root / "credentials.json"
    create_credentials(
        imported_workflow["store"],
        credentials_path=credentials,
        plaintext_path=root / "handoff.csv",
        session_secret_path=root / "session.key",
    )
    document = json.loads(credentials.read_text(encoding="utf-8"))
    for record in document["users"]:
        record.pop("must_change_password")
        record.pop("password_version")
    credentials.write_text(json.dumps(document), encoding="utf-8")

    users = load_credentials(credentials)
    assert users["admin"]["must_change_password"] is False
    assert users["expert-a"]["must_change_password"] is False
    assert users["expert-a"]["password_version"] == 1
    _, session = create_session(users["expert-a"], (root / "session.key").read_bytes())
    assert session["password_change_required"] is False


def test_expert_password_change_and_reset_are_hashed_and_versioned(
    imported_workflow: dict,
) -> None:
    root: Path = imported_workflow["tmp"] / "password-updates"
    credentials = root / "credentials.json"
    plaintext = root / "handoff.csv"
    create_credentials(
        imported_workflow["store"],
        credentials_path=credentials,
        plaintext_path=plaintext,
        session_secret_path=root / "session.key",
    )
    initial_password = next(
        row["password"]
        for row in csv.DictReader(plaintext.read_text(encoding="utf-8").splitlines())
        if row["username"] == "expert-a"
    )
    users = load_credentials(credentials)
    new_password = "a-new-private-password"

    changed = change_expert_password(
        credentials,
        username="expert-a",
        new_password=new_password,
        expected_users=users,
    )
    activated = changed.users["expert-a"]
    assert activated["must_change_password"] is False
    assert activated["password_version"] == 2
    assert verify_password(new_password, activated)
    assert not verify_password(initial_password, activated)
    assert new_password not in credentials.read_text(encoding="utf-8")

    reset = reset_expert_password(
        credentials,
        expert_id="expert-a",
        expected_users=changed.users,
    )
    pending = reset.users["expert-a"]
    assert pending["must_change_password"] is True
    assert pending["password_version"] == 3
    assert verify_password(reset.password, pending)
    assert not verify_password(new_password, pending)
    assert reset.password not in credentials.read_text(encoding="utf-8")


def test_duplicate_expert_id_credentials_are_rejected(imported_workflow: dict) -> None:
    root: Path = imported_workflow["tmp"] / "duplicate-expert-auth"
    credentials = root / "credentials.json"
    create_credentials(
        imported_workflow["store"],
        credentials_path=credentials,
        plaintext_path=root / "handoff.csv",
        session_secret_path=root / "session.key",
    )
    document = json.loads(credentials.read_text(encoding="utf-8"))
    duplicate = next(
        record
        for record in document["users"]
        if record.get("expert_id") == "expert-b"
    )
    duplicate["expert_id"] = "expert-a"
    credentials.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ContractError, match="duplicate online expert_id 'expert-a'"):
        load_credentials(credentials)
def test_expert_password_reset_is_private_atomic_and_expert_only(
    imported_workflow: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    root: Path = imported_workflow["tmp"] / "reset-auth"
    credentials = root / "credentials.json"
    plaintext = root / "handoff.csv"
    secret_path = root / "session.key"
    create_credentials(
        imported_workflow["store"],
        credentials_path=credentials,
        plaintext_path=plaintext,
        session_secret_path=secret_path,
    )
    initial_rows = list(csv.DictReader(plaintext.read_text(encoding="utf-8").splitlines()))
    old_password = next(row["password"] for row in initial_rows if row["username"] == "expert-a")
    before = load_credentials(credentials)

    reset = reset_expert_password(
        credentials, expert_id="expert-a", expected_users=before
    )
    after = load_credentials(credentials)
    credential_text = credentials.read_text(encoding="utf-8")
    assert reset.password != old_password
    assert len(reset.password) >= 24
    assert reset.directory_sync_confirmed
    assert reset.password not in credential_text
    assert verify_password(reset.password, after["expert-a"])
    assert not verify_password(old_password, after["expert-a"])
    assert after["expert-a"]["salt"] != before["expert-a"]["salt"]
    assert after["expert-a"]["password_hash"] != before["expert-a"]["password_hash"]
    assert verify_password(reset.password, reset.users["expert-a"])
    assert all(
        "password" not in account and "salt" not in account and "password_hash" not in account
        for account in list_online_users(credentials)
    )

    with pytest.raises(ContractError):
        reset_expert_password(credentials, expert_id="admin", expected_users=after)
    with pytest.raises(ContractError, match="out of date"):
        reset_expert_password(credentials, expert_id="expert-a", expected_users=before)
    with pytest.raises(ContractError):
        change_expert_password(
            credentials,
            username="expert-a",
            new_password="too-short",
            expected_users=after,
        )
    with pytest.raises(ContractError):
        change_expert_password(
            credentials,
            username="expert-a",
            new_password="x" * (MAX_PASSWORD_LENGTH + 1),
            expected_users=after,
        )

    before_failed_replace = credentials.read_bytes()

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(online_auth.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        reset_expert_password(credentials, expert_id="expert-a", expected_users=after)
    assert credentials.read_bytes() == before_failed_replace
    assert verify_password(reset.password, load_credentials(credentials)["expert-a"])
    assert json.loads(credentials.read_text(encoding="utf-8"))["schema_version"]
