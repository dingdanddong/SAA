"""숫자 표기 헬퍼 (리포트·LLM 페이로드 공용)."""
from __future__ import annotations


def eok(value: float | None, signed: bool = True) -> str:
    """원 → '1,200억' / '+35.2억'."""
    if value is None:
        return "-"
    v = value / 100_000_000
    sign = "+" if signed else ""
    if abs(v) >= 10:
        return f"{v:{sign},.0f}억"
    return f"{v:{sign},.1f}억"


def cap(value: float | None) -> str:
    """시가총액 원 → '115조 7,752억' / '5,295억'."""
    if value is None:
        return "-"
    jo, eok = divmod(round(value / 100_000_000), 10_000)
    if jo:
        return f"{jo:,}조 {eok:,}억" if eok else f"{jo:,}조"
    return f"{eok:,}억"


def price(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}"


def pct(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:+.{digits}f}%"


def arrow(value: float | None) -> str:
    if value is None or value == 0:
        return "-"
    return f"▲ {abs(value):.1f}%" if value > 0 else f"▼ {abs(value):.1f}%"
