"""Auth router: POST /auth/login, POST /auth/logout."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import create_access_token, verify_password
from app.database import get_db
from app.models.usuario import Usuario
from app.schemas.auth import LoginRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Authenticate user and return JWT.

    Returns the same 401 message for unknown username and wrong password
    to prevent credential enumeration.
    """
    usuario = db.query(Usuario).filter(Usuario.username == body.username).first()

    if usuario is None or not verify_password(body.password, usuario.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
        )

    if not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario inactivo",
        )

    access_token = create_access_token(
        sub=str(usuario.id),
        username=usuario.username,
        rol=usuario.rol,
    )
    return TokenResponse(
        access_token=access_token,
        user=UserOut.model_validate(usuario),
    )


@router.post("/logout")
def logout() -> dict:
    """Stateless logout — no server state mutated.

    Client is responsible for discarding the token.
    """
    return {"detail": "Sesión cerrada"}
