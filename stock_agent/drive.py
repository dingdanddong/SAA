"""Google Drive 저장 (내 계정 OAuth refresh token, drive.file 범위).

drive.file 범위는 앱이 만든 파일·폴더만 볼 수 있어서 'SAA' 폴더를 앱이 직접 만든다.
- 실행 결과·뉴스(runs JSON)·시나리오·리포트 HTML·적중 기록을 폴더에 올린다.
- data/ 상태(runs, scenarios, accuracy_log.csv)는 압축본 하나로 올려 다음 실행 시작 때 복원한다
  (Actions 러너는 매번 새로 시작하므로 기록이 이어지게 하는 용도).
저장·복원 실패는 로그만 남기고 리포트 발송을 막지 않는다.
"""
from __future__ import annotations

import io
import logging
import zipfile

from . import http
from .config import DATA_DIR

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FOLDER = "SAA"
STATE_ZIP = "saa_state.zip"
STATE_PATHS = ("runs", "scenarios", "accuracy_log.csv")
KEYS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")


def configured(cfg: dict) -> bool:
    return all(cfg["secrets"].get(k) for k in KEYS)


class Drive:
    def __init__(self, cfg: dict):
        s = cfg["secrets"]
        resp = http.post(
            TOKEN_URL,
            data={
                "client_id": s["GOOGLE_CLIENT_ID"],
                "client_secret": s["GOOGLE_CLIENT_SECRET"],
                "refresh_token": s["GOOGLE_REFRESH_TOKEN"],
                "grant_type": "refresh_token",
            },
        )
        self._h = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        self.folder = self._find(FOLDER) or http.post(
            f"{API}/files", headers=self._h, json={"name": FOLDER, "mimeType": "application/vnd.google-apps.folder"}
        ).json()["id"]

    def _find(self, name: str, parent: str | None = None) -> str | None:
        # 파일명은 숫자·영문·밑줄·점뿐이라 따옴표 이스케이프가 필요 없다
        q = f"name = '{name}' and trashed = false and " + (
            f"'{parent}' in parents" if parent else "mimeType = 'application/vnd.google-apps.folder'"
        )
        data = http.get_json(f"{API}/files", headers=self._h, params={"q": q, "fields": "files(id)", "pageSize": 1})
        return (data.get("files") or [{}])[0].get("id")

    def put(self, name: str, data: bytes, mime: str) -> None:
        """같은 이름이 있으면 덮어쓰고 없으면 새로 만든다."""
        fid = self._find(name, self.folder) or http.post(
            f"{API}/files", headers=self._h, json={"name": name, "parents": [self.folder]}
        ).json()["id"]
        http.request(
            "PATCH", f"{UPLOAD}/files/{fid}", headers={**self._h, "Content-Type": mime},
            params={"uploadType": "media"}, data=data, timeout=60,
        )

    def get(self, name: str) -> bytes | None:
        fid = self._find(name, self.folder)
        if not fid:
            return None
        return http.get(f"{API}/files/{fid}", headers=self._h, params={"alt": "media"}, timeout=60).content


def pack_state() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(DATA_DIR.rglob("*")):
            rel = p.relative_to(DATA_DIR)
            if p.is_file() and rel.parts[0] in STATE_PATHS:
                z.write(p, rel)
    return buf.getvalue()


def restore(cfg: dict) -> Drive | None:
    """Drive 에 저장된 상태를 data/ 로 내려받는다. 미설정·실패면 None (None 이면 저장도 하지 않아 기록을 덮어쓰지 않는다)."""
    if not configured(cfg):
        return None
    try:
        drive = Drive(cfg)
        blob = drive.get(STATE_ZIP)
        if blob:
            zipfile.ZipFile(io.BytesIO(blob)).extractall(DATA_DIR)
            log.info("Drive 에서 이전 기록 복원")
        return drive
    except Exception as exc:  # noqa: BLE001
        log.warning("Drive 복원 실패 — 이번 실행은 Drive 저장을 건너뜀: %s", http.redact(exc))
        return None


def save(drive: Drive, run_name: str, mode: str) -> None:
    """리포트 HTML, (결산이면) 결과·뉴스 JSON·시나리오·적중 기록·상태 압축본을 올린다."""
    date = run_name.split("_")[0]
    files = [(f"{run_name}.html", DATA_DIR / "outbox" / f"{run_name}.html", "text/html")]
    if mode == "evening":
        files += [
            (f"{date}_evening.json", DATA_DIR / "runs" / f"{date}_evening.json", "application/json"),
            (f"{date}_scenarios.json", DATA_DIR / "scenarios" / f"{date}.json", "application/json"),
            ("accuracy_log.csv", DATA_DIR / "accuracy_log.csv", "text/csv"),
        ]
    try:
        for name, path, mime in files:
            if path.exists():
                drive.put(name, path.read_bytes(), mime)
        if mode == "evening":
            drive.put(STATE_ZIP, pack_state(), "application/zip")
        log.info("Drive 저장 완료: %s", ", ".join(n for n, p, _ in files if p.exists()))
    except Exception as exc:  # noqa: BLE001
        log.warning("Drive 저장 실패: %s", http.redact(exc))
