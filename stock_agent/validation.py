"""Stage 6 — 결과 검증: 전일 시나리오 적중 여부 자동 태깅 (LLM 토큰 0).

결산 실행마다 테마별 지목 종목의 시나리오(방향·예상 밴드·핵심 가격)를 data/scenarios/YYYYMMDD.json 에 저장하고,
다음 결산 실행에서 실제 일봉과 비교해 data/accuracy_log.csv 에 누적한다.

판정 기준
- 방향: 상승 확산 → 종가 상승, 조정 → 종가 하락, 박스권 → 등락률 ±2% 이내
- 밴드: band_low ≤ 종가 ≤ band_high
- 핵심 가격: 기준 종가 이하면 지지선(당일 저가가 지켰는지), 이상이면 저항선(종가가 넘었는지)
"""
from __future__ import annotations

import csv
import json
import logging
from typing import Callable

import pandas as pd

from .config import DATA_DIR, data_path

log = logging.getLogger(__name__)

SCENARIO_DIRECTION = {"상승 확산": 1, "박스권": 0, "조정": -1}
FLAT_BAND_PCT = 2.0
LOG_FIELDS = [
    "base_date", "eval_date", "market", "theme", "code", "name", "scenario", "source",
    "base_close", "close", "return_pct", "band_low", "band_high", "band_hit",
    "direction_hit", "key_level", "key_level_result",
]


def save_scenarios(trade_date: str, themes: list[dict], forecast: dict) -> int:
    records = []
    for th in themes:
        f = forecast["themes"].get(th["theme_id"])
        if not f or not (f.get("band_low") and f.get("band_high") and f.get("base_close")):
            continue
        records.append(
            {
                "base_date": trade_date,
                "market": th["market"],
                "theme": th["theme"],
                "code": f["focus_code"],
                "name": f["focus_name"],
                "scenario": f["scenario"],
                "source": f["source"],
                "base_close": f["base_close"],
                "band_low": f["band_low"],
                "band_high": f["band_high"],
                "key_level": f.get("key_level"),
            }
        )
    path = data_path("scenarios", f"{trade_date}.json")
    path.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("시나리오 %d건 저장: %s", len(records), path.name)
    return len(records)


def judge(rec: dict, bar: dict) -> dict:
    """시나리오 1건 + 다음 거래일 일봉 1개 → 판정 행."""
    base = float(rec["base_close"])
    close, low = float(bar["close"]), float(bar["low"])
    ret = (close / base - 1) * 100
    direction = SCENARIO_DIRECTION.get(rec["scenario"], 0)
    if direction > 0:
        direction_hit = ret > 0
    elif direction < 0:
        direction_hit = ret < 0
    else:
        direction_hit = abs(ret) <= FLAT_BAND_PCT
    band_hit = float(rec["band_low"]) <= close <= float(rec["band_high"])
    key = rec.get("key_level")
    if not key:
        key_result = "-"
    elif float(key) <= base:
        key_result = "지지 유지" if low >= float(key) else "지지 이탈"
    else:
        key_result = "저항 돌파" if close >= float(key) else "저항 미돌파"
    return {
        **{k: rec.get(k, "") for k in ("base_date", "market", "theme", "code", "name", "scenario", "source")},
        "eval_date": str(bar["date"]),
        "base_close": base,
        "close": close,
        "return_pct": round(ret, 2),
        "band_low": rec["band_low"],
        "band_high": rec["band_high"],
        "band_hit": int(band_hit),
        "direction_hit": int(direction_hit),
        "key_level": key or "",
        "key_level_result": key_result,
    }


def _read_log() -> list[dict]:
    path = DATA_DIR / "accuracy_log.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fp:
        return list(csv.DictReader(fp))


def _append_log(rows: list[dict]) -> None:
    path = data_path("accuracy_log.csv")
    new = not path.exists()
    with path.open("a", encoding="utf-8-sig" if new else "utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=LOG_FIELDS)
        if new:
            writer.writeheader()
        writer.writerows(rows)


def stats(rows: list[dict], recent_days: int = 20) -> dict:
    def rate(items: list[dict], field: str) -> float | None:
        return round(sum(int(r[field]) for r in items) / len(items) * 100, 1) if items else None

    base_dates = sorted({r["base_date"] for r in rows})
    recent = [r for r in rows if r["base_date"] in set(base_dates[-recent_days:])]
    by_source = {}
    for src in ("ai", "quant"):
        subset = [r for r in rows if r["source"] == src]
        if subset:
            by_source[src] = {"count": len(subset), "direction": rate(subset, "direction_hit"), "band": rate(subset, "band_hit")}
    return {
        "count": len(rows),
        "days": len(base_dates),
        "direction": rate(rows, "direction_hit"),
        "band": rate(rows, "band_hit"),
        "recent_days": min(recent_days, len(base_dates)),
        "recent_direction": rate(recent, "direction_hit"),
        "recent_band": rate(recent, "band_hit"),
        "by_source": by_source,
    }


def evaluate(trade_date: str, history: Callable[[str], pd.DataFrame]) -> dict:
    """trade_date 이전 가장 최근 시나리오 파일을 채점하고 누적 통계를 돌려준다."""
    scen_dir = DATA_DIR / "scenarios"
    files = sorted(p for p in scen_dir.glob("*.json") if p.stem < trade_date) if scen_dir.exists() else []
    if not files:
        return {"available": False, "rows": [], "stats": stats(_read_log())}

    base_date = files[-1].stem
    existing = _read_log()
    done = [r for r in existing if r["base_date"] == base_date]
    if done:  # 같은 날 재실행 시 중복 기록 방지
        return {"available": True, "base_date": base_date, "rows": done, "stats": stats(existing)}

    records = json.loads(files[-1].read_text(encoding="utf-8"))
    rows = []
    for rec in records:
        try:
            df = history(rec["code"])
        except Exception as exc:  # noqa: BLE001
            log.warning("검증용 일봉 조회 실패 %s: %s", rec["code"], exc)
            continue
        after = df[(df["date"].astype(str) > base_date) & (df["date"].astype(str) <= trade_date)]
        if after.empty:
            continue
        rows.append(judge(rec, after.iloc[0].to_dict()))
    if rows:
        _append_log(rows)
    log.info("Stage6 시나리오 검증: %s 기준 %d/%d건 채점", base_date, len(rows), len(records))
    return {"available": True, "base_date": base_date, "rows": rows, "stats": stats(existing + rows)}
