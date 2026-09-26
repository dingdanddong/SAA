import unittest

import numpy as np
import pandas as pd

from stock_agent import indicators as ind


def make_df(closes, volumes=None):
    closes = np.asarray(closes, dtype=float)
    volumes = np.full(len(closes), 1000.0) if volumes is None else np.asarray(volumes, dtype=float)
    return pd.DataFrame(
        {
            "date": [f"2026{(i // 28) % 12 + 1:02d}{i % 28 + 1:02d}" for i in range(len(closes))],
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": volumes,
        }
    )


class TickTest(unittest.TestCase):
    def test_tick_size_boundaries(self):
        self.assertEqual(ind.tick_size(1_999), 1)
        self.assertEqual(ind.tick_size(2_000), 5)
        self.assertEqual(ind.tick_size(19_990), 10)
        self.assertEqual(ind.tick_size(49_950), 50)
        self.assertEqual(ind.tick_size(199_900), 100)
        self.assertEqual(ind.tick_size(499_500), 500)
        self.assertEqual(ind.tick_size(500_000), 1_000)

    def test_round_tick(self):
        self.assertEqual(ind.round_tick(41_234, "down"), 41_200)
        self.assertEqual(ind.round_tick(41_234, "up"), 41_250)
        self.assertEqual(ind.round_tick(182_460), 182_500)


class IndicatorTest(unittest.TestCase):
    def test_rsi_extremes(self):
        up = pd.Series(np.arange(1, 40, dtype=float))
        self.assertEqual(ind.rsi(up), 100.0)
        down = pd.Series(np.arange(40, 1, -1, dtype=float))
        self.assertAlmostEqual(ind.rsi(down), 0.0)
        self.assertIsNone(ind.rsi(pd.Series([1.0, 2.0, 3.0])))

    def test_rsi_known_value(self):
        # 와일더 원전 예제 (Welles Wilder, 1978) 근사: 상승·하락이 섞이면 0~100 사이
        s = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
                       45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64])
        value = ind.rsi(s)
        self.assertTrue(55 < value < 75, value)

    def test_ma_state(self):
        self.assertEqual(ind.ma_state({"ma5": 4, "ma20": 3, "ma60": 2, "ma120": 1}), "정배열")
        self.assertEqual(ind.ma_state({"ma5": 1, "ma20": 2, "ma60": 3, "ma120": 4}), "역배열")
        self.assertEqual(ind.ma_state({"ma5": 2, "ma20": 3, "ma60": 1, "ma120": 4}), "혼조")
        self.assertEqual(ind.ma_state({"ma5": 2, "ma20": 1, "ma60": None, "ma120": None}), "판단불가")

    def test_uptrend_analysis(self):
        closes = np.linspace(10_000, 20_000, 200)
        volumes = np.full(200, 1000.0)
        volumes[-1] = 3000
        df = make_df(closes, volumes)
        df["high"] = df["close"] * 1.001  # 윗꼬리 없는 상승: 전일 고가가 오늘 종가 아래
        t = ind.analyze(df)
        self.assertEqual(t["ma_state"], "정배열")
        self.assertTrue(t["new_high"])
        self.assertAlmostEqual(t["volume_ratio"], 3.0)
        self.assertIsNone(t["resistance"])  # 신고가 영역: 위쪽 저항 없음
        self.assertLess(t["stop"], t["close"])
        self.assertLess(t["band_low"], t["close"])
        self.assertGreater(t["band_high"], t["close"])

    def test_stop_level_capped(self):
        # ATR 이 매우 커도 손절 참고가는 종가 대비 max_stop_pct 이내
        stop = ind.stop_level(10_000, None, atr_value=5_000, max_stop_pct=12)
        self.assertGreaterEqual(stop, 8_800)
        self.assertLess(stop, 10_000)

    def test_pullback(self):
        # 상승 추세 후 20일선 부근까지 거래량 줄며 조정
        closes = list(np.linspace(10_000, 15_000, 80)) + [14_900, 14_800, 14_700, 14_600, 14_500]
        volumes = [2000.0] * 80 + [500.0] * 5
        df = make_df(closes, volumes)
        mas = ind.moving_averages(df["close"])
        self.assertTrue(ind.is_pullback(df, mas))

    def test_streak(self):
        self.assertEqual(ind.streak([5, 3, 1, -2, 4]), 3)
        self.assertEqual(ind.streak([-1, 3]), 0)
        self.assertEqual(ind.streak([]), 0)


if __name__ == "__main__":
    unittest.main()
