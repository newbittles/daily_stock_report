from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Kiwoom OpenAPI+ (OCX) — 앱키 없음, HTS 로그인으로 인증
    kiwoom_account_no: str
    kiwoom_env: Literal["real", "paper"] = "real"

    # Telegram
    telegram_bot_token: str
    telegram_allowed_chat_ids: str  # comma-separated

    # Google Gemini
    gemini_api_key: str
    ai_daily_call_limit: int = 100

    # Storage
    db_path: str = "data/stock_bot.db"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    def allowed_chat_ids(self) -> list[int]:
        return [
            int(cid.strip())
            for cid in self.telegram_allowed_chat_ids.split(",")
            if cid.strip()
        ]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
