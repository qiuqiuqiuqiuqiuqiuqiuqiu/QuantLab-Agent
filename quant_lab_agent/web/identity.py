"""Dependency-free identity and authorization store for the single-node deployment."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROLE_RESEARCHER = "researcher"
ROLE_REVIEWER = "reviewer"
ROLE_ADMIN = "admin"
VALID_ROLES = {ROLE_RESEARCHER, ROLE_REVIEWER, ROLE_ADMIN}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: str


class IdentityStore:
    """SQLite-backed users, run ownership, and approval records.

    It intentionally uses standard-library crypto and SQLite to keep the local
    deployment small. Run behind HTTPS and use a high-entropy secret in production.
    """

    def __init__(self, path: Path, token_secret: str, token_ttl_minutes: int = 480, production: bool = False):
        if production and len(token_secret) < 32:
            raise ValueError("QUANTLAB_AUTH_SECRET must be at least 32 characters in production")
        self.path, self.secret = path, token_secret.encode()
        self.ttl, self.production = token_ttl_minutes, production
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE,
              salt BLOB NOT NULL,
              password_hash BLOB NOT NULL,
              role TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_owners (
              run_id TEXT PRIMARY KEY,
              owner_id INTEGER NOT NULL REFERENCES users(id),
              visibility TEXT NOT NULL DEFAULT 'team',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              requested_by INTEGER NOT NULL REFERENCES users(id),
              reviewer_id INTEGER REFERENCES users(id),
              status TEXT NOT NULL DEFAULT 'pending',
              comment TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              decided_at TEXT
            );
            """)

    def bootstrap_admin(self, username: str | None, password: str | None) -> None:
        if username and password and not self.user_count():
            self.create_user(username, password, ROLE_ADMIN)

    def user_count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, username: str, password: str, role: str) -> User:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
            raise ValueError("用户名只能包含字母、数字、._-，长度 3-64")
        if role not in VALID_ROLES:
            raise ValueError("无效角色")
        min_length = 12 if self.production else 8
        if len(password) < min_length:
            raise ValueError(f"密码至少需要 {min_length} 位")
        salt = os.urandom(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO users(username, salt, password_hash, role, created_at) VALUES(?,?,?,?,?)",
                    (username, salt, digest, role, _now()),
                )
                return User(cursor.lastrowid, username, role)
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在") from exc

    def authenticate(self, username: str, password: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if row is None:
            return None
        expected = hashlib.scrypt(password.encode(), salt=row["salt"], n=2**14, r=8, p=1)
        if not hmac.compare_digest(expected, row["password_hash"]):
            return None
        return User(row["id"], row["username"], row["role"])

    def issue_token(self, user: User) -> str:
        payload = {"sub": user.id, "username": user.username, "role": user.role, "exp": int((datetime.now(timezone.utc) + timedelta(minutes=self.ttl)).timestamp()), "jti": secrets.token_hex(8)}
        encoded = _b64(json.dumps(payload, separators=(",", ":")).encode())
        signature = _b64(hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def verify_token(self, token: str) -> User | None:
        try:
            encoded, signature = token.split(".", 1)
            expected = _b64(hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                return None
            payload = json.loads(_unb64(encoded))
            if int(payload["exp"]) < int(datetime.now(timezone.utc).timestamp()):
                return None
            with self._connect() as conn:
                row = conn.execute("SELECT id, username, role FROM users WHERE id=? AND active=1", (payload["sub"],)).fetchone()
            return User(row["id"], row["username"], row["role"]) if row else None
        except (ValueError, KeyError, json.JSONDecodeError, TypeError):
            return None

    def attach_run(self, run_id: str, owner: User, visibility: str = "team") -> None:
        if visibility not in {"private", "team"}:
            raise ValueError("visibility 必须是 private 或 team")
        with self._connect() as conn:
            conn.execute("INSERT INTO run_owners(run_id, owner_id, visibility, created_at) VALUES(?,?,?,?)", (run_id, owner.id, visibility, _now()))

    def can_read_run(self, run_id: str, user: User) -> bool:
        if user.role in {ROLE_ADMIN, ROLE_REVIEWER}:
            return True
        with self._connect() as conn:
            row = conn.execute("SELECT owner_id, visibility FROM run_owners WHERE run_id=?", (run_id,)).fetchone()
        return bool(row and (row["owner_id"] == user.id or row["visibility"] == "team"))

    def list_visible_ids(self, user: User) -> set[str]:
        with self._connect() as conn:
            if user.role in {ROLE_ADMIN, ROLE_REVIEWER}:
                rows = conn.execute("SELECT run_id FROM run_owners").fetchall()
            else:
                rows = conn.execute("SELECT run_id FROM run_owners WHERE owner_id=? OR visibility='team'", (user.id,)).fetchall()
        return {row["run_id"] for row in rows}

    def request_approval(self, run_id: str, user: User, comment: str) -> dict:
        with self._connect() as conn:
            cursor = conn.execute("INSERT INTO approvals(run_id, requested_by, comment, created_at) VALUES(?,?,?,?)", (run_id, user.id, comment, _now()))
            return self.get_approval(cursor.lastrowid, conn)

    def decide_approval(self, approval_id: int, reviewer: User, status: str, comment: str) -> dict:
        if reviewer.role not in {ROLE_ADMIN, ROLE_REVIEWER}:
            raise PermissionError("只有 reviewer 或 admin 可以审批")
        if status not in {"approved", "rejected"}:
            raise ValueError("审批状态必须是 approved 或 rejected")
        with self._connect() as conn:
            if conn.execute("UPDATE approvals SET reviewer_id=?, status=?, comment=?, decided_at=? WHERE id=? AND status='pending'", (reviewer.id, status, comment, _now(), approval_id)).rowcount != 1:
                raise ValueError("审批不存在或已处理")
            return self.get_approval(approval_id, conn)

    def list_approvals(self, user: User) -> list[dict]:
        with self._connect() as conn:
            query = "SELECT a.*, requester.username requester_name, reviewer.username reviewer_name FROM approvals a JOIN users requester ON requester.id=a.requested_by LEFT JOIN users reviewer ON reviewer.id=a.reviewer_id"
            args: tuple = ()
            if user.role == ROLE_RESEARCHER:
                query += " WHERE a.requested_by=?"
                args = (user.id,)
            return [dict(row) for row in conn.execute(query + " ORDER BY a.id DESC", args).fetchall()]

    @staticmethod
    def get_approval(approval_id: int, conn: sqlite3.Connection) -> dict:
        row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return dict(row)
