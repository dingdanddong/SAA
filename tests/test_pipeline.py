import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from stock_agent import funnel, report, validation
from stock_agent.agents.news import NewsAgent
from stock_agent.agents.quant import QuantAgent
from stock_agent.config import load_config

EOK = 100_000_000


def stock(code, name, market="KOSPI", close=50_000, tv_eok=100, cap_eok=5_000, **kw):
    row = {
        "code": code,
        "name": name,
        "market": market,
        "close": close,
        "change_pct": 1.0,
        "trading_value": tv_eok * EOK,
        "market_cap": cap_eok * EOK,
        "end_type": "stock",
        "trade_stop": False,
    }
    row.update(kw)
    return row


class FakeResolver:
    def __init__(self, groups):
        self.groups = groups  # 업종 코드 -> (업종명, [rows])
        self.used_fallback = False

    def industry(self, ind_code):
        return self.groups[ind_code]

    def resolve(self, code):
        for ind_code, (name, members) in self.groups.items():
            if any(m["code"] == code for m in members):
                return f"naver:{ind_code}", name, members
        return None


class FunnelTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()

    def test_kospi_top_clusters(self):
        self.cfg["funnel"].update(top_leaders=3, related_industries={"B": ["C", "B"]})
        k = [stock(f"3000{i}0", f"반도체{i}", "KOSDAQ", cap_eok=cap) for i, cap in enumerate((9000, 8000, 7000, 6000))]
        brokers = [stock("400010", "증권1", "KOSDAQ", cap_eok=4000), stock("400020", "증권2", "KOSDAQ", cap_eok=2000)]
        kospi = [
            stock("100000", "삼성", cap_eok=1_000_000),
            stock("100010", "하이닉스", cap_eok=900_000),
            stock("100020", "은행", cap_eok=500_000),
            stock("100025", "삼성우", cap_eok=2_000_000),                       # 우선주 제외
            stock("100030", "ETF", cap_eok=3_000_000, end_type="etf"),          # ETF 제외
            stock("100040", "작은회사", cap_eok=2_000),
        ]
        universe = kospi + k + brokers
        resolver = FakeResolver({"A": ("반도체", kospi[:2] + k), "B": ("은행", [kospi[2]]), "C": ("증권", brokers)})
        themes = funnel.kospi_top_clusters(universe, set(), resolver, self.cfg)
        self.assertEqual([(t["theme_id"], t["leader"]["name"]) for t in themes], [(1, "삼성"), (2, "하이닉스"), (3, "은행")])
        self.assertEqual([r["name"] for r in themes[0]["related"]], ["반도체0", "반도체1"])   # 동일 업종 시총 순
        self.assertEqual([r["name"] for r in themes[1]["related"]], ["반도체2", "반도체3"])   # 중복 없이 다음 순위
        self.assertEqual([r["name"] for r in themes[2]["related"]], ["증권1", "증권2"])       # 인접 업종 대체
        self.assertTrue(themes[2]["related"][0]["relation"].startswith("인접"))
        codes = [s["code"] for s in funnel.selected_stocks(themes)]
        self.assertEqual((len(codes), len(set(codes))), (9, 9))

    def test_cluster_skipped_without_enough_kosdaq(self):
        self.cfg["funnel"]["top_leaders"] = 1
        leader = stock("100000", "삼성", cap_eok=1_000_000)
        resolver = FakeResolver({"A": ("반도체", [leader, stock("300000", "하나뿐", "KOSDAQ")])})
        self.assertEqual(funnel.kospi_top_clusters([leader], set(), resolver, self.cfg), [])


