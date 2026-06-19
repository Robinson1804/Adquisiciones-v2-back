"""Password hashing and JWT utilities.

Security invariants enforced here:
- Plaintext passwords never cross a function boundary.
- Token and SECRET_KEY are never logged.
- decode_token always verifies signature AND exp — never bare decode.
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from jose import ExpiredSignatureError, JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
)


def hash_password(plain: str) -> str:
    """Return bcrypt hash of plain.  Caller must discard plain immediately."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time comparison via passlib."""
    return pwd_context.verify(plain, hashed)


def create_access_token(sub: str, username: str, rol: str) -> str:
    """Create a signed HS256 JWT with sub, username, rol, and exp claims."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": sub,
        "username": username,
        "rol": rol,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and verify token.  Raises HTTP 401 on any failure.

    Never logs the token value or the SECRET_KEY.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except ExpiredSignatureError:
        raise credentials_exc
    except JWTError:
        raise credentials_exc
