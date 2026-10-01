"""Gmail HTML 리포트 렌더링 (계획서 5절 Message Format).

Gmail 은 <style> 블록을 일부 무시하므로 모든 스타일을 인라인으로 넣는다.
국내 관례대로 상승은 빨강, 하락은 파랑.
"""
from __future__ import annotations

from html import escape

from . import fmt
from .compliance import DISCLAIMER, OBSERVATION_NOTE

UP, DOWN, MUTED, BORDER, NAVY, WARN_BG, WARN_BORDER = (
    "#d63031", "#1c64c8", "#6b7280", "#e5e7eb", "#1e3a5f", "#fff4e5", "#f0a040",
)
FONT = "-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif"
WEEKDAYS = "월화수목금토일"


def _e(value) -> str:
    return escape("" if value is None else str(value))


def _chg(value: float | None) -> str:
    if value is None or value == 0:
        return f'<span style="color:{MUTED}">-</span>'
    color = UP if value > 0 else DOWN
    return f'<span style="color:{color}">{fmt.arrow(value)}</span>'


def _date_label(ymd: str) -> str:
    from datetime import date

    d = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
    return f"{d.isoformat()} ({WEEKDAYS[d.weekday()]})"


def _section(title: str, body: str) -> str:
    return (
        f'<h3 style="margin:28px 0 10px;padding-bottom:6px;border-bottom:2px solid {NAVY};'
        f'color:{NAVY};font-size:17px">{_e(title)}</h3>{body}'
    )


def _table(headers: list[str], rows: list[list[str]], align: list[str] | None = None) -> str:
    align = align or ["left"] * len(headers)
    th = "".join(
        f'<th style="background:{NAVY};color:#fff;padding:6px 8px;font-weight:600;text-align:{a};'
        f'white-space:nowrap">{_e(h)}</th>'
        for h, a in zip(headers, align)
    )
    body = ""
    for i, row in enumerate(rows):
        bg = "#f8fafc" if i % 2 else "#ffffff"
        body += "<tr>" + "".join(
            f'<td style="padding:6px 8px;border-bottom:1px solid {BORDER};background:{bg};text-align:{a}">{cell}</td>'
            for cell, a in zip(row, align)
        ) + "</tr>"
    return (
        f'<table style="width:100%;border-collapse:collapse;font-size:13px;margin:6px 0">'
        f"<tr>{th}</tr>{body}</table>"
    )


def _banner(text: str) -> str:
    return (
        f'<div style="background:{WARN_BG};border:1px solid {WARN_BORDER};border-radius:6px;'
        f'padding:10px 12px;margin:12px 0;font-size:13px">{text}</div>'
    )


def _document(inner: str) -> str:
    """완결된 HTML 문서 (저장본을 브라우저로 열어도 한글이 깨지지 않도록 charset 명시)."""
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="margin:0;background:#ffffff">{inner}</body></html>'
    )


def _wrap(title: str, subtitle: str, body: str) -> str:
    return _document(
        f'<div style="max-width:760px;margin:0 auto;padding:8px 12px;font-family:{FONT};'
        f'color:#1f2937;line-height:1.6;font-size:14px">'
        f'<h2 style="margin:8px 0 2px;font-size:20px;color:{NAVY}">{title}</h2>'
        f'<div style="color:{MUTED};font-size:12px">{subtitle}</div>'
        f"{body}"
        f'<p style="margin-top:28px;padding-top:10px;border-top:1px solid {BORDER};color:{MUTED};font-size:11px">'
        f"{_e(DISCLAIMER)}</p></div>"
    )


def _news_links(bundle: dict | None) -> str:
    if not bundle:
        return ""
    items = []
    for h in bundle.get("headlines", []):
        items.append(
            f'<a href="{_e(h["url"])}" style="color:#374151">{_e(h["title"])}</a>'
            f' <span style="color:{MUTED}">({_e(h["source"])})</span>'
        )
    for d in bundle.get("disclosures", []):
        items.append(
            f'<a href="{_e(d["url"])}" style="color:#374151">[공시] {_e(d["title"])}</a>'
        )
    return "<br>".join(items)


