"""전 종목 시세·수급 제공자.

계획서는 pykrx 를 1순위로 두지만, KRX 정보데이터시스템이 로그인 필수로 바뀌어
KRX 계정이 있을 때만 pykrx 를 쓰고 없거나 실패하면 네이버 증권으로 폴백한다.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date

from . import market_calendar as cal
from .parallel import pmap
from .sources import krx, naver

log = logging.getLogger(__name__)


class NaverProvider:
    name = "naver"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def snapshot(self, today: date) -> tuple[str, list[dict]]:
        min_cap = self.cfg["funnel"]["min_market_cap_krw"]
        rows = naver.market_listing("KOSPI", min_cap) + naver.market_listing("KOSDAQ", min_cap)
        dates = Counter(r["traded_at"][:10].replace("-", "") for r in rows if r.get("traded_at"))
        trade_date = dates.most_common(1)[0][0] if dates else cal.ymd(today)
        return trade_date, rows

    def flows(self, codes: list[str], trade_date: str) -> dict[str, dict]:
        days = self.cfg["quant"]["trend_days"]
        ok, _ = pmap(
            lambda c: naver.investor_trend(c, days),
            codes,
            self.cfg["data"]["max_workers"],
            label="네이버 투자자 동향",
        )
        out = {}
        for code, trend in ok.items():
            if not trend:
                continue
            latest = trend[0]
            out[code] = {
                "date": latest["date"],
                "foreign_value": latest["foreign_value"],
                "organ_value": latest["organ_value"],
                "trend": trend,
            }
        return out


class PykrxProvider:
    name = "pykrx"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def snapshot(self, today: date) -> tuple[str, list[dict]]:
        trade_date = krx.latest_business_day(cal.ymd(today))
        rows = krx.market_snapshot(trade_date)
        if not rows:
            raise RuntimeError(f"pykrx 시세가 비어 있음 ({trade_date})")
        return trade_date, rows

    def flows(self, codes: list[str], trade_date: str) -> dict[str, dict]:
        wanted = set(codes)
        return {c: v for c, v in krx.net_purchases(trade_date).items() if c in wanted}


def providers_for(cfg: dict) -> list:
    choice = cfg["data"].get("provider", "auto")
    chain: list = []
    if choice in ("auto", "pykrx"):
        ok, reason = krx.available()
        if ok:
            chain.append(PykrxProvider(cfg))
        elif choice == "pykrx":
            log.warning("pykrx 사용 불가 (%s) — 네이버로 대체", reason)
    chain.append(NaverProvider(cfg))
    return chain
