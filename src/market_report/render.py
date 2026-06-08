"""HTML 리포트 렌더링 — Jinja2.

생성 경로:
  docs/reports/YYYY-MM-DD-pre.html   (마감 전)
  docs/reports/YYYY-MM-DD-post.html  (마감 후)
  docs/index.html                    (최신 리포트 카드 + 히스토리)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.datasource.market_map import ensure_maps, label_any
from src.market_report.models import MarketSnapshot, ReportMode

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = PROJECT_ROOT / "src" / "market_report" / "templates"
DOCS_DIR = PROJECT_ROOT / "docs"
REPORTS_DIR = DOCS_DIR / "reports"
INDEX_FILE = DOCS_DIR / "index.html"
HISTORY_FILE = REPORTS_DIR / "_history.json"  # 누적 리포트 메타


def _env() -> Environment:
    from src.datasource.market_cap import format_marcap

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["fmt_won"] = format_marcap  # 거래대금/금액 원화(조/억) 포맷
    env.globals["mkt"] = label_any  # 종목 소속 시장 라벨(코스피/코스닥/나스닥…, #471)
    return env


def report_path(snap: MarketSnapshot) -> Path:
    """리포트 출력 경로."""
    date_str = snap.generated_at.strftime("%Y-%m-%d")
    suffix = {"pre_close": "pre", "post_close": "post", "us_morning": "us",
              "midday": "midday", "us_premarket": "us-pre",
              "us_intraday": "us-mid", "kr_premarket": "kr-pre", "kr_open": "kr-open"}.get(snap.mode, "post")
    return REPORTS_DIR / f"{date_str}-{suffix}.html"


def report_url_rel(snap: MarketSnapshot) -> str:
    """index.html → 리포트 상대 URL."""
    date_str = snap.generated_at.strftime("%Y-%m-%d")
    suffix = {"pre_close": "pre", "post_close": "post", "us_morning": "us",
              "midday": "midday", "us_premarket": "us-pre",
              "us_intraday": "us-mid", "kr_premarket": "kr-pre", "kr_open": "kr-open"}.get(snap.mode, "post")
    return f"reports/{date_str}-{suffix}.html"


def render_report(snap: MarketSnapshot) -> Path:
    """단일 리포트 HTML 생성. 히스토리·index도 갱신."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    try:  # 시장 라벨 맵(일1회 캐시) — 실패 시 라벨만 생략(#471)
        ensure_maps()
    except Exception as exc:  # noqa: BLE001
        logger.warning("market_map_ensure_failed error=%s", exc)

    env = _env()
    template = env.get_template("report.html")

    title = {
        "pre_close": "마감 전 시장 리포트 (종가베팅)",
        "post_close": "마감 후 시장 리포트",
        "us_morning": "미국 증시 아침 요약",
        "midday": "장중 시장 리포트",
        "us_premarket": "미국장 장전(프리장) 리포트",
        "us_intraday": "미국장 장중 리포트 (잠정)",
        "kr_premarket": "한국장 프리 리포트 (NXT 프리장)",
        "kr_open": "한국장 장초 리포트",
    }.get(snap.mode, "시장 리포트")
    html = template.render(title=title, snap=snap)

    out = report_path(snap)
    out.write_text(html, encoding="utf-8")
    logger.info("report_rendered path=%s mode=%s", out, snap.mode)

    _update_history(snap)
    _render_index()
    return out