def _image_badge(img: dict | None) -> str:
    if not img:
        return ""
    color = {"긍정": UP, "부정": DOWN}.get(img["label"], MUTED)
    return f'<span style="color:{color};font-weight:600">이미지 {_e(img["label"])}</span>'


def _image_counts(img: dict | None) -> str:
    if not img:
        return ""
    if not img["total"]:
        return " · 기사 없음"
    note = f" · 기사 {img['total']}건 (긍정 {img['pos']}·중립 {img['neu']}·부정 {img['neg']})"
    if img["total"] < 3:
        note += " 표본 적음"
    return note + (" 키워드 분류" if img["source"] == "keyword" else "")


def _index_line(indices: list[dict]) -> str:
    return " &nbsp;|&nbsp; ".join(
        f"<b>{_e(i['name'])}</b> {i['close']:,.2f} {_chg(i.get('change_pct'))}" if i.get("close") else f"<b>{_e(i['name'])}</b> -"
        for i in indices
    )


# ============================================================================
# 15:40 장마감 결산 리포트
# ============================================================================
def render_evening(ctx: dict) -> tuple[str, str]:
    forecast = ctx["forecast"]
    body = ""
    if forecast["mode"] != "ai":
        body += _banner(
            "⚠ <b>축소 리포트 (degraded mode)</b> — AI 해석 없이 정량 데이터(가격·수급·지표)만 담았습니다. "
            f"사유: {_e(forecast['reason'])}"
        )
    for note in ctx.get("warnings", []):
        body += _banner(f"⚠ {_e(note)}")

    # 시장 개요
    overview = f"<p>{_index_line(ctx['indices'])}</p>" if ctx.get("indices") else ""
    if forecast.get("market_summary"):
        overview += f"<p>{_e(forecast['market_summary'])}</p>"
    if overview:
        body += _section("시장 개요", overview)

    # 테마 클러스터
    themes_html = ""
    for th in ctx["themes"]:
        themes_html += _theme_card(th, ctx["techs"], ctx["news"], forecast["themes"].get(th["theme_id"], {}))
    body += _section(
        f"코스피 시총 TOP {len(ctx['themes'])} + 연관 코스닥 · {sum(1 + len(t['related']) for t in ctx['themes'])}종목",
        themes_html,
    )

    # 52주 신고가 근접 / 눌림목
    if ctx.get("picks"):
        rows = [
            [
                f"<b>{_e(p['name'])}</b><br><span style='color:{MUTED};font-size:11px'>{_e(p['code'])} · {_e(p.get('market'))}</span>",
                _e(p["kind"]),
                f"{fmt.price(p['close'])}<br>{_chg(p.get('change_pct'))}",
                fmt.pct(p.get("pct_from_high")),
                f"{p['rsi']:.0f}" if p.get("rsi") is not None else "-",
                _e(p.get("supply_text", "")),
                f"{fmt.price(p.get('support'))} / {fmt.price(p.get('stop'))}",
            ]
            for p in ctx["picks"]
        ]
        body += _section(
            "52주 신고가 근접 · 눌림목 포착 Top 5",
            _table(
                ["종목", "유형", "종가", "52주 고점比", "RSI", "수급", "지지 / 손절 참고"],
                rows,
                ["left", "left", "right", "right", "right", "left", "right"],
            ),
        )

    # 전일 시나리오 검증
    body += _section("전일 시나리오 적중 검증", _validation_html(ctx.get("validation") or {}))

    # 데이터 노트
    notes = [
        f"데이터 기준일 {_date_label(ctx['trade_date'])}",
        f"시세·수급 소스: {_e(ctx['provider'])}",
        "기업 이미지는 최근 기사 제목 기준 분류이며 투자 판단 자료가 아님",
    ]
    if ctx.get("flow_date") and ctx["flow_date"] != ctx["trade_date"]:
        notes.append(f"수급 기준일 {_e(ctx['flow_date'])} (당일 확정치 미반영)")
    notes += [_e(n) for n in ctx.get("notes", [])]
    body += f'<p style="color:{MUTED};font-size:12px;margin-top:20px">' + " · ".join(notes) + "</p>"

    model = f" · 분석 모델 {forecast['model']}" if forecast.get("model") else ""
    html = _wrap(
        "📩 [오늘의 주식 트렌드 &amp; 관찰 리포트] - 15:40 결산",
        f"{_date_label(ctx['trade_date'])}{_e(model)}",
        body,
    )
    return html, _evening_text(ctx)


