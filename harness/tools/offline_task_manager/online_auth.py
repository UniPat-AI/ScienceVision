"""Password and signed-session helpers for the optional online gateway."""

from __future__ import annotations

import base64
import csv
import errno
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import ContractError, canonical_json, require_identifier
from .store import ManagerStore

CREDENTIALS_SCHEMA_VERSION = "sciencevision.online_credentials.v1"
SESSION_SCHEMA_VERSION = "sciencevision.online_session.v1"
PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 16
MAX_PASSWORD_LENGTH = 256
DIRECTORY_FSYNC_ATTEMPTS = 2


@dataclass(frozen=True)
class ExpertAccountInvite:
    """A newly created expert account and its one-time password."""

    password: str
    users: dict[str, dict[str, Any]]
    directory_sync_confirmed: bool


@dataclass(frozen=True)
class ExpertPasswordChange:
    """A persisted expert password change."""

    users: dict[str, dict[str, Any]]
    directory_sync_confirmed: bool


@dataclass(frozen=True)
class ExpertPasswordReset:
    """A reset expert account and its one-time password."""

    password: str
    users: dict[str, dict[str, Any]]
    directory_sync_confirmed: bool


def require_private_file(path: Path, field: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ContractError(f"{field} must be a regular non-symlink file")
    if path.stat().st_mode & 0o077:
        raise ContractError(f"{field} must not be accessible by group or other users")
    return path


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _password_hash(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    ).hex()


def validate_new_password(password: Any) -> str:
    """Reject a replacement password that is outside the accepted length band."""
    if (
        not isinstance(password, str)
        or not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH
    ):
        raise ContractError(
            f"password must be between {MIN_PASSWORD_LENGTH} and "
            f"{MAX_PASSWORD_LENGTH} characters"
        )
    return password


def verify_password(password: str, record: dict[str, Any]) -> bool:
    try:
        salt = bytes.fromhex(record["salt"])
        iterations = int(record["iterations"])
        expected = str(record["password_hash"])
    except (KeyError, TypeError, ValueError):
        return False
    actual = _password_hash(password, salt, iterations)
    return hmac.compare_digest(actual, expected)


def password_version(record: dict[str, Any]) -> int:
    """Return the credential generation, including the v1 legacy default."""
    value = record.get("password_version", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError("online credential password_version must be a positive integer")
    return value


def password_change_required(record: dict[str, Any]) -> bool:
    """Return whether this account is limited to password activation."""
    value = record.get("must_change_password", False)
    if not isinstance(value, bool):
        raise ContractError("online credential must_change_password must be boolean")
    return value


def load_credentials(path: Path) -> dict[str, dict[str, Any]]:
    require_private_file(path, "online credentials")
    value = json.loads(path.read_text(encoding="utf-8"))
    return _credential_users(value)


def project_online_users(users: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return account metadata that is safe to expose to an administrator."""
    return [
        {
            "username": username,
            "role": user["role"],
            "expert_id": user.get("expert_id"),
            "display_name": (
                user.get("display_name")
                if isinstance(user.get("display_name"), str) and user["display_name"]
                else username
            ),
        }
        for username, user in sorted(users.items())
    ]


def list_online_users(path: Path) -> list[dict[str, Any]]:
    """Return operator-safe account metadata without authentication material."""
    return project_online_users(load_credentials(path))


def expert_accounts_by_id(
    users: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Index expert accounts by roster identity, independent of login username."""
    result: dict[str, dict[str, Any]] = {}
    for account in users.values():
        if account.get("role") != "expert":
            continue
        expert_id = require_identifier(account.get("expert_id"), "expert_id")
        if expert_id in result:
            raise ContractError(f"duplicate online expert_id {expert_id!r}")
        result[expert_id] = account
    return result


def _credential_users(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ContractError("online credentials must be an object")
    if value.get("schema_version") != CREDENTIALS_SCHEMA_VERSION:
        raise ContractError("unsupported online credentials schema")
    users = value.get("users")
    if not isinstance(users, list) or not users:
        raise ContractError("online credentials must contain users")
    result: dict[str, dict[str, Any]] = {}
    for record in users:
        if not isinstance(record, dict):
            raise ContractError("online credential user must be an object")
        username = require_identifier(record.get("username"), "username")
        role = record.get("role")
        if role not in {"admin", "expert"}:
            raise ContractError("online credential role must be admin or expert")
        if role == "expert":
            require_identifier(record.get("expert_id"), "expert_id")
        password_version(record)
        password_change_required(record)
        if username in result:
            raise ContractError(f"duplicate online username {username!r}")
        result[username] = record | {
            "password_version": password_version(record),
            "must_change_password": password_change_required(record),
        }
    expert_accounts_by_id(result)
    return result


def create_session(
    user: dict[str, Any], secret: bytes, *, ttl_seconds: int = 12 * 60 * 60
) -> tuple[str, dict[str, Any]]:
    now = int(time.time())
    payload = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "username": user["username"],
        "role": user["role"],
        "expert_id": user.get("expert_id"),
        "display_name": user.get("display_name") or user["username"],
        "csrf": secrets.token_urlsafe(24),
        "password_version": password_version(user),
        "password_change_required": password_change_required(user),
        "issued_at": now,
        "expires_at": now + ttl_seconds,
    }
    encoded = _b64encode(canonical_json(payload).encode("utf-8"))
    signature = _b64encode(hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}", payload


def parse_session(token: str, secret: bytes, *, now: int | None = None) -> dict[str, Any] | None:
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected_signature = _b64encode(
            hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return None
        payload = json.loads(_b64decode(encoded))
        if payload.get("schema_version") != SESSION_SCHEMA_VERSION:
            return None
        current = int(time.time()) if now is None else now
        if not isinstance(payload.get("expires_at"), int) or payload["expires_at"] <= current:
            return None
        if payload.get("role") not in {"admin", "expert"}:
            return None
        version = payload.get("password_version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return None
        if not isinstance(payload.get("password_change_required"), bool):
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _atomic_new_file(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.link(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _fsync_parent_directory(path: Path) -> bool:
    """Persist a completed rename where the platform permits directory fsync."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno in {errno.EINVAL, errno.ENOTSUP}:
                return False
            raise
        return True
    finally:
        os.close(descriptor)


def _atomic_replace_private_file(path: Path, payload: bytes) -> bool:
    """Replace a private file and report whether directory sync completed."""
    require_private_file(path, "online credentials")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        for _ in range(DIRECTORY_FSYNC_ATTEMPTS):
            try:
                return _fsync_parent_directory(path)
            except OSError:
                continue
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _temporary_password() -> str:
    password = secrets.token_urlsafe(24)
    while password.startswith(("=", "+", "-", "@")):
        password = secrets.token_urlsafe(24)
    return password


def _protected_password_fields(password: str) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    return {
        "salt": salt.hex(),
        "iterations": PBKDF2_ITERATIONS,
        "password_hash": _password_hash(password, salt, PBKDF2_ITERATIONS),
    }


def _replace_account(
    credentials_path: Path,
    *,
    username: str,
    replacement: dict[str, Any],
    expected_users: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], bool]:
    require_private_file(credentials_path, "online credentials")
    document = json.loads(credentials_path.read_text(encoding="utf-8"))
    users = _credential_users(document)
    if users != expected_users:
        raise ContractError("online credential snapshot is out of date")
    if username not in users:
        raise ContractError("online account does not exist")

    records = [
        replacement if record.get("username") == username else record
        for record in document["users"]
    ]
    updated_document = dict(document)
    updated_document["users"] = records
    updated_users = dict(users)
    updated_users[username] = replacement
    payload = (
        json.dumps(updated_document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    synced = _atomic_replace_private_file(credentials_path, payload)
    return updated_users, synced


def invite_expert_account(
    credentials_path: Path,
    *,
    expert_id: str,
    display_name: str,
    expected_users: dict[str, dict[str, Any]],
) -> ExpertAccountInvite:
    """Create credentials for one roster-backed expert account."""
    expert_id = require_identifier(expert_id, "expert_id")
    if not isinstance(display_name, str) or not display_name.strip():
        raise ContractError("expert display_name is required")
    require_private_file(credentials_path, "online credentials")
    document = json.loads(credentials_path.read_text(encoding="utf-8"))
    users = _credential_users(document)
    if users != expected_users:
        raise ContractError("online credential snapshot is out of date")
    if expert_id in expert_accounts_by_id(users):
        raise ContractError("expert already has an online account")
    if expert_id in users:
        raise ContractError("expert_id conflicts with an existing online username")

    password = _temporary_password()
    account = {
        "username": expert_id,
        "role": "expert",
        "expert_id": expert_id,
        "display_name": display_name,
        "must_change_password": True,
        "password_version": 1,
        **_protected_password_fields(password),
    }
    updated_document = dict(document)
    updated_document["users"] = [*document["users"], account]
    updated_users = dict(users)
    updated_users[expert_id] = account
    payload = (
        json.dumps(updated_document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    directory_sync_confirmed = _atomic_replace_private_file(credentials_path, payload)
    return ExpertAccountInvite(
        password=password,
        users=updated_users,
        directory_sync_confirmed=directory_sync_confirmed,
    )


def change_expert_password(
    credentials_path: Path,
    *,
    username: str,
    new_password: str,
    expected_users: dict[str, dict[str, Any]],
) -> ExpertPasswordChange:
    """Activate an invited expert account with a new private password."""
    username = require_identifier(username, "username")
    new_password = validate_new_password(new_password)
    account = expected_users.get(username)
    if not account or account.get("role") != "expert":
        raise ContractError("expert account does not exist")
    if not password_change_required(account):
        raise ContractError("password activation is not required")
    if verify_password(new_password, account):
        raise ContractError("new password must not reuse the temporary password")

    replacement = account | {
        "must_change_password": False,
        "password_version": password_version(account) + 1,
        **_protected_password_fields(new_password),
    }
    users, synced = _replace_account(
        credentials_path,
        username=username,
        replacement=replacement,
        expected_users=expected_users,
    )
    return ExpertPasswordChange(users=users, directory_sync_confirmed=synced)


def reset_expert_password(
    credentials_path: Path,
    *,
    expert_id: str,
    expected_users: dict[str, dict[str, Any]],
) -> ExpertPasswordReset:
    """Issue a new temporary password and revoke all existing expert sessions."""
    expert_id = require_identifier(expert_id, "expert_id")
    account = expert_accounts_by_id(expected_users).get(expert_id)
    if not account:
        raise ContractError("expert account does not exist")
    username = account["username"]

    password = _temporary_password()
    replacement = account | {
        "must_change_password": True,
        "password_version": password_version(account) + 1,
        **_protected_password_fields(password),
    }
    users, synced = _replace_account(
        credentials_path,
        username=username,
        replacement=replacement,
        expected_users=expected_users,
    )
    return ExpertPasswordReset(
        password=password,
        users=users,
        directory_sync_confirmed=synced,
    )


def create_credentials(
    store: ManagerStore,
    *,
    credentials_path: Path,
    plaintext_path: Path,
    session_secret_path: Path,
    admin_username: str = "admin",
) -> dict[str, Any]:
    admin_username = require_identifier(admin_username, "admin_username")
    overview = store.overview()
    users = [
        {
            "username": admin_username,
            "role": "admin",
            "expert_id": None,
            "display_name": "ScienceVision administrator",
        }
    ]
    users.extend(
        {
            "username": expert["expert_id"],
            "role": "expert",
            "expert_id": expert["expert_id"],
            "display_name": expert["name"],
        }
        for expert in overview["experts"]
        if expert.get("active")
    )
    protected: list[dict[str, Any]] = []
    plaintext: list[dict[str, str]] = []
    for user in users:
        password = _temporary_password()
        protected.append(
            user
            | {
                "must_change_password": user["role"] == "expert",
                "password_version": 1,
                **_protected_password_fields(password),
            }
        )
        plaintext.append(
            {
                "username": user["username"],
                "password": password,
                "role": user["role"],
                "expert_id": user.get("expert_id") or "",
                "display_name": user["display_name"],
            }
        )
    credentials = {
        "schema_version": CREDENTIALS_SCHEMA_VERSION,
        "users": protected,
    }
    _atomic_new_file(
        credentials_path,
        (json.dumps(credentials, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )
    import io

    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output, fieldnames=["username", "password", "role", "expert_id", "display_name"]
    )
    writer.writeheader()
    writer.writerows(
        {
            key: "'" + value
            if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
            else value
            for key, value in row.items()
        }
        for row in plaintext
    )
    _atomic_new_file(plaintext_path, output.getvalue().encode("utf-8"))
    _atomic_new_file(session_secret_path, secrets.token_bytes(32))
    return {
        "credentials": str(credentials_path),
        "plaintext": str(plaintext_path),
        "session_secret": str(session_secret_path),
        "user_count": len(users),
        "expert_count": len(users) - 1,
    }
