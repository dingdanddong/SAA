"""설정 로더: config.json(공개 설정) + secrets.json/환경변수(비밀 값).

LLM 모델명 등 바뀔 수 있는 값은 코드에 하드코딩하지 않고 config.json 에서 읽는다
(계획서 v2 Fail-Safe: 모델 단종 대응).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

SECRET_KEYS = (
    "GEMINI_API_KEY",
    "DART_API_KEY",
    "GMAIL_USER",
    "GMAIL_APP_PASSWORD",
    "REPORT_TO",
    "ADMIN_TO",
    "KRX_ID",
    "KRX_PW",
)


def load_config(config_path: Path | None = None, secrets_path: Path | None = None) -> dict:
    config_path = config_path or ROOT / "config.json"
    secrets_path = secrets_path or ROOT / "secrets.json"
    cfg = json.loads(config_path.read_text(encoding="utf-8"))

    file_secrets = {}
    if secrets_path.exists():
        file_secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
    # 환경변수가 secrets.json 보다 우선
    secrets = {k: str(os.environ.get(k) or file_secrets.get(k) or "").strip() for k in SECRET_KEYS}
    secrets["GMAIL_APP_PASSWORD"] = secrets["GMAIL_APP_PASSWORD"].replace(" ", "")
    if not secrets["REPORT_TO"]:
        secrets["REPORT_TO"] = secrets["GMAIL_USER"]
    if not secrets["ADMIN_TO"]:
        secrets["ADMIN_TO"] = secrets["REPORT_TO"]
    cfg["secrets"] = secrets

    # pykrx 는 KRX_ID/KRX_PW 를 환경변수에서 읽는다.
    for k in ("KRX_ID", "KRX_PW"):
        if secrets[k]:
            os.environ.setdefault(k, secrets[k])
    return cfg


def data_path(*parts: str) -> Path:
    """data/ 아래 경로를 돌려주고 상위 폴더를 만든다."""
    path = DATA_DIR.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