def _theme_card(th: dict, techs: dict, news: dict, fc: dict) -> str:
    leader = th["leader"]
    members = [("대장주", leader)] + [("연관주", r) for r in th["related"]]
    rows = []
    for role, s in members:
        t = techs.get(s["code"], {})
        focus = " 🎯" if fc.get("focus_code") == s["code"] else ""
        img = (news.get(s["code"]) or {}).get("image")
        rel = " · 인접 업종" if str(s.get("relation", "")).startswith("인접") else ""
        rows.append(
            [
                f"<b>{_e(s['name'])}</b>{focus} <span style='color:{MUTED};font-size:12px'>시총 {_e(fmt.cap(s.get('market_cap')))}</span> "
                f"{_image_badge(img)}<br><span style='color:{MUTED};font-size:11px'>{role} · {_e(s['code'])}{rel}{_e(_image_counts(img))}</span>",
                f"{fmt.price(s.get('close'))}<br>{_chg(s.get('change_pct'))}",
                f"{_e(t.get('tech_text', '지표 없음'))}<br><span style='color:{MUTED}'>{_e(t.get('supply_text', ''))}</span>",
                f"{fmt.price(t.get('support'))}<br>{fmt.price(t.get('resistance'))}",
                fmt.price(t.get("stop")),
            ]
        )
    table = _table(
        ["종목", "종가", "지표 · 수급", "지지 / 저항", "손절 참고"],
        rows,
        ["left", "right", "left", "right", "right"],
    )

    lines = []
    if fc.get("leader_view"):
        lines.append(f"<b>대장주 평가</b>: {_e(fc['leader_view'])}")
    if fc.get("observation"):
        lines.append(
            f"<b style='color:{UP}'>관찰 포인트</b>: \"{_e(fc['observation'])}\" "
            f"<span style='color:{MUTED};font-size:12px'>({_e(OBSERVATION_NOTE)})</span>"
        )
    if fc.get("band_low"):
        src = "AI" if fc.get("band_source") == "ai" else "정량 ±1ATR"
        conf = f" · 신뢰도 {_e(fc['confidence'])}" if fc.get("confidence") else ""
        lines.append(
            f"<b>🎯 {_e(fc.get('focus_name'))} 다음 거래일 참고</b>: 시나리오 [{_e(fc['scenario'])}]{conf} · "
            f"예상 밴드 {fmt.price(fc['band_low'])}~{fmt.price(fc['band_high'])}원 ({src}) · "
            f"핵심 가격 {fmt.price(fc.get('key_level'))}원"
        )
    links = "<br>".join(filter(None, (_news_links(news.get(s["code"])) for _, s in members)))
    if links:
        lines.append(f"<span style='font-size:12px'>{links}</span>")

    return (
        f'<div style="border:1px solid {BORDER};border-radius:8px;padding:12px 14px;margin:14px 0">'
        f'<div style="font-weight:700;font-size:15px;margin-bottom:6px">🔥 코스피 시총 {th["theme_id"]}위 · '
        f'{_e(leader["name"])} [{_e(th["theme"])}]</div>'
        f"{table}"
        + "".join(f'<div style="margin:6px 0">{line}</div>' for line in lines)
        + "</div>"
    )


