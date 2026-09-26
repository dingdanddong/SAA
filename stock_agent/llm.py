"""Gemini REST 클라이언트.

a-Shell 에서는 pydantic·grpc 같은 컴파일 확장이 필요한 google-genai SDK 를 설치할 수 없어
requests 로 generateContent REST 엔드포인트를 직접 호출한다.

모델 단종 대응: config.json 의 llm.models 를 순서대로 시도한다. 한 모델이 404(단종)·403(접근 제한)·
쿼터 초과로 실패하면 다음 모델로 넘어가고, 전부 실패하면 LLMUnavailable → 축소 리포트(degraded).
"""
from __future__ import annotations

import json
import logging
import re

import requests

from . import http

log = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta"


class LLMUnavailable(RuntimeError):
    pass


def _error_detail(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except ValueError:
            msg = resp.text[:200]
        return f"HTTP {resp.status_code} {msg}".strip()
    return http.redact(exc)


def _extract_text(data: dict) -> str:
    feedback = data.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        raise ValueError(f"프롬프트 차단: {feedback['blockReason']}")
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError("응답 후보 없음")
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise ValueError(f"빈 응답 (finishReason={cand.get('finishReason')})")
    return text


def parse_json(text: str):
    """```json 펜스나 앞뒤 잡음이 섞여도 JSON 본문을 꺼낸다."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def generate_json(cfg: dict, system: str, user: str) -> tuple[dict, str]:
    """(파싱된 JSON, 사용한 모델명). 모든 모델 실패 시 LLMUnavailable."""
    key = cfg["secrets"].get("GEMINI_API_KEY")
    if not key:
        raise LLMUnavailable("GEMINI_API_KEY 미설정")
    llm_cfg = cfg["llm"]
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": llm_cfg.get("temperature", 0.3),
            "responseMimeType": "application/json",
        },
    }
    errors = []
    for model in llm_cfg["models"]:
        try:
            resp = http.post(
                f"{API}/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=body,
                timeout=llm_cfg.get("timeout_sec", 120),
                retries=llm_cfg.get("max_retries", 3),
            )
            data = resp.json()
            result = parse_json(_extract_text(data))
            usage = data.get("usageMetadata") or {}
            log.info(
                "Gemini 응답 (%s): 입력 %s · 출력 %s 토큰",
                model,
                usage.get("promptTokenCount"),
                usage.get("candidatesTokenCount"),
            )
            return result, model
        except (requests.RequestException, ValueError, KeyError) as exc:
            detail = _error_detail(exc)
            log.warning("Gemini 모델 %s 실패: %s", model, detail)
            errors.append(f"{model}: {detail}")
            if "API key" in detail:  # 키 자체가 틀리면 다른 모델도 같은 결과
                break
    raise LLMUnavailable(" / ".join(errors))


def list_models(api_key: str) -> list[str]:
    """generateContent 를 지원하는 모델 ID 목록 (설정 점검용)."""
    names: list[str] = []
    page_token = None
    while True:
        params = {"pageSize": 100}
        if page_token:
            params["pageToken"] = page_token
        data = http.get_json(f"{API}/models", headers={"x-goog-api-key": api_key}, params=params)
        for m in data.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                names.append(m["name"].removeprefix("models/"))
        page_token = data.get("nextPageToken")
        if not page_token:
            return names