class ImageTest(unittest.TestCase):
    def test_rate_image(self):
        from stock_agent.llm import LLMUnavailable

        agent = NewsAgent(load_config())
        stocks = [{"code": c, "name": c} for c in "ABCD"]

        def bundles():
            return {
                "A": {"titles": ["a1", "a2", "a3", "a4"]},
                "B": {"titles": ["유상증자 결정에 주가 급락", "대규모 공급계약 체결"]},
                "C": {"titles": []},
                "D": {"titles": ["유상증자 결정에 주가 급락"]},
            }

        raw = {"results": [{"code": "A", "labels": ["긍정", "긍정", "중립", "부정"]}, {"code": "B", "labels": ["긍정"]}]}
        b = bundles()
        with mock.patch("stock_agent.llm.generate_json", return_value=(raw, "m")):
            agent.rate_image(b, stocks)
        self.assertEqual((b["A"]["image"]["label"], b["A"]["image"]["source"], b["A"]["image"]["pos"]), ("긍정", "ai", 2))
        self.assertEqual((b["B"]["image"]["source"], b["B"]["image"]["neg"], b["B"]["image"]["label"]), ("keyword", 1, "중립"))  # 개수 불일치 → 키워드
        self.assertEqual((b["C"]["image"]["total"], b["C"]["image"]["label"]), (0, "중립"))
        self.assertEqual((b["D"]["image"]["neg"], b["D"]["image"]["label"]), (1, "중립"))     # 기사 3건 미만 → 중립

        b = bundles()
        with mock.patch("stock_agent.llm.generate_json", side_effect=LLMUnavailable("quota")):
            agent.rate_image(b, stocks)
        self.assertTrue(all(v["image"]["source"] == "keyword" for v in b.values()))


class ForecastTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.cfg["secrets"]["GEMINI_API_KEY"] = "test"
        self.agent = QuantAgent(self.cfg)
        self.techs = {
            "A": {"close": 100_000, "band_low": 97_000, "band_high": 103_000, "support": 95_000, "ma_state": "정배열", "rsi": 60, "supply_ratio": 5, "change_pct": 1},
            "B": {"close": 20_000, "band_low": 19_400, "band_high": 20_600, "support": 19_000, "ma_state": "혼조", "rsi": 50, "supply_ratio": 1, "change_pct": 1},
            "C": {"close": 30_000, "band_low": 29_000, "band_high": 31_000, "support": 28_500, "ma_state": "역배열", "rsi": 40, "supply_ratio": 8, "change_pct": 1},
        }
        self.payloads = [
            {"theme_id": 1, "related_stocks": [{"code": "B", "name": "비", "price": 20_000}, {"code": "C", "name": "씨", "price": 30_000}]},
            {"theme_id": 2, "related_stocks": [{"code": "B", "name": "비", "price": 20_000}, {"code": "C", "name": "씨", "price": 30_000}]},
            {"theme_id": 3, "related_stocks": [{"code": "B", "name": "비", "price": 20_000}, {"code": "C", "name": "씨", "price": 30_000}]},
        ]

    def test_llm_output_validated(self):
        raw = {
            "market_summary": "반도체 강세가 관찰됨. 매수하세요",
            "themes": [
                {"theme_id": 1, "focus_code": "B", "scenario": "상승 확산", "band_low": 19_500, "band_high": 21_000,
                 "key_level": 19_000, "observation": "비중 확대 권고", "confidence": "보통"},
                # 잘못된 종목코드·비현실적 밴드·없는 시나리오 → 정량값으로 보정
                {"theme_id": 2, "focus_code": "ZZZ", "scenario": "폭등", "band_low": 1, "band_high": 999_999},
            ],
        }
        with mock.patch("stock_agent.llm.generate_json", return_value=(raw, "gemini-test")):
            result = self.agent.forecast(self.payloads, self.techs, {})
        self.assertEqual(result["mode"], "ai")
        self.assertNotIn("매수하세요", result["market_summary"])
        t1, t2, t3 = (result["themes"][i] for i in (1, 2, 3))
        self.assertEqual((t1["focus_code"], t1["band_source"], t1["band_low"]), ("B", "ai", 19_500))
        self.assertNotIn("비중 확대", t1["observation"])
        self.assertEqual(t2["focus_code"], "C")           # 규칙 기반: 수급 집중도 높은 쪽
        self.assertEqual(t2["scenario"], "조정")           # 역배열 → 조정
        self.assertEqual((t2["band_low"], t2["band_source"]), (29_000, "quant"))
        self.assertEqual(t3["source"], "quant")            # LLM 이 빠뜨린 테마

    def test_degraded_when_llm_fails(self):
        from stock_agent.llm import LLMUnavailable

        with mock.patch("stock_agent.llm.generate_json", side_effect=LLMUnavailable("quota")):
            result = self.agent.forecast(self.payloads, self.techs, {})
        self.assertEqual(result["mode"], "degraded")
        self.assertIn("quota", result["reason"])
        self.assertTrue(all(t["source"] == "quant" and t["observation"] is None for t in result["themes"].values()))


