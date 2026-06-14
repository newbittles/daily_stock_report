"""KIS 접근토큰 관리 — 자동 발급·갱신·캐시.

- access_token 유효기간 24시간 → 만료 전 자동 갱신
- 토큰은 gitignore된 런타임 캐시 파일에 보관 — **환경별 분리**(data/kis_token_{real,paper}.json).
  (실전·모의를 한 파일에 캐시하면 서로 덮어써 매번 재발급→분당1회 제한 위반→403, 2026-06-14 자동매매 버그)
- KIS는 토큰 재발급을 분당 1회로 제한 → 캐시 필수 (잦은 발급 금지)

엔드포인트: POST /oauth2/tokenP  (grant_type=client_credentials)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TOKEN_CACHE = PROJECT_ROOT / "data" / "kis_token.json"

# 실전/모의 도메인
BASE_URL = {
    "real": "https://openapi.koreainvestment.com:9443",
    "paper": "https://openapivts.koreainvestment.com:29443",
}

# 만료 안전 마진 (실제 만료 10분 전에 갱신)
REFRESH_MARGIN = timedelta(minutes=10)


class KisTokenManager:
    """access_token 발급·캐시·자동 갱신."""

    def __init__(self, app_key: str, app_secret: str, env: str = "paper") -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._env = env
        self._base = BASE_URL[env]
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._load_cache()

    def _cache_file(self) -> Path:
        """환경별 토큰 캐시 파일 — 실전/모의가 서로 캐시를 덮어쓰지 않도록 분리(2026-06-14).

        모듈 전역 TOKEN_CACHE(테스트가 monkeypatch)의 디렉터리에 env 접미사 파일로 둔다."""
        return TOKEN_CACHE.parent / f"kis_token_{self._env}.json"

    @property
    def base_url(self) -> str:
        return self._base

    def _load_cache(self) -> None:
        """캐시 파일에서 토큰 복원 (환경·만료 검증)."""
        cache = self._cache_file()
        if not cache.exists():
            return
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("env") != self._env:
                return  # 환경 바뀌면 무효
            expires_at = datetime.fromisoformat(data["expires_at"])
            if datetime.now() < expires_at - REFRESH_MARGIN:
                self._token = data["token"]
                self._expires_at = expires_at
                logger.info("kis_token_cache_hit expires=%s", expires_at.isoformat())
        except Exception as exc:
            logger.warning("kis_token_cache_load_failed error=%s", exc)

    def _save_cache(self) -> None:
        cache = self._cache_file()
        cache.parent.mkdir(parents=True, exist_ok=True)
        try:
            cache.write_text(
                json.dumps({
                    "env": self._env,
                    "token": self._token,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                }, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("kis_token_cache_save_failed error=%s", exc)

    def _is_valid(self) -> bool:
        if not self._token or not self._expires_at:
            return False
        return datetime.now() < self._expires_at - REFRESH_MARGIN

    async def get_token(self) -> str:
        """유효 토큰 반환. 만료/없음이면 재발급."""
        if self._is_valid():
            return self._token  # type: ignore[return-value]
        return await self._issue()

    async def _issue(self) -> str:
        """토큰 신규 발급."""
        url = f"{self._base}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        self._token = data["access_token"]
        # expires_in(초) 또는 access_token_token_expired(문자열) 제공
        expires_in = int(data.get("expires_in", 86400))
        self._expires_at = datetime.now() + timedelta(seconds=expires_in)
        self._save_cache()
        logger.info("kis_token_issued env=%s expires_in=%ds", self._env, expires_in)
        return self._token

    def auth_headers(self, tr_id: str, *, tr_cont: str = "") -> dict[str, str]:
        """공통 인증 헤더 (토큰은 get_token으로 미리 확보 후 호출)."""
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "tr_cont": tr_cont,
            "custtype": "P",  # 개인
        }
