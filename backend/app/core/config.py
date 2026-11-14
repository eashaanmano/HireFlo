"""Application settings, loaded from environment variables or backend/.env."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "HireFlo (DriveOps)"
    database_url: str = "sqlite:///./data/driveops.db"
    secret_key: str = Field(default="dev-insecure-secret-change-me", min_length=16)
    demo_mode: bool = True
    public_url: str = "http://localhost:5173"
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:5173"  # comma separated
    session_hours: int = 12
    recruiter_link_days: int = 21
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5

    # Drive-day operations
    delay_threshold_min: int = 10        # projected lateness that triggers a re-plan proposal
    no_show_grace_min: int = 10          # minutes after slot start before a no-show is flagged
    monitor_interval_sec: int = 30       # background check of live drives
    nudge_hours_before: int = 24         # remind eligible non-applicants this long before a deadline (0 = off)
    display_utc_offset_min: int = 330    # local time used on printed sheets (IST)
    solver_time_limit_sec: float = 5.0
    # Working hours for interviews (local time, see display_utc_offset_min). A round that doesn't fit in one day
    # carries on the next working day; the cell can override these per round when scheduling.
    work_day_start: str = "09:00"
    work_day_end: str = "18:00"
    work_breaks: str = "13:00-14:00"     # comma separated, empty for none
    schedule_max_days: int = 3
    schedule_skip_sundays: bool = True

    # Optional LLMs. Configure any of them; tasks are routed to the best one available (see services/llm.py).
    # Azure OpenAI (Microsoft Foundry): endpoint like https://<resource>.openai.azure.com, plus a deployment name.
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    # Groq: fast open models through an OpenAI-compatible API (free tier at console.groq.com).
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_fast_model: str = "llama-3.1-8b-instant"
    # Generic provider (kept for older .env files): anthropic, or any OpenAI-compatible API.
    llm_provider: Literal["none", "anthropic", "openai"] = "none"
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-5-5"
    llm_base_url: str = ""
    # Which provider does what: "auto" picks Azure for extraction and Groq for chat, with fall-back to the others.
    llm_chat_provider: Literal["auto", "azure", "groq", "generic"] = "auto"
    llm_extract_provider: Literal["auto", "azure", "groq", "generic"] = "auto"
    llm_timeout_sec: float = 30.0
    assistant_enabled: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "HireFlo <no-reply@driveops.local>"
    telegram_bot_token: str = ""

    upload_dir: str = "./data/uploads"
    static_dir: str = ""  # optional: path to a built frontend to serve from the API

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def azure_enabled(self) -> bool:
        return bool(self.azure_openai_endpoint and self.azure_openai_api_key and self.azure_openai_deployment)

    @property
    def groq_enabled(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def generic_llm_enabled(self) -> bool:
        return self.llm_provider != "none" and bool(self.llm_api_key)

    @property
    def llm_enabled(self) -> bool:
        return self.azure_enabled or self.groq_enabled or self.generic_llm_enabled


@lru_cache
def get_settings() -> Settings:
    return Settings()
