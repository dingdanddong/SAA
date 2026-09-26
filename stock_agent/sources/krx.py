"""pykrx (KRX 정보데이터시스템) 래퍼 — 선택 사항.

2025-12-27부터 KRX 정보데이터시스템이 로그인 필수로 바뀌어 pykrx 1.2.9 는
KRX_ID/KRX_PW 환경변수(KRX 회원 계정)가 있어야 동작한다. 계정이 없거나 로그인에
실패하면 호출 측(providers)이 네이버 소스로 자동 폴백한다.
"""
from __future__ import annotations

import logging
import os

from .. import http

log = logging.getLogger(__name__)

INVESTORS = {"foreign": "외국인", "organ": "기관합계"}


def available() -> tuple[bool, str]:
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        return False, "KRX_ID/KRX_PW 미설정"
    try:
        import pykrx  # noqa: F401
    except ImportError as exc:
        return False, f"pykrx 미설치 ({exc})"
    return True, ""


def _stock():
    from pykrx import stock

    return stock


def latest_business_day(date: str) -> str:
    return http.retry_call(_stock().get_nearest_business_day_in_a_week, date, label="pykrx 영업일 조회")


def market_snapshot(date: str) -> list[dict]:
    """해당 영업일의 KOSPI+KOSDAQ 전 종목 시세·시총."""
    stock = _stock()
    rows: list[dict] = []
    for market in ("KOSPI", "KOSDAQ"):
        cap = http.retry_call(stock.get_market_cap, date, market=market, label=f"pykrx 시총({market})")
        ohlcv = http.retry_call(stock.get_market_ohlcv, date, market=market, label=f"pykrx 시세({market})")
        if cap is None or cap.empty:
            continue
        for code, r in cap.iterrows():
            change = 0.0
            if ohlcv is not None and code in ohlcv.index and "등락률" in ohlcv.columns:
                change = float(ohlcv.at[code, "등락률"])
            rows.append(
                {
                    "code": str(code),
                    "name": stock.get_market_ticker_name(code),
                    "market": market,
                    "close": float(r["종가"]),
                    "change_pct": change,
                    "volume": float(r["거래량"]),
                    "trading_value": float(r["거래대금"]),
                    "market_cap": float(r["시가총액"]),
                    "end_type": "stock",
                    "trade_stop": float(r["거래량"]) == 0,
                    "traded_at": date,
                }
            )
    return rows


def net_purchases(date: str) -> dict[str, dict]:
    """종목코드 → {foreign_value, organ_value} (당일 순매수 거래대금, 원)."""
    stock = _stock()
    out: dict[str, dict] = {}
    for market in ("KOSPI", "KOSDAQ"):
        for key, investor in INVESTORS.items():
            df = http.retry_call(
                stock.get_market_net_purchases_of_equities,
                date,
                date,
                market,
                investor,
                label=f"pykrx 순매수({market},{investor})",
            )
            if df is None or df.empty:
                continue
            for code, r in df.iterrows():
                out.setdefault(str(code), {"date": date, "foreign_value": 0.0, "organ_value": 0.0})
                out[str(code)][f"{key}_value"] = float(r["순매수거래대금"])
    return out
