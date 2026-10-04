"""종목 선정 (0 토큰, 순수 Python).

코스피 시총 상위 N개(보통주, 시총 순) → 각 종목의 업종에서 시총 상위 코스닥 종목 2개를 연관주로 붙인다.
코스닥 종목이 부족한 업종은 config.json funnel.related_industries 에 적은 업종 코드 순서대로 찾는다.
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


class IndustryResolver:
    """종목 → (업종 키, 업종명, 업종 구성 종목). 네이버 업종 우선, 실패 시 KIND 업종표."""

    def __init__(self, universe: list[dict]):
        self.universe = {r["code"]: r for r in universe}
        self._members: dict[str, tuple[str, list[dict]]] = {}
        self._kind: dict[str, dict] | None = None
        self.used_fallback = False

    def industry(self, ind_code: str) -> tuple[str, list[dict]]:
        """(업종명, 구성 종목). 같은 업종은 한 번만 조회한다."""
        if ind_code not in self._members:
            self._members[ind_code] = naver.industry_members(ind_code)
        return self._members[ind_code]

    def resolve(self, code: str) -> tuple[str, str, list[dict]] | None:
        try:
            ind_code, _ = naver.industry_of(code)
            if ind_code:
                name, members = self.industry(ind_code)
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


def _eligible(m: dict, used: set[str], excluded: set[str], cfg: dict) -> bool:
    f = cfg["funnel"]
    return (
        m["market"] == "KOSDAQ"
        and m["code"] not in used
        and m["code"] not in excluded
        and is_common_stock(m, cfg)
        and (m.get("close") or 0) >= f["min_price_krw"]
        and (m.get("market_cap") or 0) >= f["min_market_cap_krw"]
        and (m.get("trading_value") or 0) >= f["related_min_trading_value_krw"]
    )


def _kosdaq_related(
    ind_key: str, resolver: IndustryResolver, used: set[str], excluded: set[str], by_code: dict, cfg: dict
) -> list[dict]:
    """동일 업종(없으면 인접 업종) 코스닥 종목을 시총 순으로 연관주 수만큼."""
    f = cfg["funnel"]
    own = ind_key.split(":", 1)[1] if ind_key.startswith("naver:") else None
    searches = f.get("related_industries", {}).get(own, [own]) if own else []
    out: list[dict] = []
    for ind_code in searches:
        name, members = resolver.industry(ind_code)
        relation = f"{'동일' if ind_code == own else '인접'} 업종({name})"
        rows = [by_code.get(m["code"], m) for m in members]
        rows = sorted((r for r in rows if _eligible(r, used, excluded, cfg)), key=lambda r: r["market_cap"], reverse=True)
        out += [{**r, "relation": relation} for r in rows]
    return out[: f["related_per_leader"]]


def _cap_ranks(universe: list[dict]) -> dict[str, int]:
    """시장별 보통주 시총 순위 (우선주·ETF·ETN 제외). 목록이 시총 하한 이상을 모두 담으므로 순위가 정확하다."""
    ranks: dict[str, int] = {}
    for market in ("KOSPI", "KOSDAQ"):
        rows = sorted(
            (r for r in universe if r["market"] == market and r.get("end_type", "stock") == "stock" and r["code"].endswith("0")),
            key=lambda r: r.get("market_cap") or 0,
            reverse=True,
        )
        ranks.update({r["code"]: i for i, r in enumerate(rows, 1)})
    return ranks


def kospi_top_clusters(universe: list[dict], excluded: set[str], resolver: IndustryResolver, cfg: dict) -> list[dict]:
    f = cfg["funnel"]
    by_code = {r["code"]: r for r in universe}
    leaders = sorted(
        (r for r in universe if r["market"] == "KOSPI" and r["code"] not in excluded and is_common_stock(r, cfg)),
        key=lambda r: r.get("market_cap") or 0,
        reverse=True,
    )[: f["top_leaders"]]
    used = {r["code"] for r in leaders}
    themes: list[dict] = []
    for rank, leader in enumerate(leaders, 1):
        resolved = resolver.resolve(leader["code"])
        if not resolved:
            log.warning("업종 조회 실패로 제외: %s(%s)", leader["name"], leader["code"])
            continue
        key, name, _ = resolved
        related = _kosdaq_related(key, resolver, used, excluded, by_code, cfg)
        if len(related) < f["related_per_leader"]:
            log.warning("연관 코스닥 종목 부족으로 제외: %s(%s) 업종=%s", leader["name"], leader["code"], name)
            continue
        used.update(r["code"] for r in related)
        themes.append(
            {"theme_id": rank, "market": "KOSPI", "theme": name, "industry_key": key, "leader": leader, "related": related}
        )
    ranks = _cap_ranks(universe)
    for stock in selected_stocks(themes):
        stock["cap_rank"] = ranks.get(stock["code"])
    log.info("코스피 시총 TOP %d: %d개 클러스터, %d종목", f["top_leaders"], len(themes), len(used))
    return themes


def selected_stocks(themes: list[dict]) -> list[dict]:
    out = []
    for t in themes:
        out.append(t["leader"])
        out.extend(t["related"])
    return out
