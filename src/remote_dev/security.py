"""Authentication and token helpers."""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=2)


def hash_password(password: str) -> str:
    return _PASSWORDS.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return _PASSWORDS.verify(encoded, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_api_token() -> str:
    return "rdp_" + secrets.token_urlsafe(32)


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(encoded: str, token: str) -> bool:
    return hmac.compare_digest(encoded, token_hash(token))
