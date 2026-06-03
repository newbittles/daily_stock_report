"""주문 수량 계산 — 순수 함수 (외부 의존 없음)."""
from __future__ import annotations

DEFAULT_BUDGET = 1_000_000  # 1회 매수당 예산(원)


def calc_qty(price: float, budget: int = DEFAULT_BUDGET) -> int:
    """예산 이내 최대 정수 매수 수량. price<=0 또는 1주가 예산 초과면 0."""
    if price <= 0 or price > budget:
        return 0
    return int(budget // price)


def split_sell_qty(qty: int) -> tuple[int, int]:
    """2차 50% 분할 매도 → (지금 매도, 잔여). qty=1이면 (1,0)=전량(쪼갤 수 없음)."""
    if qty <= 1:
        return (qty, 0)
    half = qty // 2
    return (half, qty - half)
