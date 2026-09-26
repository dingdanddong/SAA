"""공용 HTTP 클라이언트.

모든 외부 호출은 여기를 거쳐 계획서 v2의 '3회 재시도' 규칙을 따른다.
- 네트워크 오류, 429, 5xx → 지수 백오프 후 재시도
- 그 외 4xx → 재시도해도 결과가 같으므로 FatalHTTPError 로 즉시 실패
"""
from __future__ import annotations

import logging
import re
import threading
import time

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
DEFAULT_RETRIES = 3
DEFAULT_TIMEOUT = 15

_local = threading.local()
_SECRET_PARAM = re.compile(r"(crtfc_key|key)=[^&\s'\"]+")


class FatalHTTPError(requests.HTTPError):
    """재시도해도 결과가 바뀌지 않는 4xx 응답."""


def redact(text: object) -> str:
    """로그/메일에 API 키가 남지 않도록 쿼리 파라미터를 가린다."""
    return _SECRET_PARAM.sub(r"\1=***", str(text))


def _session() -> requests.Session:
    # requests.Session 은 스레드 간 공유가 보장되지 않으므로 스레드별로 둔다.
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"})
        _local.session = session
    return session


def request(
    method: str,
    url: str,
    *,
    retries: int = DEFAULT_RETRIES,
    backoff: float = 2.0,
    timeout: float = DEFAULT_TIMEOUT,
    **kwargs,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = _session().request(method, url, timeout=timeout, **kwargs)
            status = resp.status_code
            if status == 429 or status >= 500:
                raise requests.HTTPError(f"HTTP {status}: {url}", response=resp)
            if status >= 400:
                raise FatalHTTPError(f"HTTP {status}: {url}", response=resp)
            return resp
        except FatalHTTPError:
            raise
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                wait = backoff ** (attempt - 1)
                log.warning("요청 실패 (%d/%d) %s — %.0fs 후 재시도: %s", attempt, retries, url, wait, redact(exc))
                time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def get(url: str, **kwargs) -> requests.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs) -> requests.Response:
    return request("POST", url, **kwargs)


def get_json(url: str, **kwargs):
    return get(url, **kwargs).json()


def retry_call(fn, *args, retries: int = DEFAULT_RETRIES, backoff: float = 2.0, label: str = "", **kwargs):
    """HTTP 이외의 호출(pykrx, SMTP 등)에 같은 재시도 규칙을 적용한다."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — 외부 라이브러리 예외 종류를 알 수 없음
            last_exc = exc
            if attempt < retries:
                wait = backoff ** (attempt - 1)
                log.warning("%s 실패 (%d/%d) — %.0fs 후 재시도: %s", label or fn.__name__, attempt, retries, wait, redact(exc))
                time.sleep(wait)
    assert last_exc is not None
    raise last_exc
