"""역피라미드 필터링 Stage 1~3 (0 토큰, 순수 Python).

Stage 1  퀀트 하드 필터   전 종목 → 150  (거래대금·시총·동전주·관리종목·거래정지 제외, 거래대금 순)
Stage 2  수급 필터        150 → 50      (당일 외국인+기관 순매수 합계 순)
Stage 3  테마 클러스터    50 → 30       (시장별 거래대금 주도주 5개 × 동일 업종 연관주 2개)
"""
from __future__ import annotations

import logging

from .http import redact
from .sources import kind, naver

log = logging.getLogger(__name__)


def is_common_stock(row: dict, cfg: dict) -> bool:
    f = cfg["funnel"]
    if row.get("end_type", "stock") != "stock" or row.get("trade_stop"):
        return False
    # 보통주 코드는 끝자리가 0 (우선주 5·7·9·K·L 등)
    if f.get("exclude_preferred", True) and not row["code"].endswith("0"):
        return False
    if f.get("exclude_spac", True) and "스팩" in row.get("name", ""):
        return False
    return True


def stage1_hard_filter(universe: list[dict], excluded: set[str], cfg: dict) -> list[dict]:
    f = cfg["funnel"]
    passed = [
        r
        for r in universe
        if r["code"] not in excluded
        and is_common_stock(r, cfg)
        and (r.get("close") or 0) >= f["min_price_krw"]
        and (r.get("trading_value") or 0) >= f["min_trading_value_krw"]
        and (r.get("market_cap") or 0) >= f["min_market_cap_krw"]
    ]
    passed.sort(key=lambda r: r["trading_value"], reverse=True)
    log.info("Stage1 퀀트 필터: %d → %d (상위 %d 유지)", len(universe), len(passed), f["stage1_keep"])
    return passed[: f["stage1_keep"]]


def attach_flows(rows: list[dict], flows: dict[str, dict]) -> None:
    for r in rows:
        fl = flows.get(r["code"])
        if not fl:
            continue
        r["foreign_net"] = fl["foreign_value"]
        r["organ_net"] = fl["organ_value"]
        r["supply_net"] = fl["foreign_value"] + fl["organ_value"]
        r["supply_ratio"] = r["supply_net"] / r["trading_value"] * 100 if r.get("trading_value") else 0.0
        r["flow_date"] = fl.get("date", "")


def stage2_supply_rank(pool: list[dict], flows: dict[str, dict], cfg: dict) -> list[dict]:
    attach_flows(pool, flows)
    ranked = sorted((r for r in pool if "supply_net" in r), key=lambda r: r["supply_net"], reverse=True)
    keep = cfg["funnel"]["stage2_keep"]
    log.info("Stage2 수급 필터: %d → %d (수급 데이터 %d건)", len(pool), min(keep, len(ranked)), len(ranked))
    return ranked[:keep]


class IndustryResolver:
    """종목 → (업종 키, 업종명, 업종 구성 종목). 네이버 업종 우선, 실패 시 KIND 업종표."""

    def __init__(self, universe: list[dict]):
        self.universe = {r["code"]: r for r in universe}
        self._members: dict[str, tuple[str, list[dict]]] = {}
        self._kind: dict[str, dict] | None = None
        self.used_fallback = False

    def resolve(self, code: str) -> tuple[str, str, list[dict]] | None:
        try:
            ind_code, _ = naver.industry_of(code)
            if ind_code:
                if ind_code not in self._members:
                    self._members[ind_code] = naver.industry_members(ind_code)
                name, members = self._members[ind_code]
                return f"naver:{ind_code}", name, members
        except Exception as exc:  # noqa: BLE001
            log.warning("네이버 업종 조회 실패 %s: %s — KIND 업종표로 대체", code, redact(exc))
        return self._resolve_kind(code)

    def _resolve_kind(self, code: str) -> tuple[str, str, list[dict]] | None:
        if self._kind is None:
            try:
                self._kind = kind.sector_map()
            except Exception as exc:  # noqa: BLE001
                log.warning("KIND 업종표 조회 실패: %s", redact(exc))
                self._kind = {}
        sector = (self._kind.get(code) or {}).get("sector")
        if not sector:
            return None
        self.used_fallback = True
        members = [r for c, r in self.universe.items() if (self._kind.get(c) or {}).get("sector") == sector]
        return f"kind:{sector}", sector, members


def _related_candidates(
    members: list[dict], leader: dict, used: set[str], excluded: set[str], by_code_150: dict, cfg: dict
) -> list[dict]:
    f = cfg["funnel"]
    out = []
    for m in members:
        code = m["code"]
        if code == leader["code"] or code in used or code in excluded or not is_common_stock(m, cfg):
            continue
        if (m.get("close") or 0) < f["min_price_krw"]:
            continue
        if (m.get("market_cap") or 0) < f["min_market_cap_krw"]:
            continue
        if (m.get("trading_value") or 0) < f["related_min_trading_value_krw"]:
            continue
        out.append(by_code_150.get(code, m))
    # 1차 필터 통과 종목(수급 정보 보유) 우선 → 수급 순매수 → 거래대금
    out.sort(
        key=lambda r: (r["code"] in by_code_150, r.get("supply_net", 0.0), r.get("trading_value") or 0),
        reverse=True,
    )
    return out


def stage3_theme_clusters(
    pool50: list[dict], pool150: list[dict], resolver: IndustryResolver, excluded: set[str], cfg: dict
) -> list[dict]:
    f = cfg["funnel"]
    by_code_150 = {r["code"]: r for r in pool150}
    used: set[str] = set()
    themes: list[dict] = []
    for market in ("KOSPI", "KOSDAQ"):
        # 거래대금 주도주: 2차 수급 통과 종목 우선, 부족하면 1차 통과 종목으로 보충
        in50 = sorted((r for r in pool50 if r["market"] == market), key=lambda r: r["trading_value"], reverse=True)
        in50_codes = {r["code"] for r in in50}
        rest = sorted(
            (r for r in pool150 if r["market"] == market and r["code"] not in in50_codes),
            key=lambda r: r["trading_value"],
            reverse=True,
        )
        industries_used: set[str] = set()
        picked = 0
        for leader in in50 + rest:
            if picked >= f["leaders_per_market"]:
                break
            if leader["code"] in used:
                continue
            resolved = resolver.resolve(leader["code"])
            if not resolved:
                continue
            key, name, members = resolved
            if key in industries_used:
                continue
            related = _related_candidates(members, leader, used, excluded, by_code_150, cfg)[: f["related_per_leader"]]
            if len(related) < f["related_per_leader"]:
                log.info("연관주 부족으로 제외: %s(%s) 업종=%s", leader["name"], leader["code"], name)
                continue
            industries_used.add(key)
            used.add(leader["code"])
            used.update(r["code"] for r in related)
            picked += 1
            themes.append(
                {
                    "theme_id": len(themes) + 1,
                    "market": market,
                    "theme": name,
                    "industry_key": key,
                    "leader": leader,
                    "related": related,
                }
            )
    log.info("Stage3 테마 클러스터: %d개 테마, %d종목", len(themes), len(used))
    return themes


def selected_stocks(themes: list[dict]) -> list[dict]:
    out = []
    for t in themes:
        out.append(t["leader"])
        out.extend(t["related"])
    return out
