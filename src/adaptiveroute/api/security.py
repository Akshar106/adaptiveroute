"""API-key authentication.

Key format: ``ar_<8 hex prefix>_<43 char secret>``. The prefix is a public lookup id;
the database stores only HMAC-SHA256(pepper, key). Because keys are 256-bit random
values, a fast keyed hash is appropriate (slow password hashes like bcrypt protect
low-entropy secrets); the pepper lives outside the database (env / Secrets Manager),
so a database dump alone does not allow offline verification of guesses.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptiveroute.db.repositories import ApiKeyRepository

_KEY_RE = re.compile(r"^ar_([0-9a-f]{8})_[A-Za-z0-9_-]{43}$")


@dataclass(frozen=True, slots=True)
class Principal:
    api_key_id: uuid.UUID
    name: str
    role: str
    rate_limit_per_minute: int | None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def generate_api_key() -> tuple[str, str]:
    """Return (plaintext key, prefix)."""
    prefix = secrets.token_hex(4)
    return f"ar_{prefix}_{secrets.token_urlsafe(32)}", prefix


def hash_api_key(plaintext: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), plaintext.encode(), hashlib.sha256).hexdigest()


def parse_prefix(plaintext: str) -> str | None:
    m = _KEY_RE.match(plaintext)
    return m.group(1) if m else None


class Authenticator:
    """Verifies keys against Postgres with a short in-process cache.

    The cache is keyed by the key's HMAC (never the plaintext) and bounded by a TTL,
    so a revoked key stops working within ``cache_ttl_s`` on every replica.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        pepper: str,
        cache_ttl_s: float = 30.0,
        max_entries: int = 10_000,
    ) -> None:
        self._sessions = sessions
        self._pepper = pepper
        self._ttl = cache_ttl_s
        self._max = max_entries
        self._cache: dict[str, tuple[float, Principal]] = {}

    async def authenticate(self, plaintext: str) -> Principal | None:
        prefix = parse_prefix(plaintext)
        if prefix is None:
            return None
        digest = hash_api_key(plaintext, self._pepper)
        hit = self._cache.get(digest)
        if hit is not None and time.monotonic() - hit[0] < self._ttl:
            return hit[1]

        async with self._sessions() as session:
            row = await ApiKeyRepository(session).get_active_by_prefix(prefix)
        if row is None or not hmac.compare_digest(row.key_hash, digest):
            return None
        principal = Principal(row.id, row.name, row.role, row.rate_limit_per_minute)
        if len(self._cache) >= self._max:
            self._cache.clear()
        self._cache[digest] = (time.monotonic(), principal)
        return principal


async def create_api_key(
    session: AsyncSession,
    *,
    name: str,
    role: str,
    pepper: str,
    rate_limit_per_minute: int | None = None,
) -> tuple[str, uuid.UUID]:
    """Create a key and return (plaintext, id). The plaintext is never stored."""
    if role not in ("admin", "user"):
        raise ValueError("role must be 'admin' or 'user'")
    plaintext, prefix = generate_api_key()
    row = await ApiKeyRepository(session).create(
        name=name,
        prefix=prefix,
        key_hash=hash_api_key(plaintext, pepper),
        role=role,
        rate_limit=rate_limit_per_minute,
    )
    return plaintext, row.id
