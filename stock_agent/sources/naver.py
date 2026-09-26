"""네이버 증권 데이터 소스 (비공식 API).

공개 문서가 없는 모바일/차트 API라 응답 형식이 예고 없이 바뀔 수 있다.
모든 호출은 http 모듈의 3회 재시도를 거치며, 실패 시 호출 측에서 폴백하거나 축소 처리한다.

단위 (2026-09 실측):
- 종목 목록의 accumulatedTradingValue = 백만원, marketValue = 억원
- 투자자 동향(trend)의 순매수 값 = 주식 수량
"""
from __future__ import annotations

import html
import logging
import re
import threading

import pandas as pd

from .. import http

log = logging.getLogger(__name__)

M_API = "https://m.stock.naver.com/api"
FRONT_API = "https://m.stock.naver.com/front-api"
WORLD_API = "https://api.stock.naver.com"
FCHART = "https://fchart.stock.naver.com/sise.nhn"

MILLION = 1_000_000
EOK = 100_000_000

MARKET_BY_SOSOK = {"0": "KOSPI", "1": "KOSDAQ"}
_FALLING = {"FALLING", "LOWER_LIMIT"}
_TAG = re.compile(r"<[^>]+>")
_CHART_ITEM = re.compile(r'<item data="([^"]+)"')

_trend_cache: dict[tuple[str, int], list[dict]] = {}
_trend_lock = threading.Lock()