class ValidationTest(unittest.TestCase):
    def test_judge(self):
        rec = {"base_date": "20260922", "scenario": "상승 확산", "source": "ai", "base_close": 10_000,
               "band_low": 9_800, "band_high": 10_400, "key_level": 9_700, "code": "A", "name": "에이", "theme": "t", "market": "KOSPI"}
        row = validation.judge(rec, {"date": "20260923", "close": 10_300, "low": 9_900})
        self.assertEqual((row["direction_hit"], row["band_hit"], row["key_level_result"]), (1, 1, "지지 유지"))
        row = validation.judge({**rec, "scenario": "박스권", "key_level": 10_500}, {"date": "20260923", "close": 10_600, "low": 10_000})
        self.assertEqual((row["direction_hit"], row["band_hit"], row["key_level_result"]), (0, 0, "저항 돌파"))

    def test_save_evaluate_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with mock.patch("stock_agent.validation.DATA_DIR", tmp), mock.patch("stock_agent.config.DATA_DIR", tmp):
                themes = [{"theme_id": 1, "market": "KOSPI", "theme": "반도체"}]
                forecast = {"themes": {1: {"focus_code": "A", "focus_name": "에이", "scenario": "상승 확산", "source": "ai",
                                           "base_close": 10_000, "band_low": 9_800, "band_high": 10_400, "key_level": 9_700}}}
                self.assertEqual(validation.save_scenarios("20260922", themes, forecast), 1)
                hist = pd.DataFrame({"date": ["20260922", "20260923"], "close": [10_000, 10_200], "low": [9_900, 9_950], "high": [10_100, 10_300]})
                first = validation.evaluate("20260923", lambda code: hist)
                self.assertEqual(first["stats"]["count"], 1)
                self.assertEqual(first["stats"]["direction"], 100.0)
                again = validation.evaluate("20260923", lambda code: hist)  # 재실행해도 중복 기록 없음
                self.assertEqual(again["stats"]["count"], 1)
                self.assertTrue((tmp / "accuracy_log.csv").exists())


class ReportTest(unittest.TestCase):
    def test_render_evening_ai_mode(self):
        leader = stock("000010", "대장<주>")
        rel = [stock("000020", "연관1"), stock("000030", "연관2")]
        themes = [{"theme_id": 1, "market": "KOSPI", "theme": "반도체", "leader": leader, "related": rel}]
        forecast = {"mode": "ai", "model": "gemini-test", "reason": "", "market_summary": "요약",
                    "themes": {1: {"focus_code": "000020", "focus_name": "연관1", "scenario": "상승 확산", "band_low": 49_000,
                                   "band_high": 52_000, "key_level": 48_000, "band_source": "ai", "observation": "관찰 문장",
                                   "leader_view": "평가", "confidence": "보통"}}}
        ctx = {"trade_date": "20260923", "flow_date": "20260923", "provider": "naver", "indices": [], "themes": themes,
               "techs": {}, "news": {"000020": {"image": {"label": "긍정", "pos": 2, "neu": 1, "neg": 0, "total": 3, "source": "ai"}}},
               "forecast": forecast, "picks": [], "validation": {"available": False}, "warnings": [], "notes": []}
        html, text = report.render_evening(ctx)
        self.assertIn("대장&lt;주&gt;", html)   # HTML 이스케이프
        self.assertIn("관찰 포인트", html)
        self.assertIn("투자자문업 등록 서비스가 아닙니다", html)
        self.assertIn('<meta charset="utf-8">', html)
        self.assertNotIn("축소 리포트", html)
        self.assertIn("관찰 문장", text)
        self.assertIn("코스피 시총 1위", html)
        self.assertIn("시총 5,000억", html)
        self.assertIn("이미지 긍정", html)
        self.assertIn("이미지 긍정", text)


if __name__ == "__main__":
    unittest.main()
