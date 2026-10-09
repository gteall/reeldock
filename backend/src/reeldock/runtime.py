from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Runtime(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REELDOCK_", env_file=".env.app", extra="ignore")

    data_dir: Path = Path("data")
    frontend_dir: Path = Path("frontend/dist")
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    secure_cookie: bool = False
    worker_enabled: bool = True
    worker_concurrency: int = Field(default=2, ge=1, le=8)
    lease_seconds: float = Field(default=30, ge=3)
    poll_seconds: float = Field(default=0.5, ge=0.05)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "reeldock.sqlite3"
