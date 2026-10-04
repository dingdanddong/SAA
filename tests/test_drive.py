import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stock_agent import drive


class Resp:
    def __init__(self, body=None, content=b""):
        self._body, self.content = body or {}, content

    def json(self):
        return self._body


class FakeHttp:
    """메모리 안의 가짜 Google Drive (폴더 찾기·생성, 이름 기준 덮어쓰기, 다운로드)."""

    def __init__(self):
        self.files = {}  # id -> {name, parents, mime, data}

    def post(self, url, **kw):
        if "oauth2" in url:
            return Resp({"access_token": "token"})
        fid = f"id{len(self.files) + 1}"
        meta = kw["json"]
        self.files[fid] = {"name": meta["name"], "parents": meta.get("parents"), "mime": meta.get("mimeType"), "data": b""}
        return Resp({"id": fid})

    def get_json(self, url, **kw):
        q = kw["params"]["q"]
        name = re.search(r"name = '([^']+)'", q).group(1)
        parent = re.search(r"'([^']+)' in parents", q)
        hits = [
            i for i, f in self.files.items()
            if f["name"] == name and (f["parents"] == [parent.group(1)] if parent else f["mime"])
        ]
        return {"files": [{"id": i} for i in hits[:1]]}

    def request(self, method, url, **kw):
        self.files[url.rsplit("/", 1)[1]]["data"] = kw["data"]
        return Resp()

    def get(self, url, **kw):
        return Resp(content=self.files[url.rsplit("/", 1)[1]]["data"])

    def redact(self, exc):
        return str(exc)


class DriveTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {"secrets": {k: "x" for k in drive.KEYS}}
        self.fake = FakeHttp()
        self.tmp = Path(tempfile.mkdtemp())
        for p in (
            "outbox/20261001_evening.html", "runs/20261001_evening.json",
            "scenarios/20261001.json", "accuracy_log.csv", "cache/universe_latest.json",
        ):
            (self.tmp / p).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / p).write_text(p, encoding="utf-8")
        patches = [mock.patch.object(drive, "http", self.fake), mock.patch.object(drive, "DATA_DIR", self.tmp)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def names(self):
        return sorted(f["name"] for f in self.fake.files.values() if f["parents"])

    def test_save_overwrite_and_restore(self):
        store = drive.restore(self.cfg)  # 첫 실행: 복원할 것은 없고 SAA 폴더만 만들어진다
        self.assertIsNotNone(store)
        drive.save(store, "20261001_evening", "evening")
        self.assertEqual(
            self.names(),
            ["20261001_evening.html", "20261001_evening.json", "20261001_scenarios.json", "accuracy_log.csv", "saa_state.zip"],
        )
        drive.save(store, "20261001_evening", "evening")  # 같은 이름은 덮어써서 개수가 늘지 않는다
        self.assertEqual(len(self.names()), 5)

        shutil.rmtree(self.tmp)  # 새 러너: data/ 가 비어 있는 상태에서 상태를 복원
        self.tmp.mkdir()
        drive.restore(self.cfg)
        self.assertEqual((self.tmp / "accuracy_log.csv").read_text(encoding="utf-8"), "accuracy_log.csv")
        self.assertTrue((self.tmp / "runs/20261001_evening.json").exists())
        self.assertFalse((self.tmp / "cache").exists())  # 캐시·outbox 는 상태에 포함하지 않는다

    def test_morning_uploads_only_report(self):
        drive.save(drive.restore(self.cfg), "20261001_morning", "morning")
        self.assertEqual(self.names(), [])  # morning HTML 이 없으면 아무것도 올리지 않는다
        (self.tmp / "outbox/20261001_morning.html").write_text("m", encoding="utf-8")
        drive.save(drive.restore(self.cfg), "20261001_morning", "morning")
        self.assertEqual(self.names(), ["20261001_morning.html"])

    def test_not_configured_or_failed_restore_skips_drive(self):
        self.assertIsNone(drive.restore({"secrets": {k: "" for k in drive.KEYS}}))
        with mock.patch.object(self.fake, "post", side_effect=RuntimeError("network")):
            self.assertIsNone(drive.restore(self.cfg))  # 복원 실패 → None → 저장하지 않아 기록을 덮어쓰지 않음


if __name__ == "__main__":
    unittest.main()
