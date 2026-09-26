"""data/cache 아래 JSON 파일 캐시 (업종표·기업코드처럼 자주 바뀌지 않는 데이터용)."""
from __future__ import annotations

import json
import logging
import time

from .config import data_path

log = logging.getLogger(__name__)


def load(name: str, max_age_hours: float | None = None):
    path = data_path("cache", f"{name}.json")
    if not path.exists():
        return None
    if max_age_hours is not None and time.time() - path.stat().st_mtime > max_age_hours * 3600:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log.warning("캐시 파일 손상, 무시: %s", path)
        return None


def save(name: str, obj) -> None:
    path = data_path("cache", f"{name}.json")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