def _validation_html(val: dict) -> str:
    st = val.get("stats") or {}
    if not val.get("available"):
        text = "검증할 전일 시나리오가 없습니다 (첫 실행 또는 기록 없음)."
        return f'<p style="color:{MUTED}">{text}</p>'
    rows = [
        [
            f"<b>{_e(r['name'])}</b><br><span style='color:{MUTED};font-size:11px'>{_e(r['theme'])}</span>",
            f"{_e(r['scenario'])}<br><span style='color:{MUTED};font-size:11px'>{'AI' if r['source'] == 'ai' else '정량'}</span>",
            f"{fmt.price(float(r['close']))}<br>{_chg(float(r['return_pct']))}",
            f"{fmt.price(float(r['band_low']))}~{fmt.price(float(r['band_high']))}",
            "✅" if int(r["direction_hit"]) else "❌",
            "✅" if int(r["band_hit"]) else "❌",
            _e(r["key_level_result"]),
        ]
        for r in val.get("rows", [])
    ]
    html = f"<p>기준일 {_e(val.get('base_date'))} 시나리오 → 다음 거래일 실제 결과</p>"
    html += _table(
        ["종목", "시나리오", "실제 종가", "예상 밴드", "방향", "밴드", "핵심 가격"],
        rows,
        ["left", "left", "right", "right", "center", "center", "left"],
    ) if rows else f'<p style="color:{MUTED}">채점 가능한 종목이 없습니다.</p>'
    if st.get("count"):
        html += (
            f'<p style="font-size:13px">누적 {st["days"]}거래일 {st["count"]}건 · 방향 적중 {st["direction"]}% · '
            f'밴드 적중 {st["band"]}% (최근 {st["recent_days"]}거래일: 방향 {st["recent_direction"]}% · '
            f'밴드 {st["recent_band"]}%)</p>'
        )
    return html


def _evening_text(ctx: dict) -> str:
    lines = [f"[오늘의 주식 트렌드 & 관찰 리포트] - 15:40 결산 ({ctx['trade_date']})", ""]
    fc_all = ctx["forecast"]["themes"]
    if ctx["forecast"]["mode"] != "ai":
        lines.append(f"* 축소 리포트: {ctx['forecast']['reason']}")
    news = ctx.get("news", {})

    def tag(x: dict) -> str:
        img = (news.get(x["code"]) or {}).get("image")
        return f"시총 {fmt.cap(x.get('market_cap'))}, 이미지 {img['label'] if img else '-'}"

    for th in ctx["themes"]:
        leader = th["leader"]
        lines.append(f"코스피 시총 {th['theme_id']}위: {leader['name']} [{th['theme']}]")
        lines.append(f" - 대장주: {leader['name']} ({tag(leader)} / {fmt.price(leader.get('close'))}원 / {fmt.arrow(leader.get('change_pct'))})")
        lines.append(" - 연관주: " + ", ".join(f"{r['name']}({tag(r)} / {fmt.arrow(r.get('change_pct'))})" for r in th["related"]))
        fc = fc_all.get(th["theme_id"], {})
        if fc.get("observation"):
            lines.append(f" - 관찰 포인트: {fc['observation']} {OBSERVATION_NOTE}")
        lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


