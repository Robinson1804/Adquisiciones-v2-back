"""Auth schemas.  password_hash MUST NOT appear in any response schema."""
from typing import Literal

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    """Public user representation.  Never includes password_hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    nombre_completo: str
    rol: str
    area: str | None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserOut
