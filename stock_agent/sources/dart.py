"""DART Open API (금융감독원 전자공시, 공식 무료 API).

계획서 v2: 공시는 네이버 파싱 대신 DART Open API 를 우선 사용한다.
일일 호출 한도 20,000회. 응답 status '000' 정상, '013' 조회 결과 없음.
"""
from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
import zipfile

from .. import cache, http

log = logging.getLogger(__name__)

API = "https://opendart.fss.or.kr/api"
VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"

# 공시 유형: B 주요사항보고, I 거래소공시 (공급계약·잠정실적 등 주가 영향 공시가 몰려 있음)
MATERIAL_TYPES = ("B", "I")


class DartError(RuntimeError):
    pass


def viewer_url(rcept_no: str) -> str:
    return VIEWER.format(rcept_no)


def _check(payload: dict) -> list[dict]:
    status = payload.get("status")
    if status == "000":
        return payload.get("list") or []
    if status == "013":  # 조회된 데이터 없음
        return []
    raise DartError(f"DART 오류 {status}: {payload.get('message')}")


def corp_codes(api_key: str) -> dict[str, str]:
    """상장사 종목코드(6자리) → DART 고유번호(8자리). 1주 캐시."""
    cached = cache.load("dart_corp_codes", max_age_hours=24 * 7)
    if cached:
        return cached
    resp = http.get(f"{API}/corpCode.xml", params={"crtfc_key": api_key}, timeout=60)
    if not resp.content.startswith(b"PK"):
        # 키 오류 등은 zip 대신 JSON/XML 오류 본문이 온다
        raise DartError(f"corpCode 다운로드 실패: {resp.text[:200]}")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_bytes = zf.read(zf.namelist()[0])
    mapping = {}
    for node in ET.fromstring(xml_bytes).iter("list"):
        stock_code = (node.findtext("stock_code") or "").strip()
        if stock_code:
            mapping[stock_code] = (node.findtext("corp_code") or "").strip()
    cache.save("dart_corp_codes", mapping)
    return mapping


def list_disclosures(
    api_key: str,
    *,
    bgn_de: str,
    end_de: str,
    corp_code: str | None = None,
    corp_cls: str | None = None,
    pblntf_ty: str | None = None,
    max_pages: int = 5,
) -> list[dict]:
    """공시 검색. corp_code 없이 부르면 시장 전체 (기간 3개월 이내)."""
    params = {"crtfc_key": api_key, "bgn_de": bgn_de, "end_de": end_de, "page_count": 100}
    if corp_code:
        params["corp_code"] = corp_code
    if corp_cls:
        params["corp_cls"] = corp_cls
    if pblntf_ty:
        params["pblntf_ty"] = pblntf_ty
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        params["page_no"] = page
        payload = http.get_json(f"{API}/list.json", params=params)
        items = _check(payload)
        out.extend(items)
        if page >= int(payload.get("total_page") or 1):
            break
    for item in out:
        item["url"] = viewer_url(item.get("rcept_no", ""))
        item["report_nm"] = " ".join((item.get("report_nm") or "").split())
    return out


def company_disclosures(api_key: str, stock_code: str, bgn_de: str, end_de: str) -> list[dict]:
    corp_code = corp_codes(api_key).get(stock_code)
    if not corp_code:
        return []
    return list_disclosures(api_key, bgn_de=bgn_de, end_de=end_de, corp_code=corp_code, max_pages=1)


def market_disclosures(api_key: str, bgn_de: str, end_de: str, max_pages: int = 5) -> list[dict]:
    """KOSPI(Y)·KOSDAQ(K) 상장사의 주요사항·거래소 공시 전체."""
    out: list[dict] = []
    for corp_cls in ("Y", "K"):
        for ty in MATERIAL_TYPES:
            out.extend(
                list_disclosures(
                    api_key, bgn_de=bgn_de, end_de=end_de, corp_cls=corp_cls, pblntf_ty=ty, max_pages=max_pages
                )
            )
    return out
