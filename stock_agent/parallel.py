"""스레드 풀 기반 병렬 수집 (I/O 대기 위주라 스레드로 충분).

a-Shell 에서 스레드 문제가 생기면 config.json 의 data.max_workers 를 1로 두면 순차 실행된다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, TypeVar

from .http import redact

log = logging.getLogger(__name__)

K = TypeVar("K")
V = TypeVar("V")


def pmap(fn: Callable[[K], V], keys: Iterable[K], max_workers: int = 4, label: str = "") -> tuple[dict, dict]:
    """(성공 결과 {key: value}, 실패 {key: 예외}) — 일부 실패해도 나머지는 살린다."""
    keys = list(dict.fromkeys(keys))
    ok: dict = {}
    failed: dict = {}
    if max_workers <= 1:
        for key in keys:
            try:
                ok[key] = fn(key)
            except Exception as exc:  # noqa: BLE001
                failed[key] = exc
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {key: pool.submit(fn, key) for key in keys}
            for key, fut in futures.items():
                try:
                    ok[key] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    failed[key] = exc
    if failed:
        sample = ", ".join(f"{k}: {redact(v)}" for k, v in list(failed.items())[:3])
        log.warning("%s 실패 %d/%d건 (예: %s)", label or fn.__name__, len(failed), len(keys), sample)
    return ok, failed
