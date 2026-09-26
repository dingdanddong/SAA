"""설정 점검 도구 (1주차 환경 세팅용).

    python3 check_setup.py            # 전체 점검
    python3 check_setup.py --send-test  # Gmail 테스트 메일까지 발송

점검 항목: 파이썬·라이브러리, 비밀 값, 네이버/KIND 접속, DART 키, Gemini 모델 접근, KRX(pykrx), Gmail 로그인.
"""
from __future__ import annotations

import argparse
import smtplib
import ssl
import sys
from datetime import timedelta

from stock_agent import http, llm, mailer
from stock_agent import market_calendar as cal
from stock_agent.config import load_config
from stock_agent.sources import dart, kind, krx, naver

OK, WARN, FAIL = "✅", "⚠️ ", "❌"


def line(mark: str, name: str, detail: str = "") -> None:
    print(f"{mark} {name}" + (f" — {detail}" if detail else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-test", action="store_true", help="테스트 메일 발송")
    args = parser.parse_args(argv)
    cfg = load_config()
    s = cfg["secrets"]
    failures = 0

    print(f"Python {sys.version.split()[0]}")
    for mod in ("pandas", "numpy", "requests", "bs4"):
        try:
            m = __import__(mod)
            line(OK, mod, getattr(m, "__version__", ""))
        except ImportError as exc:
            line(FAIL, mod, str(exc))
            failures += 1

    for key in ("GEMINI_API_KEY", "DART_API_KEY", "GMAIL_USER", "GMAIL_APP_PASSWORD"):
        line(OK if s[key] else WARN, f"비밀 값 {key}", "설정됨" if s[key] else "비어 있음 (secrets.json)")

    try:
        rows = naver.market_listing("KOSPI", min_market_cap_krw=10**15)
        line(OK, "네이버 증권 시세", f"{rows[0]['name']} {rows[0]['close']:,.0f}원")
    except Exception as exc:  # noqa: BLE001
        line(FAIL, "네이버 증권 시세", http.redact(exc))
        failures += 1

    try:
        line(OK, "KIND 관리종목 목록", f"{len(kind.admin_issue_codes())}종목")
    except Exception as exc:  # noqa: BLE001
        line(WARN, "KIND 관리종목 목록", http.redact(exc))

    if s["DART_API_KEY"]:
        try:
            today = cal.today_kst()
            items = dart.list_disclosures(
                s["DART_API_KEY"], bgn_de=cal.ymd(today - timedelta(days=7)), end_de=cal.ymd(today), corp_cls="Y", max_pages=1
            )
            line(OK, "DART Open API", f"최근 7일 KOSPI 공시 {len(items)}건 조회")
        except Exception as exc:  # noqa: BLE001
            line(FAIL, "DART Open API", http.redact(exc))
            failures += 1

    if s["GEMINI_API_KEY"]:
        try:
            available = set(llm.list_models(s["GEMINI_API_KEY"]))
            for model in cfg["llm"]["models"]:
                line(OK if model in available else WARN, f"Gemini 모델 {model}", "사용 가능" if model in available else "목록에 없음")
            flash = sorted(m for m in available if "flash" in m)
            print(f"   사용 가능한 Flash 계열: {', '.join(flash[:12])}")
            _, model = llm.generate_json(cfg, "JSON으로만 답한다.", '{"ping": "pong 을 값으로 하는 JSON을 반환"}')
            line(OK, "Gemini 호출", f"{model} 응답 정상")
        except Exception as exc:  # noqa: BLE001
            line(FAIL, "Gemini", http.redact(exc))
            failures += 1

    ok, reason = krx.available()
    if ok:
        try:
            line(OK, "KRX(pykrx)", f"최근 영업일 {krx.latest_business_day(cal.ymd(cal.today_kst()))}")
        except Exception as exc:  # noqa: BLE001
            line(WARN, "KRX(pykrx)", f"로그인/조회 실패 → 네이버로 자동 대체: {http.redact(exc)}")
    else:
        line(WARN, "KRX(pykrx)", f"{reason} → 네이버 증권 데이터 사용")

    if mailer.configured(cfg):
        try:
            with smtplib.SMTP_SSL(cfg["mail"]["smtp_host"], cfg["mail"]["smtp_port"], context=ssl.create_default_context(), timeout=30) as smtp:
                smtp.login(s["GMAIL_USER"], s["GMAIL_APP_PASSWORD"])
            line(OK, "Gmail SMTP 로그인")
            if args.send_test:
                mailer.send(cfg, f"{cfg['mail']['subject_prefix']} 테스트 메일", "<p>설정 점검 테스트 메일입니다.</p>", "테스트")
                line(OK, "테스트 메일 발송", s["REPORT_TO"])
        except Exception as exc:  # noqa: BLE001
            line(FAIL, "Gmail SMTP", str(exc))
            failures += 1
    else:
        line(WARN, "Gmail", "GMAIL_USER / GMAIL_APP_PASSWORD 미설정")

    print("\n점검 완료" + (f" — 실패 {failures}건" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