def _update_history(snap: MarketSnapshot) -> None:
    """리포트 히스토리 JSON 갱신 (index.html 카드용)."""
    history: list[dict] = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = []

    entry = {
        "date": snap.generated_at.strftime("%Y-%m-%d"),
        "time": snap.generated_at.strftime("%H:%M"),
        "mode": snap.mode,
        "url": report_url_rel(snap),
        "summary": snap.summary[:140] if snap.summary else "",
        "kospi_pct": snap.kospi.change_pct if snap.kospi else None,
        "kosdaq_pct": snap.kosdaq.change_pct if snap.kosdaq else None,
    }

    # 같은 (date, mode) 항목 제거 후 추가 (재실행 대응)
    history = [
        h for h in history
        if not (h.get("date") == entry["date"] and h.get("mode") == entry["mode"])
    ]
    history.append(entry)
    # 최신순 정렬
    history.sort(key=lambda h: (h["date"], h["time"]), reverse=True)
    # 최근 60개만 보관
    history = history[:60]

    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _render_index() -> None:
    """index.html — 최근 리포트 목록 페이지."""
    history: list[dict] = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = []

    env = _env()
    # 인라인 템플릿 (별도 파일 안 만들고 여기서 처리)
    template_str = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>📊 Daily Stock Report</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      min-height: 100vh;
      padding: 1.5rem 1rem;
      line-height: 1.6;
    }
    .container { max-width: 720px; margin: 0 auto; }
    h1 { font-size: 1.75rem; color: #60a5fa; margin-bottom: 0.5rem; }
    .sub { color: #94a3b8; margin-bottom: 2rem; }
    .empty {
      background: #1e293b;
      border: 1px dashed #334155;
      border-radius: 12px;
      padding: 2rem;
      text-align: center;
      color: #94a3b8;
    }
    .report-card {
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 1.25rem;
      margin-bottom: 0.75rem;
      text-decoration: none;
      color: #e2e8f0;
      display: block;
      transition: transform 0.15s, border-color 0.15s;
    }
    .report-card:hover { transform: translateY(-2px); border-color: #60a5fa; }
    .card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; flex-wrap: wrap; gap: 0.5rem; }
    .card-date { font-weight: 700; font-size: 1.05rem; }
    .card-time { color: #94a3b8; font-size: 0.85rem; }
    .badge { padding: 0.2rem 0.55rem; border-radius: 999px; font-size: 0.7rem; font-weight: 600; }
    .badge.pre { background: #fbbf2422; color: #fbbf24; }
    .badge.post { background: #60a5fa22; color: #60a5fa; }
    .summary { color: #cbd5e1; font-size: 0.9rem; }
    .indices { font-size: 0.8rem; color: #94a3b8; margin-top: 0.5rem; font-family: monospace; }
    .up { color: #34d399; }
    .down { color: #f87171; }
    footer { margin-top: 2rem; padding-top: 1.5rem; border-top: 1px solid #334155; color: #64748b; font-size: 0.8rem; text-align: center; }
  </style>
</head>
<body>
  <div class="container">
    <h1>📊 Daily Stock Report</h1>
    <p class="sub">한국 주식 일일 시장 동향 자동 리포트 · 매일 평일 14:50 (마감 전) · 16:30 (마감 후)</p>
    <p class="sub"><a href="reports/screen-dashboard.html" style="color:#58a6ff;">📈 전략 스크린 대시보드 (A/B/C · 일자별 · 차트 매수/매도 시그널)</a></p>

    {% if not history %}
    <div class="empty">아직 생성된 리포트가 없습니다. 다음 발송 시간 이후 표시됩니다.</div>
    {% else %}
    {% for r in history %}
    <a href="{{ r.url }}" class="report-card">
      <div class="card-head">
        <div>
          <span class="card-date">{{ r.date }}</span>
          <span class="card-time">{{ r.time }}</span>
        </div>
        <span class="badge {{ 'pre' if r.mode == 'pre_close' else 'post' }}">
          {{ '🟡 마감 전' if r.mode == 'pre_close' else '🔵 마감 후' }}
        </span>
      </div>
      {% if r.summary %}<div class="summary">{{ r.summary }}</div>{% endif %}
      {% if r.kospi_pct is not none or r.kosdaq_pct is not none %}
      <div class="indices">
        {% if r.kospi_pct is not none %}
        KOSPI <span class="{{ 'up' if r.kospi_pct >= 0 else 'down' }}">{{ '+' if r.kospi_pct >= 0 else '' }}{{ "%.2f"|format(r.kospi_pct) }}%</span>
        {% endif %}
        {% if r.kosdaq_pct is not none %}
        · KOSDAQ <span class="{{ 'up' if r.kosdaq_pct >= 0 else 'down' }}">{{ '+' if r.kosdaq_pct >= 0 else '' }}{{ "%.2f"|format(r.kosdaq_pct) }}%</span>
        {% endif %}
      </div>
      {% endif %}
    </a>
    {% endfor %}
    {% endif %}

    <footer>
      ※ 본 리포트는 공개 데이터 기반 참고용 정보입니다. 투자 판단·책임은 본인에게 있습니다.
    </footer>
  </div>
</body>
</html>
"""
    template = env.from_string(template_str)
    INDEX_FILE.write_text(template.render(history=history), encoding="utf-8")
