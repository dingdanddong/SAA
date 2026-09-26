"""메인 파이썬 엔진 — 단축어 → run_stock.sh → 여기.

사용 예:
    python3 main_pipeline.py                  # 시각으로 모드 자동 판단 (12시 전 모닝, 이후 결산)
    python3 main_pipeline.py --mode evening   # 15:40 결산 리포트
    python3 main_pipeline.py --mode morning   # 08:30 모닝 브리프
    python3 main_pipeline.py --mode evening --dry-run --force --no-llm   # 개발용: 메일 없이 data/outbox 에 HTML 저장
"""
from __future__ import annotations

import argparse
import sys

from stock_agent import logging_setup
from stock_agent import market_calendar as cal
from stock_agent.agents.orchestrator import Orchestrator
from stock_agent.config import load_config


def resolve_mode(mode: str, cfg: dict) -> str:
    if mode != "auto":
        return mode
    return "morning" if cal.now_kst().hour < cfg["schedule"]["morning_cutoff_hour"] else "evening"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="주식 트렌드 분석 멀티 에이전트 파이프라인")
    parser.add_argument("--mode", choices=["auto", "morning", "evening"], default="auto")
    parser.add_argument("--dry-run", action="store_true", help="메일을 보내지 않고 data/outbox 에 HTML 만 저장")
    parser.add_argument("--force", action="store_true", help="휴장일에도 실행 (마지막 거래일 데이터 사용)")
    parser.add_argument("--no-llm", action="store_true", help="Gemini 호출 없이 축소 리포트로 실행")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    buffer = logging_setup.setup(args.verbose)
    cfg = load_config()
    mode = resolve_mode(args.mode, cfg)
    orchestrator = Orchestrator(
        cfg, mode=mode, dry_run=args.dry_run, force=args.force, use_llm=not args.no_llm, log_buffer=buffer.lines
    )
    return orchestrator.run()


if __name__ == "__main__":
    sys.exit(main())
