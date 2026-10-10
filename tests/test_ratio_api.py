import unittest
from datetime import date, datetime
from unittest.mock import patch

from backend.config import SHANGHAI_TZ
from backend.ratio_store import RatioHistoryStore
from backend import main


CONTRACTS = {
    "cu": {"m_plus_1": "SHFE.cu2609", "m_plus_2": "SHFE.cu2610"},
    "bc": {"m_plus_1": "INE.bc2609", "m_plus_2": "INE.bc2610"},
}


def bar(close):
    stamp = datetime(2026, 8, 17, 9, 0, tzinfo=SHANGHAI_TZ)
    return {"datetime": int(stamp.timestamp()) * 1_000_000_000,
            "close": close, "volume": 10}


class RatioApiTests(unittest.TestCase):
    def test_route_returns_two_separate_hourly_series_and_metadata(self):
        cache = RatioHistoryStore()
        cache.set_history(
            CONTRACTS, [date(2026, 8, 17)],
            {CONTRACTS["cu"]["m_plus_1"]: [bar(72000)],
             CONTRACTS["bc"]["m_plus_1"]: [bar(64000)],
             CONTRACTS["cu"]["m_plus_2"]: [bar(80000)],
             CONTRACTS["bc"]["m_plus_2"]: [bar(64000)]},
            datetime(2026, 8, 17, 12, 0, tzinfo=SHANGHAI_TZ),
        )
        with patch.object(main, "ratio_store", cache, create=True):
            result = main.get_ratio_history()
        self.assertIn("/api/ratio-history", [route.path for route in main.app.routes])
        self.assertEqual(3600, result["period_seconds"])
        self.assertEqual("Asia/Shanghai", result["timezone"])
        self.assertEqual(20, result["trading_days"])
        self.assertEqual(CONTRACTS, result["contracts"])
        self.assertEqual(1.125, result["m_plus_1"][0]["ratio"])
        self.assertEqual(1.25, result["m_plus_2"][0]["ratio"])
        self.assertEqual(cache.snapshot()["revision"], result["revision"])

    def test_unavailable_history_returns_empty_series_and_explanation(self):
        cache = RatioHistoryStore()
        cache.set_unavailable("历史数据暂不可用")
        with patch.object(main, "ratio_store", cache, create=True):
            result = main.get_ratio_history()
        self.assertEqual("unavailable", result["status"])
        self.assertEqual([], result["m_plus_1"])
        self.assertEqual([], result["m_plus_2"])
        self.assertIn("暂不可用", result["message"])

    def test_dashboard_keeps_prior_fields_and_adds_history_revision(self):
        cache = RatioHistoryStore()
        with patch.object(main, "ratio_store", cache, create=True):
            result = main.get_dashboard()
        for key in ("status", "error", "cu", "bc", "arb", "cache_size", "update_time"):
            self.assertIn(key, result)
        self.assertEqual(cache.snapshot()["revision"], result["ratio_history_revision"])


if __name__ == "__main__":
    unittest.main()
