"""KST 시각과 KRX 영업일 계산.

a-Shell 에는 tzdata 가 없을 수 있어 zoneinfo 대신 고정 오프셋(UTC+9, 서머타임 없음)을 쓴다.
휴장일은 holidays 패키지(한국 공휴일·대체공휴일)에 KRX 휴장(근로자의 날·연말 휴장)을 더해 매번 계산한다.
임시공휴일 등 패키지에 없는 날만 config.json 의 market_holidays 에 직접 적는다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import holidays as kr_holidays

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


def krx_holidays(years: list[int]) -> set[str]:
    """해당 연도들의 KRX 휴장일(YYYY-MM-DD). 공휴일 + 5/1 + 연말 마지막 평일(공휴일이면 그 앞 평일)."""
    days = {d.isoformat() for d in kr_holidays.KR(years=years)}
    for y in years:
        days.add(f"{y}-05-01")
        last = date(y, 12, 31)
        while last.weekday() >= 5 or last.isoformat() in days:
            last -= timedelta(days=1)
        days.add(last.isoformat())
    return days
