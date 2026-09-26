"""KST 시각과 KRX 영업일 계산.

a-Shell 에는 tzdata 가 없을 수 있어 zoneinfo 대신 고정 오프셋(UTC+9, 서머타임 없음)을 쓴다.
휴장일은 config.json 의 market_holidays 로 관리한다 (매년 갱신 필요).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse_ymd(s: str) -> date:
    return datetime.strptime(s[:8], "%Y%m%d").date()


def is_trading_day(d: date, holidays: set[str] | list[str]) -> bool:
    return d.weekday() < 5 and d.isoformat() not in set(holidays)


def previous_trading_day(d: date, holidays: set[str] | list[str]) -> date:
    prev = d - timedelta(days=1)
    while not is_trading_day(prev, holidays):
        prev -= timedelta(days=1)
    return prev
