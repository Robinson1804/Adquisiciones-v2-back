"""FastAPI dependencies for authentication and role-based authorization."""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.security import decode_token
from app.database import get_db
from app.models.usuario import Usuario
from sqlalchemy.orm import Session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Usuario:
    """Extract and validate Bearer token; return active Usuario ORM object.

    Raises HTTP 401 if token is missing, invalid, expired, user not found,
    or user is inactive.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autenticado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_token(token)  # raises 401 on invalid/expired
    sub = payload.get("sub")
    if sub is None:
        raise credentials_exc

    try:
        user_id = int(sub)
    except (ValueError, TypeError):
        raise credentials_exc

    usuario = db.get(Usuario, user_id)
    if usuario is None:
        raise credentials_exc
    if not usuario.activo:
        raise credentials_exc

    return usuario


def require_role(*roles: str):
    """Factory returning a dependency that enforces role membership.

    Usage: Depends(require_role("ADMIN")) or Depends(require_role("ADMIN","EDITOR"))
    """

    def _checker(user: Usuario = Depends(get_current_user)) -> Usuario:
        if user.rol not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permisos insuficientes",
            )
        return user

    return _checker
