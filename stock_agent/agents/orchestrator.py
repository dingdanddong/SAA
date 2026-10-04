"""Agent 1 — 지휘·총괄 (Orchestrator).

- 모드 결정(08:30 모닝 / 15:40 결산), 휴장일 판단
- 파이프라인 단계 실행과 상태 관리, Agent 2·3 병렬 실행 후 JSON 취합
- HTML 리포트 렌더링·Gmail 발송, 예외 발생 시 관리자에게 로그 첨부 알림
- 각 단계 실패는 가능한 한 축소(degraded) 처리해 완전 무응답을 막는다
"""
from __future__ import annotations

import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from .. import cache, drive, funnel, mailer, report, validation
from .. import market_calendar as cal
from ..config import data_path
from ..http import redact
from ..providers import NaverProvider, providers_for
from ..sources import kind, naver
from .news import NewsAgent
from .quant import QuantAgent

log = logging.getLogger(__name__)

WORLD_INDICES = (".DJI", ".INX", ".IXIC", ".SOX")


class Orchestrator:
    def __init__(self, cfg: dict, *, mode: str, dry_run: bool = False, force: bool = False, use_llm: bool = True, log_buffer=None):
        self.cfg = cfg
        self.mode = mode
        self.dry_run = dry_run
        self.force = force
        self.use_llm = use_llm
        self.log_buffer = log_buffer
        self.holidays = set(cfg.get("market_holidays", []))
        self.news_agent = NewsAgent(cfg)
        self.quant_agent = QuantAgent(cfg)
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.run_name: str | None = None

    # ------------------------------------------------------------------------
    def run(self) -> int:
        log.info("===== %s 파이프라인 시작 (dry_run=%s) =====", self.mode, self.dry_run)
        store = drive.restore(self.cfg)
        code = 0
        try:
            if self.mode == "morning":
                self._run_morning()
            else:
                self._run_evening()
            log.info("===== %s 파이프라인 정상 종료 =====", self.mode)
        except Exception as exc:  # noqa: BLE001
            log.error("파이프라인 실패: %s\n%s", redact(exc), redact(traceback.format_exc()))
            self._alert_admin(exc)
            code = 1
        if store and self.run_name and not self.dry_run:
            drive.save(store, self.run_name, self.mode)
        return code

    # ------------------------------------------------------------------------
    # 15:40 장마감 결산
    # ------------------------------------------------------------------------
    def _run_evening(self) -> None:
        today = cal.today_kst()
        trade_date, universe, provider = self._snapshot(today)
        if trade_date != cal.ymd(today) and not self.force:
            self._send_notice(
                f"휴장일 안내 ({today.isoformat()})",
                [f"오늘은 거래가 없어 결산 리포트를 생략합니다. 마지막 거래일: {trade_date}"],
            )
            return

        excluded = set(self.cfg["funnel"].get("exclude_codes", []))
        try:
            excluded |= kind.admin_issue_codes()
        except Exception as exc:  # noqa: BLE001
            log.warning("KIND 관리종목 조회 실패: %s", redact(exc))
            self.warnings.append("KIND 관리종목 목록 조회 실패 — 관리종목 제외 필터 없이 진행")

        # 선정: 코스피 시총 TOP N + 동일/인접 업종 코스닥 연관주
        resolver = funnel.IndustryResolver(universe)
        themes = funnel.kospi_top_clusters(universe, excluded, resolver, self.cfg)
        if resolver.used_fallback:
            self.notes.append("일부 업종 분류는 KIND 업종표로 대체")
        if not themes:
            raise RuntimeError("선정된 종목이 없음 (업종 조회 전면 실패)")
        if len(themes) < self.cfg["funnel"]["top_leaders"]:
            self.warnings.append(f"연관 코스닥 종목을 찾지 못해 시총 상위 {self.cfg['funnel']['top_leaders'] - len(themes)}개 기업 제외")
        selected = funnel.selected_stocks(themes)
        flows = self._flows(provider, selected, trade_date)
        funnel.attach_flows(selected, flows)
        flow_dates = {r.get("flow_date") for r in selected if r.get("flow_date")}
        flow_date = max(flow_dates) if flow_dates else ""

        # 뉴스·기업 이미지 (Agent 2) ∥ 기술·수급 분석 (Agent 3) — 비동기 병렬
        def news_job() -> dict:
            bundles = self.news_agent.collect(selected, trade_date)
            self.news_agent.rate_image(bundles, selected, self.use_llm)
            return bundles

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_news = pool.submit(news_job)
            fut_quant = pool.submit(self.quant_agent.analyze, selected, flows)
            news = fut_news.result()
            techs = fut_quant.result()
        self._note_source_status()

        # Stage 6: 전일 시나리오 채점 (오늘 가격이 필요하므로 AI 추론 전에 수행)
        try:
            val = validation.evaluate(trade_date, self.quant_agent.history)
        except Exception as exc:  # noqa: BLE001
            log.warning("시나리오 검증 실패: %s", redact(exc))
            val = {"available": False, "rows": [], "stats": {}}

        # Stage 5: AI 심층 추론 (Gemini 1회 호출)
        indices = self._domestic_indices()
        market_ctx = {
            "date": trade_date,
            "indices": [{"name": i["name"], "close": i["close"], "change_pct": i["change_pct"]} for i in indices],
        }
        payloads = self.quant_agent.build_payloads(themes, techs, news)
        forecast = self.quant_agent.forecast(payloads, techs, market_ctx, self.use_llm)
        validation.save_scenarios(trade_date, themes, forecast)
        picks = self.quant_agent.top_picks(selected, techs)

        ctx = {
            "trade_date": trade_date,
            "flow_date": flow_date,
            "provider": provider.name,
            "indices": indices,
            "themes": themes,
            "techs": techs,
            "news": news,
            "forecast": forecast,
            "picks": picks,
            "validation": val,
            "warnings": self.warnings,
            "notes": self.notes,
        }
        self._save_run(trade_date, ctx, payloads)
        self._save_universe(universe, trade_date)
        html, text = report.render_evening(ctx)
        suffix = "" if forecast["mode"] == "ai" else " (축소)"
        self._deliver(f"15:40 결산 {trade_date[4:6]}/{trade_date[6:]}{suffix}", html, text, f"{trade_date}_evening")

    def _snapshot(self, today):
        errors = []
        for provider in providers_for(self.cfg):
            try:
                trade_date, rows = provider.snapshot(today)
                log.info("전 종목 시세: %s %d종목 (기준일 %s)", provider.name, len(rows), trade_date)
                if errors:
                    self.warnings.append(f"1순위 시세 소스 실패로 {provider.name} 사용: {errors[-1]}")
                return trade_date, rows, provider
            except Exception as exc:  # noqa: BLE001
                log.warning("%s 시세 조회 실패: %s", provider.name, redact(exc))
                errors.append(f"{provider.name}: {redact(exc)}")
        raise RuntimeError("모든 시세 소스 실패 — " + " / ".join(errors))

    def _flows(self, provider, pool: list[dict], trade_date: str) -> dict:
        codes = [r["code"] for r in pool]
        try:
            flows = provider.flows(codes, trade_date)
            if flows:
                return flows
            raise RuntimeError("수급 데이터 0건")
        except Exception as exc:  # noqa: BLE001
            if isinstance(provider, NaverProvider):
                log.warning("수급 조회 실패: %s", redact(exc))
                return {}
            log.warning("%s 수급 조회 실패, 네이버로 대체: %s", provider.name, redact(exc))
            self.notes.append("수급 데이터는 네이버 투자자 동향으로 대체")
            return NaverProvider(self.cfg).flows(codes, trade_date)

    def _note_source_status(self) -> None:
        news_status = self.news_agent.status
        if news_status.get("news") == "failed":
            self.warnings.append("뉴스 수집 실패 — 뉴스 없이 분석")
        elif news_status.get("news") == "partial":
            self.notes.append("일부 종목 뉴스 수집 실패")
        dart_status = news_status.get("dart")
        if dart_status == "no_key":
            self.notes.append("DART_API_KEY 미설정 — 공시 생략")
        elif dart_status == "failed":
            self.warnings.append("DART 공시 조회 실패 — 공시 없이 분석")
        if self.quant_agent.status.get("history") == "partial":
            self.notes.append("일부 종목 일봉 조회 실패 — 해당 종목 지표 생략")

    def _domestic_indices(self) -> list[dict]:
        out = []
        for code in ("KOSPI", "KOSDAQ"):
            try:
                out.append(naver.domestic_index(code))
            except Exception as exc:  # noqa: BLE001
                log.warning("%s 지수 조회 실패: %s", code, redact(exc))
        return out

    # ------------------------------------------------------------------------
    # 08:30 모닝 브리프
    # ------------------------------------------------------------------------
    def _run_morning(self) -> None:
        today = cal.today_kst()
        if not cal.is_trading_day(today, self.holidays) and not self.force:
            self._send_notice(f"휴장일 안내 ({today.isoformat()})", ["오늘은 휴장일이라 모닝 브리프를 생략합니다."])
            return
        prev_day = cal.previous_trading_day(today, self.holidays)

        world = []
        for code in WORLD_INDICES:
            try:
                world.append(naver.world_index(code))
            except Exception as exc:  # noqa: BLE001
                log.warning("해외 지수 %s 조회 실패: %s", code, redact(exc))
        if len(world) < len(WORLD_INDICES):
            self.warnings.append("일부 해외 지수 조회 실패")
        fx = None
        try:
            fx = naver.fx_rate("FX_USDKRW")
        except Exception as exc:  # noqa: BLE001
            log.warning("환율 조회 실패: %s", redact(exc))
            self.warnings.append("환율 조회 실패")
        domestic = self._domestic_indices()

        universe = self._load_universe(today)
        disclosures = self.news_agent.morning_disclosures(today, prev_day, universe)
        if self.news_agent.status.get("dart") == "failed":
            self.warnings.append("DART 공시 조회 실패")
        candidates = self._gap_candidates(disclosures, universe)

        payload = {
            "date": cal.ymd(today),
            "us_market": [{"name": w["name"], "close": w["close"], "change_pct": w["change_pct"]} for w in world],
            "usdkrw": {"close": fx["close"], "change_pct": fx["change_pct"]} if fx else None,
            "korea_prev_close": [{"name": d["name"], "close": d["close"], "change_pct": d["change_pct"]} for d in domestic],
            "disclosures": [{"code": d["code"], "name": d["name"], "title": d["title"], "label": d["label"]} for d in disclosures],
            "gap_candidates": [{"code": c["code"], "name": c["name"], "reasons": c["reasons"]} for c in candidates],
        }
        outlook = self.quant_agent.morning_outlook(payload, self.use_llm)

        ctx = {
            "date": cal.ymd(today),
            "world": world,
            "fx": fx,
            "domestic": domestic,
            "disclosures": disclosures,
            "dart_status": self.news_agent.status.get("dart"),
            "gap_candidates": candidates,
            "outlook": outlook,
            "warnings": self.warnings,
        }
        html, text = report.render_morning(ctx)
        suffix = "" if outlook["mode"] == "ai" else " (축소)"
        self._deliver(f"08:30 모닝 브리프 {today:%m/%d}{suffix}", html, text, f"{cal.ymd(today)}_morning")

    def _gap_candidates(self, disclosures: list[dict], universe: dict[str, dict], limit: int = 10) -> list[dict]:
        """갭 상승 관찰 후보: 개장 전 호재 공시 + 전일 결산의 강세·수급 종목."""
        cands: dict[str, dict] = {}

        def add(code: str, name: str, reason: str, score: float) -> None:
            info = universe.get(code, {})
            c = cands.setdefault(
                code,
                {"code": code, "name": name, "close": info.get("close"), "change_pct": info.get("change_pct"), "reasons": [], "score": 0.0},
            )
            if reason not in c["reasons"]:
                c["reasons"].append(reason)
                c["score"] += score

        for d in disclosures:
            if d["sentiment"] > 0:
                add(d["code"], d["name"], f"공시: {d['title']}", 3.0)

        last = self._load_last_run()
        for p in last.get("picks", []):
            add(p["code"], p["name"], f"전일 {p['kind']}", 1.5)
        for th in last.get("themes", []):
            leader = th["leader"]
            t = last.get("techs", {}).get(leader["code"], {})
            if t.get("ma_state") == "정배열" and t.get("both_buy"):
                add(leader["code"], leader["name"], f"전일 주도주({th['theme']}) 정배열·외인기관 동시 순매수", 2.0)
            fc = last.get("forecast", {}).get("themes", {}).get(str(th["theme_id"]), {})
            if fc.get("scenario") == "상승 확산":
                add(fc["focus_code"], fc["focus_name"], f"전일 시나리오 [상승 확산] ({th['theme']})", 1.0)

        ranked = sorted(cands.values(), key=lambda c: c["score"], reverse=True)
        return ranked[:limit]

    # ------------------------------------------------------------------------
    # 저장·발송
    # ------------------------------------------------------------------------
    def _save_run(self, trade_date: str, ctx: dict, payloads: list[dict]) -> None:
        keep = {k: ctx[k] for k in ("trade_date", "flow_date", "provider", "themes", "techs", "news", "forecast", "picks", "warnings", "notes")}
        keep["payloads"] = payloads
        path = data_path("runs", f"{trade_date}_evening.json")
        path.write_text(json.dumps(keep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        log.info("실행 결과 JSON 저장: %s", path)

    def _load_last_run(self) -> dict:
        runs = sorted(data_path("runs", "x").parent.glob("*_evening.json"))
        if not runs:
            self.notes.append("전일 결산 기록 없음")
            return {}
        try:
            return json.loads(runs[-1].read_text(encoding="utf-8"))
        except ValueError:
            return {}

    def _save_universe(self, universe: list[dict], trade_date: str) -> None:
        cache.save(
            "universe_latest",
            {
                "trade_date": trade_date,
                "stocks": {
                    r["code"]: {k: r.get(k) for k in ("name", "market", "close", "change_pct", "market_cap", "trading_value")}
                    for r in universe
                },
            },
        )

    def _load_universe(self, today) -> dict[str, dict]:
        cached = cache.load("universe_latest", max_age_hours=24 * 5)
        if cached:
            return cached["stocks"]
        try:
            _, rows = NaverProvider(self.cfg).snapshot(today - timedelta(days=1))
            return {r["code"]: r for r in rows}
        except Exception as exc:  # noqa: BLE001
            log.warning("종목 목록 조회 실패: %s", redact(exc))
            return {}

    def _deliver(self, title: str, html: str, text: str, name: str) -> None:
        outbox = data_path("outbox", f"{name}.html")
        outbox.write_text(html, encoding="utf-8")
        self.run_name = name
        log.info("리포트 저장: %s", outbox)
        if self.dry_run:
            log.info("dry-run: 메일 발송 생략")
            return
        subject = f"{self.cfg['mail']['subject_prefix']} {title}"
        mailer.send(self.cfg, subject, html, text)

    def _send_notice(self, title: str, lines: list[str]) -> None:
        log.info("%s — %s", title, " ".join(lines))
        if self.dry_run:
            return
        mailer.send(self.cfg, f"{self.cfg['mail']['subject_prefix']} {title}", report.render_notice(title, lines), "\n".join(lines))

    def _alert_admin(self, exc: Exception) -> None:
        """실패 시 관리자 채널로 에러 로그(pipeline.log 상당) 즉시 발송."""
        if self.dry_run or not mailer.configured(self.cfg):
            return
        log_text = redact("\n".join(self.log_buffer)) if self.log_buffer is not None else ""
        try:
            mailer.send(
                self.cfg,
                f"{self.cfg['mail']['subject_prefix']} ❗ {self.mode} 파이프라인 실패",
                report.render_notice(
                    f"{self.mode} 파이프라인 실패",
                    [f"오류: {redact(exc)}", "첨부된 pipeline.log 를 확인하세요. 수동 점검이 필요합니다."],
                    pre=log_text[-4000:],
                ),
                f"파이프라인 실패: {redact(exc)}",
                to=self.cfg["secrets"]["ADMIN_TO"],
                attachments=[("pipeline.log", log_text.encode("utf-8"), "text/plain")],
            )
        except Exception as mail_exc:  # noqa: BLE001
            log.error("에러 알림 메일 발송 실패: %s", redact(mail_exc))
