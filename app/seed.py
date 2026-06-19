"""Idempotent seed: creates the default ADMIN user if it does not exist.

DEFAULT CREDENTIALS (MUST be changed before production):
  username: admin
  password: Adquisiciones2026!

To regenerate SECRET_KEY:
  python -c "import secrets; print(secrets.token_urlsafe(48))"
"""
import logging

from app.core.security import hash_password
from app.database import SessionLocal
from app.models.usuario import Usuario

logger = logging.getLogger(__name__)

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "Adquisiciones2026!"  # PLACEHOLDER — change after first login


def seed() -> None:
    """Insert default admin user if it does not exist.  Safe to run multiple times."""
    db = SessionLocal()
    try:
        existing = db.query(Usuario).filter(Usuario.username == DEFAULT_USERNAME).first()
        if existing:
            logger.info("Seed: usuario '%s' ya existe, seed omitido.", DEFAULT_USERNAME)
            return

        admin = Usuario(
            username=DEFAULT_USERNAME,
            nombre_completo="Administrador",
            rol="ADMIN",
            activo=True,
            area=None,
            password_hash=hash_password(DEFAULT_PASSWORD),
        )
        db.add(admin)
        db.commit()
        logger.info("Seed: usuario '%s' creado exitosamente.", DEFAULT_USERNAME)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed()
