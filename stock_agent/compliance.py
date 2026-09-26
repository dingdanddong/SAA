"""리포트 문구 컴플라이언스 (계획서 v2 5절).

LLM 이 지시형·권유형 문장을 만들어도 발송 전에 관찰형 표현으로 바꾼다.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

DISCLAIMER = (
    "본 리포트는 공개 데이터 기반 정량·AI 분석 결과이며 투자자문업 등록 서비스가 아닙니다. "
    "특정 종목의 매매를 권유하지 않으며, 모든 투자 결정과 그 결과의 책임은 이용자 본인에게 있습니다."
)
OBSERVATION_NOTE = "※ 투자 권유가 아닌 관찰 결과이며, 최종 투자 판단은 본인 책임"

# (패턴, 대체어) — 순서대로 적용
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(p), r)
    for p, r in [
        (r"(강력\s*|적극\s*)?(매수|매도|매집)\s*(를|을)?\s*(추천|권유|권고)(합니다|드립니다|함)?", "관찰 대상"),
        (r"(매수|매도)\s*(하세요|하십시오|하라|할 것|해야 합니다|해야 함|하시길)", "관찰 필요"),
        # '사라'는 '사라지다'와 겹치므로 넣지 않는다
        (r"(사세요|파세요|담으세요|담아라|팔아라|올라타세요|탑승하세요)", "관찰 필요"),
        (r"비중\s*(확대|축소|늘리|줄이)\w*", "관찰"),
        (r"(추천|권유|권고)(합니다|드립니다|함)", "관찰됨"),
        (r"(강력\s*)?(추천주|추천 종목|유망주)", "관찰 종목"),
        (r"목표\s*주가|목표\s*가격|목표가(?![능치])", "참고 가격대"),
        (r"(반드시|무조건|확실히|틀림없이|100%)\s*", ""),
        (r"수익\s*(보장|확정)", "수익 불확실"),
    ]
]


def sanitize(text: str | None) -> str:
    """지시형 문구를 관찰형으로 치환. 바뀐 경우 로그를 남긴다."""
    if not text:
        return ""
    out = str(text)
    for pattern, repl in _RULES:
        out = pattern.sub(repl, out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    if out != text:
        log.info("컴플라이언스 치환: %r → %r", text[:80], out[:80])
    return out


def sanitize_tree(obj):
    """dict/list 안의 모든 문자열에 sanitize 적용."""
    if isinstance(obj, str):
        return sanitize(obj)
    if isinstance(obj, list):
        return [sanitize_tree(x) for x in obj]
    if isinstance(obj, dict):
        return {k: sanitize_tree(v) for k, v in obj.items()}
    return obj
