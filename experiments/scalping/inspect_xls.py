"""바탕화면 스캘핑백테스트 *.xls (HTS 분봉 export) 내용 실측.

각 파일의 컬럼·봉개수·날짜범위·시간간격(타임프레임)을 출력한다.
파싱만 — 원본 비변경.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

SRC = Path.home() / "Desktop" / "스캘핑백테스트"


def infer_tf(times: list) -> str:
    """연속 시각의 최빈 간격(분)으로 타임프레임 추정."""
    nums = []
    for t in times:
        s = str(t).strip().replace(":", "")
        if not s.isdigit():
            continue
        s = s.zfill(4)[:4] if len(s) <= 4 else s.zfill(6)[:6]
        hh = int(s[:-2]) if len(s) >= 3 else 0
        mm = int(s[-2:])
        nums.append(hh * 60 + mm)
    diffs = [b - a for a, b in zip(nums, nums[1:]) if 0 < (b - a) < 60]
    if not diffs:
        return "?"
    common = Counter(diffs).most_common(1)[0][0]
    return f"{common}분봉(추정)"


def main() -> None:
    files = sorted(SRC.glob("*.xls"))
    print(f"대상 {len(files)}개 파일 @ {SRC}\n")
    for f in files:
        try:
            df = pd.read_excel(f, engine="xlrd", header=0)
        except Exception as exc:  # noqa: BLE001
            print(f"[{f.name}] 읽기 실패: {type(exc).__name__}: {exc}")
            continue
        cols = list(df.columns)
        n = len(df)
        # 날짜/시간 컬럼 추정 (앞 2개)
        date_col = cols[0]
        time_col = cols[1] if len(cols) > 1 else None
        dates = df[date_col].astype(str).str.strip()
        d_min, d_max = (dates.min(), dates.max()) if n else ("?", "?")
        ndays = dates.nunique() if n else 0
        tf = infer_tf(df[time_col].tolist()) if time_col is not None else "?"
        print(f"[{f.name}]")
        print(f"  봉수={n}  고유날짜={ndays}  범위={d_min}~{d_max}  TF={tf}")
        print(f"  컬럼({len(cols)}): {cols}")
        if n:
            print(f"  첫행: {df.iloc[0].to_dict()}")
            print(f"  끝행: {df.iloc[-1].to_dict()}")
        print()


if __name__ == "__main__":
    main()
