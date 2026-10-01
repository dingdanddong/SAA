"""Agent 3 — 차트·트렌드 분석 (Quant & Forecast).

- 기술 지표: 이평선 정배열, 거래량 폭발, RSI(14), 지지/저항, 손절 참고가, 52주 고점, 눌림목
- 수급: 외국인·기관 동시 순매수, 연속 순매수 일수, 수급 집중도(순매수/거래대금)
- Gemini 로 테마별 예상 밴드·시나리오 도출 (1회 호출), 실패 시 정량 밴드로 축소(degraded)
"""
from __future__ import annotations

import json
import logging

import pandas as pd

from .. import compliance, fmt, indicators, llm
from ..parallel import pmap
from ..sources import naver

log = logging.getLogger(__name__)

SCENARIOS = {"상승 확산": 1, "박스권": 0, "조정": -1}
CONFIDENCE = ("낮음", "보통", "높음")

THEME_REQUEST = (
    "대장주의 수급 탄력성을 객관적으로 평가하고, 연관주 2개 중 상대적으로 상승 여력이 관찰되는 "
    "1개를 지목하여 관찰 포인트를 제시"
)

_RULES = """반드시 지킬 규칙:
1. 입력 JSON에 있는 숫자·뉴스·공시만 근거로 쓴다. 입력에 없는 실적, 수치, 사건, 제품 정보를 지어내지 않는다.
2. 매매를 지시하거나 권유하는 표현을 쓰지 않는다. 금지: 매수/매도 추천, 비중 확대/축소, 목표가, 수익 보장, '반드시', '확실'.
   허용: '~가능성이 관찰됨', '~여부가 참고 지표', '~흐름이 확인됨' 같은 관찰형 문장.
3. 모든 문장은 간결한 한국어 보고체(~함, ~됨)로 쓰고 각 문자열은 150자 이내로 한다.
4. 출력은 output_format 과 같은 구조의 JSON 객체 하나만 반환한다."""

SYSTEM_EVENING = f"""너는 한국 주식시장 데이터를 해석하는 정량 분석 보조자다.
입력은 파이썬이 전 종목에서 추려낸 테마 클러스터(대장주 1 + 연관주 2)이고, 테마 안의 수급 확산(낙수효과) 가능성을 본다.

{_RULES}
5. focus_code 는 해당 테마 related_stocks 중 하나의 code 여야 한다.
6. band_low/band_high 는 focus 종목의 다음 거래일 예상 가격 범위(원)다. 입력의 band_ref 근처로 잡고 종가 대비 ±15%를 넘기지 않는다.
7. key_level 은 focus 종목의 support 또는 resistance 값(원)을 우선 사용한다.
8. scenario 는 "상승 확산", "박스권", "조정" 중 하나, confidence 는 "낮음", "보통", "높음" 중 하나다."""

OUTPUT_EVENING = {
    "market_summary": "시장 전반 관찰 2~3문장",
    "themes": [
        {
            "theme_id": 1,
            "leader_view": "대장주 수급 탄력성 평가 1~2문장",
            "focus_code": "지목한 연관주 code",
            "observation": "지목 연관주의 관찰 포인트 1~2문장",
            "scenario": "상승 확산 | 박스권 | 조정",
            "band_low": 0,
            "band_high": 0,
            "key_level": 0,
            "confidence": "낮음 | 보통 | 높음",
        }
    ],
}

SYSTEM_MORNING = f"""너는 한국 주식시장 개장 전 브리프를 쓰는 분석 보조자다.
입력은 전일 미국 증시 마감, 환율, 개장 전 공시, 전일 결산에서 추린 관찰 후보다.

{_RULES}
5. candidates 의 code 는 입력 gap_candidates 에 있는 종목만 쓴다."""

OUTPUT_MORNING = {
    "summary": "개장 전 시장 환경 관찰 2~3문장",
    "watch_points": ["오늘 관찰 포인트 (최대 4개)"],
    "candidates": [{"code": "종목코드", "observation": "관찰 포인트 1문장"}],
}


