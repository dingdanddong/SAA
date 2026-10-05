import unittest
from datetime import date

from stock_agent import market_calendar as cal

# 직전까지 config.json 에 손으로 적어 두던 2026년 휴장일
MANUAL_2026 = {
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02", "2026-05-01", "2026-05-05", "2026-05-25",
    "2026-06-03", "2026-08-17", "2026-09-24", "2026-09-25", "2026-10-05", "2026-10-09", "2026-12-25", "2026-12-31",
}


class KrxHolidayTest(unittest.TestCase):
    def test_matches_manual_2026_list(self):
        weekdays = {d for d in cal.krx_holidays([2026]) if date.fromisoformat(d).weekday() < 5 and d.startswith("2026")}
        self.assertEqual(weekdays, MANUAL_2026)

    def test_year_end_closes_on_last_weekday(self):
        self.assertIn("2027-12-31", cal.krx_holidays([2027]))  # 금요일
        self.assertIn("2028-12-29", cal.krx_holidays([2028]))  # 12/31 이 일요일 → 마지막 평일 금요일


if __name__ == "__main__":
    unittest.main()
