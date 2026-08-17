import unittest
from datetime import datetime

from backend.config import SHANGHAI_TZ
from backend.store import QuoteStore, decorate_status
from backend.tqsdk_worker import clean_number


class QuoteStoreTests(unittest.TestCase):
    def test_nan_is_json_safe(self):
        self.assertIsNone(clean_number(float("nan")))
        self.assertIsNone(clean_number(float("inf")))
        self.assertEqual(123.5, clean_number(123.5))

    def test_status_uses_exchange_timestamp(self):
        item = {
            "has_quote": True,
            "expired": False,
            "datetime": "2026-08-17 10:00:00.000000",
        }
        live = decorate_status(
            item,
            datetime(2026, 8, 17, 10, 1, 0, tzinfo=SHANGHAI_TZ),
        )
        stale = decorate_status(
            item,
            datetime(2026, 8, 17, 10, 3, 0, tzinfo=SHANGHAI_TZ),
        )
        self.assertEqual("live", live["data_status"])
        self.assertEqual("stale", stale["data_status"])

    def test_rollover_removes_old_symbols(self):
        store = QuoteStore()
        old_contracts = {
            "cu": {"m_plus_1": "SHFE.cu2612", "m_plus_2": "SHFE.cu2701"},
            "bc": {"m_plus_1": "INE.bc2612", "m_plus_2": "INE.bc2701"},
        }
        new_contracts = {
            "cu": {"m_plus_1": "SHFE.cu2701", "m_plus_2": "SHFE.cu2702"},
            "bc": {"m_plus_1": "INE.bc2701", "m_plus_2": "INE.bc2702"},
        }
        store.set_contracts(old_contracts)
        store.mark_unavailable(
            symbol="SHFE.cu2612",
            instrument_id="SHFE.cu2612",
            market_datetime="",
            received_at="2026-11-01 00:00:00",
            expired=False,
        )
        store.set_contracts(new_contracts)
        self.assertNotIn("SHFE.cu2612", store.snapshot())
        self.assertEqual(
            ["SHFE.cu2701", "SHFE.cu2702", "INE.bc2701", "INE.bc2702"],
            store.symbols,
        )


if __name__ == "__main__":
    unittest.main()
