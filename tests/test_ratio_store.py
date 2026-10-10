import unittest
import sys
import threading
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from backend.config import SHANGHAI_TZ
from backend.ratio_store import RatioHistoryStore
from backend.store import QuoteStore
from backend.tqsdk_worker import AppState, TqSdkWorker


OLD = {
    "cu": {"m_plus_1": "SHFE.cu2609", "m_plus_2": "SHFE.cu2610"},
    "bc": {"m_plus_1": "INE.bc2609", "m_plus_2": "INE.bc2610"},
}
NEW = {
    "cu": {"m_plus_1": "SHFE.cu2610", "m_plus_2": "SHFE.cu2611"},
    "bc": {"m_plus_1": "INE.bc2610", "m_plus_2": "INE.bc2611"},
}


def row(day, close):
    stamp = datetime(day.year, day.month, day.day, 9, 0, tzinfo=SHANGHAI_TZ)
    return {"datetime": int(stamp.timestamp()) * 1_000_000_000,
            "close": close, "volume": 10}


def bars_for(days):
    return {
        OLD["cu"]["m_plus_1"]: [row(day, 72000) for day in days],
        OLD["bc"]["m_plus_1"]: [row(day, 64000) for day in days],
        OLD["cu"]["m_plus_2"]: [row(day, 80000) for day in days],
        OLD["bc"]["m_plus_2"]: [row(day, 64000) for day in days],
    }


class FixtureCalendarLoader:
    def __init__(self):
        self.loaded_on = None

    def request(self, api, day):
        self.loaded_on = day

    def snapshot(self):
        return self.loaded_on, [date(2026, 8, 17)], None


