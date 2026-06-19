from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str
    SECRET_KEY: str = "dev-secret-change-me-min-32-characters-long"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    ALLOWED_ORIGINS: str = (
        "http://localhost:3100,http://127.0.0.1:3100,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )

    # File upload settings (C3c)
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024  # 10 MB

    @property
    def allowed_origins_list(self) -> list[str]:
        configured = [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]
        # For every localhost origin, also accept the 127.0.0.1 sibling (and vice
        # versa) — browsers treat them as different origins for CORS.
        extras: list[str] = []
        for origin in configured:
            if "://localhost" in origin and origin.replace("://localhost", "://127.0.0.1") not in configured:
                extras.append(origin.replace("://localhost", "://127.0.0.1"))
            elif "://127.0.0.1" in origin and origin.replace("://127.0.0.1", "://localhost") not in configured:
                extras.append(origin.replace("://127.0.0.1", "://localhost"))
        return configured + extras

    @property
    def upload_path(self) -> Path:
        """Absolute path to the upload directory."""
        return Path(self.UPLOAD_DIR).resolve()


settings = Settings()
