"""하트비트 확인 메일 (계획서 v2 [보완]).

스크립트 시작 시 1줄 확인 메일을 보낸다. 지정 시각까지 하트비트만 오고 결과 메일이 없으면
파이프라인이 도중에 멈춘 것이고, 하트비트조차 없으면 단축어 자동화 자체가 실행되지 않은 것이다.

    python3 heartbeat.py --stage start [--mode morning|evening]
"""
from __future__ import annotations

import argparse
import logging
import sys

from stock_agent import logging_setup, mailer
from stock_agent import market_calendar as cal
from stock_agent.config import load_config
from stock_agent.report import render_notice

EXPECTED = {"morning": "08:45", "evening": "16:00"}
LABEL = {"morning": "08:30 모닝 브리프", "evening": "15:40 결산"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="실행 시작 하트비트 메일")
    parser.add_argument("--stage", default="start")
    parser.add_argument("--mode", choices=["auto", "morning", "evening"], default="auto")
    args = parser.parse_args(argv)

    logging_setup.setup()
    cfg = load_config()
    now = cal.now_kst()
    mode = args.mode
    if mode == "auto":
        mode = "morning" if now.hour < cfg["schedule"]["morning_cutoff_hour"] else "evening"

    line = f"💓 {LABEL[mode]} 실행 {args.stage} · {now:%Y-%m-%d %H:%M:%S} KST"
    try:
        mailer.send(
            cfg,
            f"{cfg['mail']['subject_prefix']} 💓 하트비트 {now:%m/%d %H:%M} {LABEL[mode]}",
            render_notice(
                "하트비트",
                [line, f"{EXPECTED[mode]}까지 결과 메일이 오지 않으면 iPad a-Shell 과 pipeline.log 를 점검하세요."],
            ),
            line,
            to=cfg["secrets"]["ADMIN_TO"],
        )
    except Exception as exc:  # noqa: BLE001 — 하트비트 실패가 본 파이프라인을 막으면 안 됨
        logging.getLogger("heartbeat").error("하트비트 발송 실패: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