def to_num(value) -> float | None:
    """'1,234' / '+4,513,767' / '3.62%' / 'N/A' → float 또는 None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if text in ("", "-", "N/A"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _signed(value: float | None, compare: dict | None) -> float | None:
    """일부 응답은 부호 없이 오므로 compareToPreviousPrice 로 하락을 보정한다."""
    if value is None:
        return None
    if compare and compare.get("name") in _FALLING and value > 0:
        return -value
    return value


def clean_text(text: str | None) -> str:
    return _TAG.sub("", html.unescape(text or "")).strip()


def stock_row(item: dict, market: str | None = None) -> dict:
    """목록형 응답(시총순/업종별) 항목을 공통 종목 dict 로 변환."""
    compare = item.get("compareToPreviousPrice")
    trading_value = to_num(item.get("accumulatedTradingValue"))
    market_cap = to_num(item.get("marketValue"))
    trade_stop = (item.get("tradeStopType") or {}).get("name", "TRADING")
    return {
        "code": item["itemCode"],
        "name": item.get("stockName", ""),
        "market": market or MARKET_BY_SOSOK.get(str(item.get("sosok")), ""),
        "close": to_num(item.get("closePrice")),
        "change_pct": _signed(to_num(item.get("fluctuationsRatio")), compare) or 0.0,
        "volume": to_num(item.get("accumulatedTradingVolume")),
        "trading_value": trading_value * MILLION if trading_value is not None else None,
        "market_cap": market_cap * EOK if market_cap is not None else None,
        "end_type": item.get("stockEndType", "stock"),
        "trade_stop": trade_stop != "TRADING",
        "traded_at": item.get("localTradedAt", ""),
    }


def market_listing(market: str, min_market_cap_krw: float = 0, page_size: int = 100) -> list[dict]:
    """KOSPI/KOSDAQ 전 종목을 시가총액 내림차순으로 가져온다.

    시총순 정렬이므로 min_market_cap_krw 아래로 내려가면 페이지 조회를 멈춘다.
    """
    rows: list[dict] = []
    page = 1
    while True:
        data = http.get_json(
            f"{M_API}/stocks/marketValue/{market}", params={"page": page, "pageSize": page_size}
        )
        items = data.get("stocks") or []
        rows.extend(stock_row(it, market) for it in items)
        total = int(data.get("totalCount") or 0)
        if not items or page * page_size >= total:
            break
        last_cap = rows[-1]["market_cap"] or 0
        if min_market_cap_krw and last_cap < min_market_cap_krw:
            break
        page += 1
    return rows


def daily_ohlcv(code: str, count: int = 260) -> pd.DataFrame:
    """일봉 (date, open, high, low, close, volume). 과거 → 최근 순."""
    resp = http.get(FCHART, params={"symbol": code, "timeframe": "day", "count": count, "requestType": 0})
    text = resp.content.decode("euc-kr", errors="replace")
    records = []
    for raw in _CHART_ITEM.findall(text):
        parts = raw.split("|")
        if len(parts) < 6:
            continue
        try:
            records.append(
                {
                    "date": parts[0],
                    "open": float(parts[1]),
                    "high": float(parts[2]),
                    "low": float(parts[3]),
                    "close": float(parts[4]),
                    "volume": float(parts[5]),
                }
            )
        except ValueError:
            continue
    df = pd.DataFrame.from_records(records, columns=["date", "open", "high", "low", "close", "volume"])
    # 신규 상장·거래정지 구간은 0으로 채워지는 경우가 있어 제외
    return df[df["close"] > 0].reset_index(drop=True)


def investor_trend(code: str, days: int = 20) -> list[dict]:
    """일별 외국인/기관 순매수 (최근 → 과거 순). 순매수 금액은 수량 × 종가로 근사."""
    key = (code, days)
    with _trend_lock:
        if key in _trend_cache:
            return _trend_cache[key]
    data = http.get_json(f"{M_API}/stock/{code}/trend", params={"pageSize": days})
    rows = []
    for item in data or []:
        close = to_num(item.get("closePrice")) or 0.0
        foreign_qty = to_num(item.get("foreignerPureBuyQuant")) or 0.0
        organ_qty = to_num(item.get("organPureBuyQuant")) or 0.0
        rows.append(
            {
                "date": item.get("bizdate", ""),
                "close": close,
                "foreign_qty": foreign_qty,
                "organ_qty": organ_qty,
                "foreign_value": foreign_qty * close,
                "organ_value": organ_qty * close,
                "foreign_hold_pct": to_num(item.get("foreignerHoldRatio")),
            }
        )
    with _trend_lock:
        _trend_cache[key] = rows
    return rows


def stock_news(code: str, page_size: int = 10) -> list[dict]:
    """종목 뉴스 (최신순). 같은 사건의 묶음 기사는 대표 기사 1건만 남긴다."""
    data = http.get_json(f"{M_API}/news/stock/{code}", params={"pageSize": page_size, "page": 1})
    out = []
    for group in data or []:
        items = group.get("items") or []
        if not items:
            continue
        it = items[0]
        out.append(
            {
                "title": clean_text(it.get("titleFull") or it.get("title")),
                "body": clean_text(it.get("body")),
                "source": it.get("officeName", ""),
                "datetime": it.get("datetime", ""),  # YYYYMMDDHHMM
                "url": it.get("mobileNewsUrl", ""),
            }
        )
    return out


def industry_of(code: str) -> tuple[str, list[dict]]:
    """(네이버 업종코드, 동일 업종 비교 종목). 업종코드가 없으면 ''."""
    data = http.get_json(f"{M_API}/stock/{code}/integration")
    peers = [stock_row(p) for p in data.get("industryCompareInfo") or [] if p.get("itemCode")]
    return str(data.get("industryCode") or ""), peers


def industry_members(industry_code: str, page_size: int = 100, max_pages: int = 5) -> tuple[str, list[dict]]:
    """(업종명, 업종 구성 종목 전체)."""
    name, rows = "", []
    for page in range(1, max_pages + 1):
        data = http.get_json(
            f"{M_API}/stocks/industry/{industry_code}", params={"page": page, "pageSize": page_size}
        )
        name = (data.get("groupInfo") or {}).get("name", name)
        items = data.get("stocks") or []
        rows.extend(stock_row(it) for it in items)
        if not items or page * page_size >= int(data.get("totalCount") or 0):
            break
    return name, rows


def domestic_index(code: str) -> dict:
    """KOSPI / KOSDAQ 지수."""
    data = http.get_json(f"{M_API}/index/{code}/basic")
    compare = data.get("compareToPreviousPrice")
    return {
        "name": data.get("stockName") or code,
        "close": to_num(data.get("closePrice")),
        "change": _signed(to_num(data.get("compareToPreviousClosePrice")), compare),
        "change_pct": _signed(to_num(data.get("fluctuationsRatio")), compare),
        "traded_at": data.get("localTradedAt", ""),
    }


def world_index(reuters_code: str) -> dict:
    """해외 지수 (.DJI, .INX, .IXIC, .SOX 등)."""
    data = http.get_json(f"{WORLD_API}/index/{reuters_code}/basic")
    compare = data.get("compareToPreviousPrice")
    return {
        "name": data.get("indexName") or reuters_code,
        "close": to_num(data.get("closePriceRaw") or data.get("closePrice")),
        "change": _signed(to_num(data.get("compareToPreviousClosePriceRaw")), compare),
        "change_pct": _signed(to_num(data.get("fluctuationsRatioRaw") or data.get("fluctuationsRatio")), compare),
        "traded_at": data.get("localTradedAt", ""),
    }


def fx_rate(reuters_code: str = "FX_USDKRW") -> dict:
    data = http.get_json(
        f"{FRONT_API}/marketIndex/productDetail", params={"category": "exchange", "reutersCode": reuters_code}
    )
    result = data.get("result") or {}
    compare = result.get("compareToPreviousPrice")
    return {
        "name": result.get("name") or reuters_code,
        "close": to_num(result.get("closePrice")),
        "change": _signed(to_num(result.get("fluctuations")), compare),
        "change_pct": _signed(to_num(result.get("fluctuationsRatio")), compare),
        "traded_at": result.get("localTradedAt", ""),
    }