def tech_summary(t: dict, cfg: dict) -> str:
    q = cfg["quant"]
    parts = [t["ma_state"]]
    if t["new_high"]:
        parts.append("52주 신고가")
    elif t["pct_from_high"] is not None and t["pct_from_high"] >= -q["near_high_pct"]:
        parts.append(f"52주 고점 대비 {t['pct_from_high']:.1f}%")
    if t["volume_ratio"] and t["volume_ratio"] >= q["volume_spike_ratio"]:
        parts.append(f"거래량 {t['volume_ratio']:.1f}배 급증")
    if t["rsi"] is not None:
        tag = " 과열" if t["rsi"] >= 70 else (" 침체" if t["rsi"] <= 30 else "")
        parts.append(f"RSI {t['rsi']:.0f}{tag}")
    if t["pullback"]:
        parts.append("눌림목")
    return ", ".join(parts)


def supply_summary(t: dict) -> str:
    if t.get("foreign_net") is None:
        return "수급 데이터 없음"
    parts = [f"외인 {fmt.eok(t['foreign_net'])}", f"기관 {fmt.eok(t['organ_net'])}"]
    if t.get("foreign_streak", 0) >= 2:
        parts.append(f"외인 {t['foreign_streak']}일 연속 순매수")
    if t.get("organ_streak", 0) >= 2:
        parts.append(f"기관 {t['organ_streak']}일 연속 순매수")
    if t.get("both_buy"):
        parts.append("외인·기관 동시 순매수")
    return " · ".join(parts)


def rule_scenario(t: dict) -> str:
    rsi = t.get("rsi") or 50
    if t["ma_state"] == "역배열" or rsi >= 75:
        return "조정"
    if t["ma_state"] == "정배열" and (t.get("supply_net") or 0) > 0:
        return "상승 확산"
    return "박스권"