# ============================================================================
# 08:30 모닝 브리프
# ============================================================================
def render_morning(ctx: dict) -> tuple[str, str]:
    outlook = ctx["outlook"]
    body = ""
    if outlook["mode"] != "ai":
        body += _banner(f"⚠ <b>축소 브리프</b> — AI 코멘트 없이 데이터만 담았습니다. 사유: {_e(outlook['reason'])}")
    for note in ctx.get("warnings", []):
        body += _banner(f"⚠ {_e(note)}")

    if ctx.get("world"):
        rows = [
            [f"<b>{_e(i['name'])}</b>", f"{i['close']:,.2f}" if i.get("close") else "-", _chg(i.get("change_pct"))]
            for i in ctx["world"]
        ]
        body += _section("전일 미국 증시 마감", _table(["지수", "종가", "등락"], rows, ["left", "right", "right"]))
    if ctx.get("fx") or ctx.get("domestic"):
        items = []
        if ctx.get("fx"):
            fx = ctx["fx"]
            items.append(f"<b>원/달러</b> {fx['close']:,.2f}원 {_chg(fx.get('change_pct'))}")
        if ctx.get("domestic"):
            items.append("전일 " + _index_line(ctx["domestic"]))
        body += _section("환율 · 국내 지수", "<p>" + "<br>".join(items) + "</p>")

    if outlook.get("summary") or outlook.get("watch_points"):
        html = f"<p>{_e(outlook.get('summary'))}</p>" if outlook.get("summary") else ""
        if outlook.get("watch_points"):
            html += "<ul>" + "".join(f"<li>{_e(w)}</li>" for w in outlook["watch_points"]) + "</ul>"
        body += _section("개장 전 관찰 포인트 (AI)", html)

    disclosures = ctx.get("disclosures") or []
    if disclosures:
        rows = [
            [
                f"<b>{_e(d['name'])}</b><br><span style='color:{MUTED};font-size:11px'>{_e(d['code'])}</span>",
                f'<a href="{_e(d["url"])}" style="color:#374151">{_e(d["title"])}</a>',
                _label_badge(d["label"]),
                _e(d["date"]),
            ]
            for d in disclosures
        ]
        body += _section("개장 전 특징 공시 (DART)", _table(["종목", "공시", "구분", "접수일"], rows, ["left", "left", "center", "center"]))
    elif ctx.get("dart_status") == "no_key":
        body += _section("개장 전 특징 공시 (DART)", f'<p style="color:{MUTED}">DART_API_KEY 미설정으로 생략</p>')

    cands = ctx.get("gap_candidates") or []
    if cands:
        rows = [
            [
                f"<b>{_e(c['name'])}</b><br><span style='color:{MUTED};font-size:11px'>{_e(c['code'])}</span>",
                f"{fmt.price(c.get('close'))}<br>{_chg(c.get('change_pct'))}",
                _e(" · ".join(c["reasons"])),
                _e(outlook["candidates"].get(c["code"], "")),
            ]
            for c in cands
        ]
        body += _section(
            "갭 상승 관찰 후보",
            _table(["종목", "전일 종가", "근거", "관찰 포인트"], rows, ["left", "right", "left", "left"])
            + f'<p style="color:{MUTED};font-size:12px">{_e(OBSERVATION_NOTE)}</p>',
        )

    model = f" · 분석 모델 {outlook['model']}" if outlook.get("model") else ""
    html = _wrap("🌅 [모닝 브리프] - 08:30 개장 전", f"{_date_label(ctx['date'])}{_e(model)}", body)
    return html, _morning_text(ctx)


def _label_badge(label: str) -> str:
    color = {"호재": UP, "악재": DOWN}.get(label, MUTED)
    return f'<span style="color:{color};font-weight:600">{_e(label)}</span>'


def _morning_text(ctx: dict) -> str:
    lines = [f"[모닝 브리프] - 08:30 개장 전 ({ctx['date']})", ""]
    for i in ctx.get("world", []):
        lines.append(f"{i['name']}: {i['close']:,.2f} ({fmt.pct(i.get('change_pct'), 2)})")
    if ctx.get("fx"):
        lines.append(f"원/달러: {ctx['fx']['close']:,.2f} ({fmt.pct(ctx['fx'].get('change_pct'), 2)})")
    if ctx["outlook"].get("summary"):
        lines += ["", ctx["outlook"]["summary"]]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


# ============================================================================
# 단문 알림 (하트비트, 휴장일, 에러)
# ============================================================================
def render_notice(title: str, lines: list[str], pre: str | None = None) -> str:
    body = "".join(f"<p style='margin:6px 0'>{_e(line)}</p>" for line in lines)
    if pre:
        body += (
            f'<pre style="background:#f3f4f6;padding:10px;border-radius:6px;font-size:11px;'
            f'white-space:pre-wrap;overflow-x:auto">{_e(pre)}</pre>'
        )
    return _document(
        f'<div style="font-family:{FONT};font-size:14px;color:#1f2937;max-width:760px;padding:8px 12px">'
        f'<h3 style="color:{NAVY};margin:6px 0">{_e(title)}</h3>{body}</div>'
    )