class RatioHistoryStoreTests(unittest.TestCase):
    def test_calendar_request_does_not_wait_for_slow_history_source(self):
        from backend.tqsdk_worker import CalendarLoader

        entered = threading.Event()
        release = threading.Event()

        class SlowApi:
            def get_trading_calendar(self, start_dt, end_dt):
                entered.set()
                release.wait(1)
                return pd.DataFrame({"date": [pd.Timestamp(date(2026, 8, 17))],
                                     "trading": [True]})

        loader = CalendarLoader()
        try:
            started = time.monotonic()
            loader.request(SlowApi(), date(2026, 8, 17))
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertTrue(entered.wait(1))
            self.assertNotEqual(date(2026, 8, 17), loader.snapshot()[0])
        finally:
            release.set()

    def test_pairs_have_separate_series_on_the_same_20_day_window(self):
        days = [date(2026, 8, 3) + timedelta(days=i) for i in range(29)
                if (date(2026, 8, 3) + timedelta(days=i)).weekday() < 5]
        store = RatioHistoryStore()
        store.set_history(
            OLD, days, bars_for(days),
            datetime(2026, 8, 31, 16, 0, tzinfo=SHANGHAI_TZ),
        )
        snapshot = store.snapshot()
        self.assertEqual("ready", snapshot["status"])
        self.assertEqual(20, len(snapshot["m_plus_1"]))
        self.assertEqual(20, len(snapshot["m_plus_2"]))
        self.assertEqual("2026-08-04T09:00:00+08:00", snapshot["m_plus_1"][0]["time"])
        self.assertEqual(1.125, snapshot["m_plus_1"][-1]["ratio"])
        self.assertEqual(1.25, snapshot["m_plus_2"][-1]["ratio"])

    def test_revision_changes_only_for_changed_history_and_rollover_clears_old_series(self):
        day = date(2026, 8, 17)
        now = datetime(2026, 8, 17, 12, 0, tzinfo=SHANGHAI_TZ)
        bars = bars_for([day])
        store = RatioHistoryStore()
        store.set_history(OLD, [day], bars, now)
        revision = store.snapshot()["revision"]
        store.set_history(OLD, [day], bars, now)
        self.assertEqual(revision, store.snapshot()["revision"])
        changed = {symbol: rows.copy() for symbol, rows in bars.items()}
        changed[OLD["cu"]["m_plus_1"]] = [row(day, 73000)]
        store.set_history(OLD, [day], changed, now)
        self.assertGreater(store.snapshot()["revision"], revision)
        changed_revision = store.snapshot()["revision"]
        store.set_history(NEW, [day], {}, now)
        snapshot = store.snapshot()
        self.assertGreater(snapshot["revision"], changed_revision)
        self.assertEqual([], snapshot["m_plus_1"])
        self.assertEqual([], snapshot["m_plus_2"])
        self.assertEqual(NEW, snapshot["contracts"])

    def test_rollover_clears_old_history_before_new_subscription_succeeds(self):
        day = date(2026, 8, 17)
        store = RatioHistoryStore()
        store.set_history(OLD, [day], bars_for([day]),
                          datetime(2026, 8, 17, 12, 0, tzinfo=SHANGHAI_TZ))
        store.set_unavailable("等待新合约的一小时行情", contracts=NEW)
        snapshot = store.snapshot()
        self.assertEqual(NEW, snapshot["contracts"])
        self.assertEqual([], snapshot["m_plus_1"])
        self.assertEqual([], snapshot["m_plus_2"])
        self.assertEqual("unavailable", snapshot["status"])

    def test_window_uses_current_exchange_day_and_warns_per_series(self):
        days = [date(2026, 8, 3) + timedelta(days=i) for i in range(35)
                if (date(2026, 8, 3) + timedelta(days=i)).weekday() < 5]
        bars = bars_for(days[:20])
        bars[OLD["cu"]["m_plus_2"]] = [row(day, 80000) for day in days]
        bars[OLD["bc"]["m_plus_2"]] = [row(day, 64000) for day in days]
        store = RatioHistoryStore()
        store.set_history(OLD, days, bars,
                          datetime(2026, 9, 4, 16, 0, tzinfo=SHANGHAI_TZ))
        snapshot = store.snapshot()
        self.assertEqual(days[-20].isoformat(), snapshot["m_plus_2"][0]["time"][:10])
        self.assertEqual(15, len(snapshot["m_plus_1"]))
        self.assertIn("M+1", snapshot["message"])
        self.assertNotIn("M+2", snapshot["message"])

    def test_unavailable_history_and_missing_auth_are_explicit(self):
        store = RatioHistoryStore()
        store.set_history(OLD, [date(2026, 8, 17)], {},
                          datetime(2026, 8, 17, 12, 0, tzinfo=SHANGHAI_TZ))
        self.assertEqual("unavailable", store.snapshot()["status"])
        self.assertTrue(store.snapshot()["message"])
        worker = TqSdkWorker(QuoteStore(), AppState(), ratio_store=store,
                             user="", password="")
        worker.start()
        self.assertEqual("unavailable", store.snapshot()["status"])
        self.assertIn("TQ_USER", store.snapshot()["message"])

    def test_current_hour_becomes_final_when_the_hour_ends_without_new_trades(self):
        day = date(2026, 8, 17)
        bars = bars_for([day])
        store = RatioHistoryStore()
        store.set_history(OLD, [day], bars,
                          datetime(2026, 8, 17, 9, 20, tzinfo=SHANGHAI_TZ))
        self.assertTrue(store.snapshot()["m_plus_1"][-1]["partial"])
        self.assertFalse(store.needs_finalization(
            datetime(2026, 8, 17, 9, 50, tzinfo=SHANGHAI_TZ)))
        self.assertTrue(store.needs_finalization(
            datetime(2026, 8, 17, 10, 1, tzinfo=SHANGHAI_TZ)))
        revision = store.snapshot()["revision"]
        store.set_history(OLD, [day], bars,
                          datetime(2026, 8, 17, 10, 1, tzinfo=SHANGHAI_TZ))
        self.assertFalse(store.snapshot()["m_plus_1"][-1]["partial"])
        self.assertGreater(store.snapshot()["revision"], revision)

    def test_worker_subscribes_hourly_bars_and_populates_history(self):
        day = date(2026, 8, 17)
        store = RatioHistoryStore()
        holder = {}
        subscribed = []
        fail_symbol = {"value": None}
        close_by_symbol = {
            OLD["cu"]["m_plus_1"]: 72000,
            OLD["bc"]["m_plus_1"]: 64000,
            OLD["cu"]["m_plus_2"]: 80000,
            OLD["bc"]["m_plus_2"]: 64000,
        }

        class FakeApi:
            def __init__(self, auth):
                self.auth = auth
                self.pending = []
                self.inside_task = False

            def create_task(self, coroutine):
                self.pending.append(coroutine)

            def get_quote(self, symbol):
                return SimpleNamespace(
                    last_price=close_by_symbol[symbol], datetime="2026-08-17 09:00:00",
                    expired=False, instrument_id=symbol, bid_price1=1, ask_price1=2,
                    bid_volume1=1, ask_volume1=1, volume=10, open_interest=1,
                )

            def get_kline_serial(self, symbol, duration_seconds, data_length):
                if not self.inside_task:
                    raise AssertionError("K-line subscriptions must run in an API task")
                subscribed.append((symbol, duration_seconds, data_length))
                if symbol == fail_symbol["value"]:
                    raise ValueError("no history for this contract")
                return pd.DataFrame([row(day, close_by_symbol[symbol])])

            def get_trading_calendar(self, start_dt, end_dt):
                return pd.DataFrame({"date": [pd.Timestamp(day)], "trading": [True]})

            def wait_update(self, deadline):
                self.inside_task = True
                for coroutine in self.pending:
                    try:
                        coroutine.send(None)
                    except StopIteration:
                        pass
                self.pending.clear()
                self.inside_task = False
                holder["worker"].stop()

            def is_changing(self, item, fields=None):
                return True

            def close(self):
                pass

        worker = TqSdkWorker(QuoteStore(), AppState(), ratio_store=store,
                             user="user", password="password")
        holder["worker"] = worker
        with patch("backend.tqsdk_worker.CalendarLoader", FixtureCalendarLoader), patch(
            "backend.tqsdk_worker.get_contracts", return_value=OLD
        ), patch.dict(
            sys.modules, {"tqsdk": SimpleNamespace(TqApi=FakeApi, TqAuth=lambda u, p: (u, p))}
        ):
            worker._run()
            first_subscriptions = subscribed.copy()
            fail_symbol["value"] = OLD["cu"]["m_plus_2"]
            partial_store = RatioHistoryStore()
            quotes = QuoteStore()
            partial_worker = TqSdkWorker(quotes, AppState(), ratio_store=partial_store,
                                         user="user", password="password")
            holder["worker"] = partial_worker
            partial_worker._run()
        self.assertEqual(4, len(first_subscriptions))
        self.assertEqual({3600}, {period for _, period, _ in first_subscriptions})
        self.assertEqual(1.125, store.snapshot()["m_plus_1"][0]["ratio"])
        self.assertEqual(1.25, store.snapshot()["m_plus_2"][0]["ratio"])
        self.assertEqual(1.125, partial_store.snapshot()["m_plus_1"][0]["ratio"])
        self.assertEqual([], partial_store.snapshot()["m_plus_2"])
        self.assertIn(OLD["cu"]["m_plus_1"], quotes.snapshot())

    def test_failed_hourly_subscription_retries_while_quotes_continue(self):
        day = date(2026, 8, 17)
        attempts = []
        clock = {"seconds": 1000}
        holder = {}
        quotes = QuoteStore()
        history = RatioHistoryStore()

        class FakeApi:
            def __init__(self, auth):
                self.pending = []
                self.waits = 0

            def get_quote(self, symbol):
                return SimpleNamespace(last_price=72000, datetime="2026-08-17 09:00:00",
                    expired=False, instrument_id=symbol, bid_price1=1, ask_price1=2,
                    bid_volume1=1, ask_volume1=1, volume=10, open_interest=1)

            def create_task(self, coroutine):
                self.pending.append(coroutine)

            def get_kline_serial(self, symbol, duration_seconds, data_length):
                attempts.append(symbol)
                if symbol == OLD["cu"]["m_plus_1"] and attempts.count(symbol) == 1:
                    raise RuntimeError("temporary history failure")
                return pd.DataFrame([row(day, 72000 if "cu" in symbol else 64000)])

            def get_trading_calendar(self, start_dt, end_dt):
                return pd.DataFrame({"date": [pd.Timestamp(day)], "trading": [True]})

            def wait_update(self, deadline):
                for coroutine in self.pending:
                    try:
                        coroutine.send(None)
                    except StopIteration:
                        pass
                self.pending.clear()
                self.waits += 1
                clock["seconds"] += 31
                if self.waits == 2:
                    holder["worker"].stop()

            def is_changing(self, item, fields=None):
                return True

            def close(self):
                pass

        worker = TqSdkWorker(quotes, AppState(), ratio_store=history,
                             user="user", password="password")
        holder["worker"] = worker
        with patch("backend.tqsdk_worker.CalendarLoader", FixtureCalendarLoader), patch(
            "backend.tqsdk_worker.get_contracts", return_value=OLD
        ), patch(
            "backend.tqsdk_worker.time.time", side_effect=lambda: clock["seconds"]
        ), patch.dict(sys.modules, {"tqsdk": SimpleNamespace(
            TqApi=FakeApi, TqAuth=lambda u, p: (u, p))}):
            worker._run()
        self.assertEqual(2, attempts.count(OLD["cu"]["m_plus_1"]))
        self.assertIn(OLD["cu"]["m_plus_1"], quotes.snapshot())
        self.assertEqual(1.125, history.snapshot()["m_plus_1"][0]["ratio"])

    def test_disconnect_clears_old_live_point(self):
        day = date(2026, 8, 17)
        history = RatioHistoryStore()
        history.set_history(OLD, [day], bars_for([day]),
                            datetime(2026, 8, 17, 9, 20, tzinfo=SHANGHAI_TZ))
        self.assertTrue(history.snapshot()["m_plus_1"][-1]["partial"])
        holder = {}

        class BrokenApi:
            def __init__(self, auth):
                pass

            def get_quote(self, symbol):
                return object()

            def create_task(self, coroutine):
                coroutine.close()

            def wait_update(self, deadline):
                holder["worker"].stop()
                raise RuntimeError("connection lost")

            def close(self):
                pass

        worker = TqSdkWorker(QuoteStore(), AppState(), ratio_store=history,
                             user="user", password="password")
        holder["worker"] = worker
        with patch("backend.tqsdk_worker.get_contracts", return_value=OLD), patch.dict(
            sys.modules, {"tqsdk": SimpleNamespace(TqApi=BrokenApi,
                TqAuth=lambda u, p: (u, p))}
        ):
            worker._run()
        self.assertEqual("unavailable", history.snapshot()["status"])
        self.assertEqual([], history.snapshot()["m_plus_1"])


if __name__ == "__main__":
    unittest.main()