class QuantAgent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.status: dict[str, str] = {}
        self._history: dict[str, pd.DataFrame] = {}

    # ---- 가격 이력 -------------------------------------------------------------
    def history(self, code: str) -> pd.DataFrame:
        if code not in self._history:
            self._history[code] = naver.daily_ohlcv(code, self.cfg["quant"]["history_days"])
        return self._history[code]

    # ---- 종목별 기술·수급 분석 ---------------------------------------------------
    def analyze(self, stocks: list[dict], flows: dict[str, dict]) -> dict[str, dict]:
        q = self.cfg["quant"]
        workers = self.cfg["data"]["max_workers"]
        codes = [s["code"] for s in stocks]
        hist_ok, hist_fail = pmap(self.history, codes, workers, "일봉 조회")
        trend_ok, _ = pmap(lambda c: naver.investor_trend(c, q["trend_days"]), codes, workers, "투자자 동향")
        self.status["history"] = "ok" if not hist_fail else "partial"

        techs = {}
        for s in stocks:
            code = s["code"]
            df = hist_ok.get(code)
            if df is None or len(df) < 20:
                continue
            t = indicators.analyze(df, q["rsi_period"], q["max_stop_pct"])
            # 장중·장마감 직후엔 목록 시세가 일봉보다 최신일 수 있어 목록 값을 우선
            t["close"] = s.get("close") or t["close"]
            t["change_pct"] = s.get("change_pct", 0.0)
            t["trading_value"] = s.get("trading_value")
            trend = trend_ok.get(code) or (flows.get(code) or {}).get("trend") or []
            flow = flows.get(code)
            if flow:
                t["foreign_net"], t["organ_net"] = flow["foreign_value"], flow["organ_value"]
            elif trend:
                t["foreign_net"], t["organ_net"] = trend[0]["foreign_value"], trend[0]["organ_value"]
            if t.get("foreign_net") is not None:
                t["supply_net"] = t["foreign_net"] + t["organ_net"]
                t["supply_ratio"] = t["supply_net"] / t["trading_value"] * 100 if t.get("trading_value") else 0.0
                t["both_buy"] = t["foreign_net"] > 0 and t["organ_net"] > 0
            t["foreign_streak"] = indicators.streak([r["foreign_qty"] for r in trend])
            t["organ_streak"] = indicators.streak([r["organ_qty"] for r in trend])
            t["tech_text"] = tech_summary(t, self.cfg)
            t["supply_text"] = supply_summary(t)
            techs[code] = t
        return techs

    # ---- Theme Cluster Payload (계획서 4절) -------------------------------------
    def build_payloads(self, themes: list[dict], techs: dict, news: dict) -> list[dict]:
        payloads = []
        for th in themes:
            leader = th["leader"]
            members = [leader] + th["related"]
            headlines, disclosures = [], []
            for m in members:
                bundle = news.get(m["code"]) or {}
                headlines += [f"[{m['name']}] {h['title']} — {h['summary']}" for h in bundle.get("headlines", [])]
                disclosures += [f"[{m['name']}] {d['title']}" for d in bundle.get("disclosures", [])]
            sentiments = [(news.get(m["code"]) or {}).get("sentiment", 0.0) for m in members]
            payloads.append(
                {
                    "theme_id": th["theme_id"],
                    "market": th["market"],
                    "theme": th["theme"],
                    "leader": self._stock_payload(leader, techs),
                    "related_stocks": [
                        self._stock_payload(r, techs, reason=self._related_reason(r, th)) for r in th["related"]
                    ],
                    "news": headlines[:6],
                    "disclosures": disclosures[:4],
                    "news_sentiment": round(sum(sentiments) / len(sentiments), 2),
                    "request": THEME_REQUEST,
                }
            )
        return payloads

    def _stock_payload(self, s: dict, techs: dict, reason: str | None = None) -> dict:
        t = techs.get(s["code"], {})
        out = {
            "code": s["code"],
            "name": s["name"],
            "price": int(s.get("close") or 0),
            "change": fmt.pct(s.get("change_pct")),
            "tech": t.get("tech_text", "지표 없음"),
            "supply": t.get("supply_text", "수급 데이터 없음"),
            "rsi": round(t["rsi"], 1) if t.get("rsi") is not None else None,
            "support": t.get("support"),
            "resistance": t.get("resistance"),
            "band_ref": [t.get("band_low"), t.get("band_high")],
        }
        if reason:
            out["reason"] = reason
        return out

    @staticmethod
    def _related_reason(r: dict, theme: dict) -> str:
        parts = [r.get("relation") or f"동일 업종({theme['theme']})", f"거래대금 {fmt.eok(r.get('trading_value'), signed=False)}"]
        if r.get("supply_net") is not None:
            parts.append(f"외인+기관 {fmt.eok(r['supply_net'])}")
        return " · ".join(parts)

    # ---- Stage 5: AI 심층 추론 ----------------------------------------------------
    def forecast(self, payloads: list[dict], techs: dict, market_ctx: dict, use_llm: bool = True) -> dict:
        result = {"mode": "degraded", "model": None, "reason": "", "market_summary": None, "themes": {}}
        raw = None
        if not use_llm:
            result["reason"] = "LLM 비활성화 (--no-llm)"
        elif not payloads:
            result["reason"] = "분석할 테마 없음"
        else:
            user = json.dumps(
                {"market": market_ctx, "themes": payloads, "output_format": OUTPUT_EVENING},
                ensure_ascii=False,
            )
            try:
                raw, model = llm.generate_json(self.cfg, SYSTEM_EVENING, user)
                result.update(mode="ai", model=model)
            except llm.LLMUnavailable as exc:
                result["reason"] = str(exc)
                log.warning("LLM 사용 불가 → 축소 리포트: %s", exc)

        by_id = {}
        if isinstance(raw, dict):
            result["market_summary"] = compliance.sanitize(raw.get("market_summary"))
            for item in raw.get("themes") or []:
                try:
                    by_id[int(item.get("theme_id"))] = item
                except (TypeError, ValueError):
                    continue
        for p in payloads:
            result["themes"][p["theme_id"]] = self._validated(p, by_id.get(p["theme_id"]), techs)
        return result

    def _validated(self, payload: dict, item: dict | None, techs: dict) -> dict:
        related = payload["related_stocks"]
        related_codes = [r["code"] for r in related]
        # 규칙 기반 지목: 지표가 있는 종목 → 수급 집중도 → 등락률 순
        rule_focus = max(
            related,
            key=lambda r: (
                r["code"] in techs,
                (techs.get(r["code"]) or {}).get("supply_ratio") or 0,
                (techs.get(r["code"]) or {}).get("change_pct") or 0,
            ),
        )["code"]
        source = "ai" if item else "quant"
        item = item or {}
        focus = item.get("focus_code") if item.get("focus_code") in related_codes else rule_focus
        t = techs.get(focus) or {}
        close = t.get("close") or next(r["price"] for r in related if r["code"] == focus)

        scenario = item.get("scenario") if item.get("scenario") in SCENARIOS else (rule_scenario(t) if t else "박스권")
        band_low, band_high, band_source = _num(item.get("band_low")), _num(item.get("band_high")), source
        if band_low and band_high and close * 0.85 <= band_low < band_high <= close * 1.15:
            band_low, band_high = indicators.round_tick(band_low, "down"), indicators.round_tick(band_high, "up")
        else:
            band_low, band_high, band_source = t.get("band_low"), t.get("band_high"), "quant"
        key_level = _num(item.get("key_level"))
        if key_level and close * 0.7 <= key_level <= close * 1.3:
            key_level = indicators.round_tick(key_level)
        else:
            key_level = t.get("support") or t.get("band_low")

        return {
            "source": source,
            "band_source": band_source,
            "focus_code": focus,
            "focus_name": next(r["name"] for r in related if r["code"] == focus),
            "base_close": close,
            "leader_view": compliance.sanitize(item.get("leader_view")) or None,
            "observation": compliance.sanitize(item.get("observation")) or None,
            "scenario": scenario,
            "band_low": band_low,
            "band_high": band_high,
            "key_level": key_level,
            "confidence": item.get("confidence") if item.get("confidence") in CONFIDENCE else None,
        }

    # ---- 52주 신고가 근접 / 눌림목 Top N -----------------------------------------
    def top_picks(self, stocks: list[dict], techs: dict) -> list[dict]:
        q = self.cfg["quant"]
        picks = []
        for s in {s["code"]: s for s in stocks}.values():
            t = techs.get(s["code"])
            if not t:
                continue
            kind = None
            near = t["pct_from_high"] is not None and t["pct_from_high"] >= -q["near_high_pct"]
            if near and t["ma_state"] != "역배열" and t.get("ma20") and t["close"] >= t["ma20"]:
                kind = "52주 신고가" if t["new_high"] else "신고가 근접"
            elif t["pullback"]:
                kind = "눌림목"
            if not kind:
                continue
            score = (t.get("supply_ratio") or 0) + (5 if t.get("both_buy") else 0) + min(t.get("volume_ratio") or 0, 3)
            picks.append({"code": s["code"], "name": s["name"], "market": s.get("market"), "kind": kind, "score": score, **t})
        picks.sort(key=lambda p: p["score"], reverse=True)
        return picks[: q["top_picks"]]

    # ---- 모닝 브리프 AI 코멘트 -------------------------------------------------------
    def morning_outlook(self, payload: dict, use_llm: bool = True) -> dict:
        result = {"mode": "degraded", "model": None, "reason": "", "summary": None, "watch_points": [], "candidates": {}}
        if not use_llm:
            result["reason"] = "LLM 비활성화 (--no-llm)"
            return result
        user = json.dumps({**payload, "output_format": OUTPUT_MORNING}, ensure_ascii=False)
        try:
            raw, model = llm.generate_json(self.cfg, SYSTEM_MORNING, user)
        except llm.LLMUnavailable as exc:
            result["reason"] = str(exc)
            log.warning("LLM 사용 불가 → 축소 브리프: %s", exc)
            return result
        if not isinstance(raw, dict):
            result["reason"] = "LLM 응답 형식 오류"
            return result
        valid_codes = {c["code"] for c in payload.get("gap_candidates", [])}
        result.update(
            mode="ai",
            model=model,
            summary=compliance.sanitize(raw.get("summary")) or None,
            watch_points=[compliance.sanitize(w) for w in (raw.get("watch_points") or [])[:4] if isinstance(w, str)],
            candidates={
                c["code"]: compliance.sanitize(c.get("observation"))
                for c in raw.get("candidates") or []
                if isinstance(c, dict) and c.get("code") in valid_codes
            },
        )
        return result


def _num(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None
