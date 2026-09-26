"""KRX KIND (상장공시시스템) — 로그인 없이 받을 수 있는 공식 목록.

- 관리종목 목록: 1차 퀀트 필터에서 제외
- 상장법인 업종표: 네이버 업종 조회 실패 시 테마 클러스터링 폴백
"""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from .. import cache, http

log = logging.getLogger(__name__)

KIND = "https://kind.krx.co.kr"


def _rows(html_bytes: bytes) -> list[list[str]]:
    soup = BeautifulSoup(html_bytes.decode("euc-kr", errors="replace"), "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if cells:
            rows.append(cells)
    return rows


def _header(html_bytes: bytes) -> list[str]:
    soup = BeautifulSoup(html_bytes.decode("euc-kr", errors="replace"), "html.parser")
    return [th.get_text(strip=True) for th in soup.find_all("th")]


def admin_issue_codes() -> set[str]:
    """현재 관리종목 종목코드 집합 (하루 캐시)."""
    cached = cache.load("kind_admin_issues", max_age_hours=12)
    if cached is not None:
        return set(cached)
    resp = http.post(
        f"{KIND}/investwarn/adminissue.do",
        data={
            "method": "searchAdminIssueSub",
            "currentPageSize": "3000",
            "pageIndex": "1",
            "orderMode": "1",
            "orderStat": "D",
            "forward": "adminissue_down",
        },
        timeout=30,
    )
    header = _header(resp.content)
    col = header.index("종목코드") if "종목코드" in header else 1
    codes = sorted({r[col] for r in _rows(resp.content) if len(r) > col and len(r[col]) == 6})
    if not codes:
        raise ValueError("KIND 관리종목 목록이 비어 있음 (응답 형식 변경 가능성)")
    cache.save("kind_admin_issues", codes)
    return set(codes)


def sector_map() -> dict[str, dict]:
    """종목코드 → {name, market, sector, products} (1주 캐시)."""
    cached = cache.load("kind_sector_map", max_age_hours=24 * 7)
    if cached is not None:
        return cached
    resp = http.get(
        f"{KIND}/corpgeneral/corpList.do", params={"method": "download", "searchType": "13"}, timeout=60
    )
    header = _header(resp.content)
    idx = {name: header.index(name) for name in ("회사명", "시장구분", "종목코드", "업종", "주요제품") if name in header}
    if "종목코드" not in idx or "업종" not in idx:
        raise ValueError(f"KIND 상장법인목록 헤더 변경: {header}")
    out = {}
    for r in _rows(resp.content):
        if len(r) < len(header):
            continue
        out[r[idx["종목코드"]]] = {
            "name": r[idx.get("회사명", 0)],
            "market": r[idx["시장구분"]] if "시장구분" in idx else "",
            "sector": r[idx["업종"]],
            "products": r[idx["주요제품"]] if "주요제품" in idx else "",
        }
    cache.save("kind_sector_map", out)
    return out
