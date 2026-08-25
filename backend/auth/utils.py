from datetime import UTC, datetime, timedelta

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash

from backend.auth.config import auth_settings
from backend.auth.exceptions import InvalidCredentials

_password_hash = PasswordHash.recommended()


# ── Password ──────────────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    return _password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _password_hash.verify(plain, hashed)


# ── JWT ───────────────────────────────────────────────────────────────────────


def create_access_token(user_id: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=auth_settings.JWT_EXP_MINUTES)
    )
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(
        payload, auth_settings.JWT_SECRET, algorithm=auth_settings.JWT_ALG
    )


def decode_access_token(token: str) -> str:
    """Decode a JWT and return the subject (user_id string).

    Raises InvalidCredentials on any failure.
    """
    try:
        payload = jwt.decode(
            token,
            auth_settings.JWT_SECRET,
            algorithms=[auth_settings.JWT_ALG],
        )
        return str(payload["sub"])
    except (InvalidTokenError, KeyError) as exc:
        raise InvalidCredentials() from exc
