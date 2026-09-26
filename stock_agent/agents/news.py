"""Agent 2 — 정보 스크래퍼 (News & Disclosure). LLM 토큰 0.

- 종목별 뉴스: 네이버 증권 → 광고성/찌라시 키워드 배제 → [헤드라인 + 핵심 2문장]
- 공시: DART Open API (공식) 우선
- 단기 호재/악재 감성 스코어: 키워드 사전 기반 (−1 ~ +1)
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from .. import market_calendar as cal
from ..http import redact
from ..parallel import pmap
from ..sources import dart, naver

log = logging.getLogger(__name__)

POSITIVE = (
    "수주", "공급계약", "공급 계약", "흑자전환", "흑자 전환", "최대 실적", "사상 최대", "역대 최대", "호실적",
    "어닝 서프라이즈", "깜짝 실적", "상향", "신고가", "급등", "강세", "성장", "확대", "증가", "개선", "협력",
    "계약 체결", "승인", "허가", "특허", "인수", "자사주 매입", "자기주식 취득", "소각", "배당 확대",
    "무상증자", "기술이전", "기술 수출", "수혜", "돌파", "턴어라운드", "양산", "독점",
)
NEGATIVE = (
    "적자", "적자전환", "적자 전환", "하락", "급락", "약세", "감소", "하향", "부진", "소송", "제재", "리콜",
    "유상증자", "전환사채", "CB 발행", "횡령", "배임", "거래정지", "상장폐지", "관리종목", "불성실공시",
    "영업정지", "손실", "악화", "우려", "블록딜", "오버행", "감자", "압수수색", "철회", "해지", "취소",
)
DISCLOSURE_POSITIVE = (
    "공급계약", "자기주식취득", "자기주식 취득", "주식소각", "무상증자", "현금ㆍ현물배당", "현금·현물배당",
    "특허권취득", "신규시설투자", "기술이전", "타법인주식및출자증권취득", "유형자산양수",
)
DISCLOSURE_NEGATIVE = (
    "유상증자", "전환사채", "신주인수권부사채", "교환사채", "감자", "소송", "횡령", "배임", "불성실공시",
    "관리종목", "매매거래정지", "상장폐지", "영업정지", "회생절차", "계약해지", "공급계약해지", "자기주식처분",
)
NEUTRAL_MATERIAL = ("잠정실적", "영업(잠정)실적", "최대주주변경", "합병", "분할")

_BYLINE = re.compile(r"^\s*[\[\(【][^\]\)】]{0,40}[\]\)】]\s*")
_REPORTER = re.compile(r"[가-힣]{2,4}\s*(기자|특파원|객원기자)\s*=?\s*")
_EMAIL = re.compile(r"\S+@\S+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NORMALIZE = re.compile(r"[\s\W_]+")


def is_ad(title: str, body: str, ad_keywords: list[str]) -> bool:
    text = f"{title} {body}"
    return any(k in text for k in ad_keywords)


def key_sentences(body: str, n: int = 2, max_len: int = 180) -> str:
    text = _BYLINE.sub("", body or "")
    text = _REPORTER.sub("", text)
    text = _EMAIL.sub("", text)
    text = re.sub(r"(\.\.\.|…)\s*$", "", text.strip())
    sentences = [s.strip() for s in _SENTENCE.split(text) if len(s.strip()) > 5]
    summary = " ".join(sentences[:n])
    return summary if len(summary) <= max_len else summary[: max_len - 1].rstrip() + "…"


def sentiment_score(title: str, body: str = "") -> float:
    """제목 가중치 2, 본문 1. (호재 − 악재) / (호재 + 악재)."""
    pos = 2 * sum(title.count(k) for k in POSITIVE) + sum(body.count(k) for k in POSITIVE)
    neg = 2 * sum(title.count(k) for k in NEGATIVE) + sum(body.count(k) for k in NEGATIVE)
    return 0.0 if pos + neg == 0 else round((pos - neg) / (pos + neg), 2)


def disclosure_sentiment(report_nm: str) -> float:
    # '공급계약해지'처럼 호재·악재 키워드가 겹치면 악재 우선
    if any(k in report_nm for k in DISCLOSURE_NEGATIVE):
        return -1.0
    if any(k in report_nm for k in DISCLOSURE_POSITIVE):
        return 1.0
    return 0.0


def label(score: float) -> str:
    if score >= 0.2:
        return "호재"
    if score <= -0.2:
        return "악재"
    return "중립"


class NewsAgent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.status: dict[str, str] = {}

    # ---- 결산(15:40): 선정 30종목 ------------------------------------------------
    def collect(self, stocks: list[dict], trade_date: str) -> dict[str, dict]:
        ncfg = self.cfg["news"]
        workers = self.cfg["data"]["max_workers"]
        codes = [s["code"] for s in stocks]
        trade_day = cal.parse_ymd(trade_date)
        cutoff = cal.ymd(trade_day - timedelta(days=ncfg["lookback_days"])) + "0000"

        news_ok, news_fail = pmap(lambda c: naver.stock_news(c, ncfg["per_stock_fetch"]), codes, workers, "종목 뉴스")
        self.status["news"] = "ok" if not news_fail else ("failed" if not news_ok else "partial")

        disclosures: dict[str, list[dict]] = {}
        key = self.cfg["secrets"].get("DART_API_KEY")
        if not key:
            self.status["dart"] = "no_key"
        else:
            bgn = cal.ymd(trade_day - timedelta(days=self.cfg["dart"]["evening_lookback_days"]))
            try:
                dart.corp_codes(key)  # 캐시를 먼저 채워 병렬 호출 시 중복 다운로드 방지
                disclosures, dart_fail = pmap(
                    lambda c: dart.company_disclosures(key, c, bgn, trade_date), codes, workers, "DART 공시"
                )
                self.status["dart"] = "ok" if not dart_fail else "partial"
            except Exception as exc:  # noqa: BLE001
                log.warning("DART 공시 조회 실패: %s", redact(exc))
                self.status["dart"] = "failed"

        bundles = {}
        for code in codes:
            headlines = self._headlines(news_ok.get(code, []), cutoff)
            discl = [
                {
                    "title": d["report_nm"],
                    "date": d.get("rcept_dt", ""),
                    "url": d["url"],
                    "sentiment": disclosure_sentiment(d["report_nm"]),
                }
                for d in disclosures.get(code, [])[:5]
            ]
            scores = [h["sentiment"] for h in headlines] + [d["sentiment"] for d in discl if d["sentiment"]]
            score = round(sum(scores) / len(scores), 2) if scores else 0.0
            bundles[code] = {"headlines": headlines, "disclosures": discl, "sentiment": score, "label": label(score)}
        return bundles

    def _headlines(self, items: list[dict], cutoff: str) -> list[dict]:
        ncfg = self.cfg["news"]
        seen: set[str] = set()
        out = []
        for it in items:
            if it["datetime"] and it["datetime"] < cutoff:
                continue
            if is_ad(it["title"], it["body"], ncfg["ad_keywords"]):
                continue
            norm = _NORMALIZE.sub("", it["title"])[:20]
            if norm in seen:
                continue
            seen.add(norm)
            out.append(
                {
                    "title": it["title"],
                    "summary": key_sentences(it["body"]),
                    "source": it["source"],
                    "datetime": it["datetime"],
                    "url": it["url"],
                    "sentiment": sentiment_score(it["title"], it["body"]),
                }
            )
            if len(out) >= ncfg["per_stock_keep"]:
                break
        return out

    # ---- 모닝(08:30): 개장 전 특징 공시 ------------------------------------------
    def morning_disclosures(self, today, prev_day, universe: dict[str, dict], limit: int = 15) -> list[dict]:
        key = self.cfg["secrets"].get("DART_API_KEY")
        if not key:
            self.status["dart"] = "no_key"
            return []
        try:
            items = dart.market_disclosures(key, cal.ymd(prev_day), cal.ymd(today))
            self.status["dart"] = "ok"
        except Exception as exc:  # noqa: BLE001
            log.warning("DART 시장 공시 조회 실패: %s", redact(exc))
            self.status["dart"] = "failed"
            return []
        out, seen = [], set()
        for d in items:
            code = (d.get("stock_code") or "").strip()
            name = d["report_nm"]
            score = disclosure_sentiment(name)
            material = score != 0 or any(k in name for k in NEUTRAL_MATERIAL)
            if not code or not material or (universe and code not in universe) or (code, name) in seen:
                continue
            seen.add((code, name))
            info = universe.get(code, {})
            out.append(
                {
                    "code": code,
                    "name": d.get("corp_name", info.get("name", "")),
                    "title": name,
                    "date": d.get("rcept_dt", ""),
                    "url": d["url"],
                    "sentiment": score,
                    "label": label(score),
                    "market_cap": info.get("market_cap"),
                }
            )
        # 오늘 접수분 → 호재 → 시총 큰 순
        out.sort(key=lambda x: (x["date"], x["sentiment"], x.get("market_cap") or 0), reverse=True)
        return out[:limit]
