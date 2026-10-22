import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]


class SourceConfig(BaseModel):
    source_id: str
    kind: Literal["official", "provider"]
    permitted: bool = False
    permission_ref: str = ""
    expires_at: str = ""
    product_ids: list[str] = Field(default_factory=list, max_length=50)
    app_key: str = Field(default="", repr=False)
    app_secret: str = Field(default="", repr=False)
    session: str = Field(default="", repr=False)
    base_url: str = ""
    token: str = Field(default="", repr=False)
    interval_seconds: int = Field(default=3600, ge=60)
    min_gap_seconds: int = Field(default=5, ge=1)
    daily_budget: int = Field(default=100, ge=1, le=100000)
    max_age_seconds: int = Field(default=7200, ge=60)
    ai_allowed: bool = False

    def ready(self):
        credentials = (
            all([self.app_key, self.app_secret, self.session])
            if self.kind == "official"
            else bool(self.base_url and self.token)
        )
        return bool(
            self.permitted
            and self.permission_ref.strip()
            and self.product_ids
            and credentials
        )


@dataclass
class Config:
    sources: list[SourceConfig] = field(default_factory=list)
    database_path: Path = ROOT / "data" / "monitor.sqlite3"
    ai_key: str = field(default="", repr=False)
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = ""
    ai_daily_budget: int = 20

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env", override=False)
        sources = []
        for prefix, kind in [("TAOBAO", "official"), ("PROVIDER", "provider")]:

            def get(name, default=""):
                return os.getenv(f"{prefix}_{name}", default).strip()

            sources.append(
                SourceConfig(
                    source_id=kind,
                    kind=kind,
                    permitted=get("PERMITTED").lower() == "true",
                    permission_ref=get("PERMISSION_REF"),
                    expires_at=get("EXPIRES_AT"),
                    product_ids=[
                        p.strip() for p in get("PRODUCT_IDS").split(",") if p.strip()
                    ],
                    app_key=get("APP_KEY"),
                    app_secret=get("APP_SECRET"),
                    session=get("SESSION"),
                    base_url=get("BASE_URL"),
                    token=get("TOKEN"),
                    interval_seconds=int(get("INTERVAL_SECONDS", "3600")),
                    min_gap_seconds=int(get("MIN_GAP_SECONDS", "5")),
                    daily_budget=int(get("DAILY_BUDGET", "100")),
                    max_age_seconds=int(get("MAX_AGE_SECONDS", "7200")),
                    ai_allowed=get("AI_ALLOWED").lower() == "true",
                )
            )
        return cls(
            sources=sources,
            database_path=Path(
                os.getenv("DATABASE_PATH", str(ROOT / "data" / "monitor.sqlite3"))
            ),
            ai_key=os.getenv("LLM_API_KEY", ""),
            ai_model=os.getenv("LLM_MODEL", ""),
            ai_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip(
                "/"
            ),
            ai_daily_budget=max(1, int(os.getenv("LLM_DAILY_BUDGET", "20"))),
        )
