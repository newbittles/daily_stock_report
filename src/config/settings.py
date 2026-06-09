from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 한국투자증권 KIS Open API (REST) — 데이터/조회용 (현재 real: 모의 도메인 OHLCV 500 회피)
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""
    kis_env: Literal["real", "paper"] = "paper"

    # 모의투자 전용 키 — 자동매매 주문 전용(데이터는 위 real 키로 조회).
    # KIS는 실전/모의 키가 별개라 분리 보관. auto_trader가 주문만 paper 도메인으로 전송.
    kis_paper_app_key: str = ""
    kis_paper_app_secret: str = ""
    kis_paper_account_no: str = ""

    # Kiwoom OpenAPI+ (OCX) — 레거시, 현재 미사용 (기본값으로 선택)
    kiwoom_account_no: str = ""
    kiwoom_env: Literal["real", "paper"] = "real"

    # Telegram
    telegram_bot_token: str
    telegram_allowed_chat_ids: str  # comma-separated

    # Google Gemini
    gemini_api_key: str
    ai_daily_call_limit: int = 100

    # DART 전자공시 OpenAPI (opendart.fss.or.kr) — 종목별 최근 공시 조회(AI요약 호재/공시 유무).
    # 무료 키(읽기전용 공개데이터). 미설정이면 빈 문자열 → 공시 '확인 불가'로 표기(뉴스만 반영).
    dart_api_key: str = ""

    # KRX 정보데이터시스템(data.krx.co.kr) 로그인 — pykrx 투자자 수급(개인/외인/기관)
    # 과거 백필용(선택). 미설정이면 빈 문자열 → 수급은 당일치 누적만(KRX 백필 비활성).
    # ⚠️ pykrx는 os.environ을 읽으므로, 백필 사용 시 이 값을 os.environ에 export 해야 함.
    krx_id: str = ""
    krx_pw: str = ""

    # 예탁결제원 외화증권 결제정보(data.go.kr) — 서학개미(한국인 미국주식) 종목별 순매수.
    # 미설정이면 빈 문자열 → 서학개미 배지 비활성(선택 기능).
    ksd_api_key: str = ""

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
