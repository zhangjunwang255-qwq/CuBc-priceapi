import unittest
from datetime import date, datetime

from backend.config import SHANGHAI_TZ
from backend.ratio_history import build_ratio_series, trading_day_for


def bar(day, hour, minute, close, volume=1):
    stamp = datetime.combine(day, datetime.min.time(), SHANGHAI_TZ).replace(
        hour=hour, minute=minute
    )
    return {"datetime": int(stamp.timestamp()) * 1_000_000_000,
            "close": close, "volume": volume}


class RatioHistoryTests(unittest.TestCase):
    def test_same_hour_closes_make_ratio_and_unmatched_hours_do_not(self):
        day = date(2026, 8, 17)
        cu = [bar(day, 9, 0, 72000), bar(day, 10, 0, 73000)]
        bc = [bar(day, 9, 0, 64000), bar(day, 11, 0, 65000)]
        result = build_ratio_series(
            cu, bc, [day], datetime(2026, 8, 17, 12, 0, tzinfo=SHANGHAI_TZ)
        )
        self.assertEqual(
            [{"time": "2026-08-17T09:00:00+08:00", "ratio": 1.125,
              "partial": False}], result
        )

    def test_invalid_and_duplicate_bars_do_not_create_extra_points(self):
        day = date(2026, 8, 17)
        cu = [bar(day, 9, 0, 72000), bar(day, 9, 0, 72100),
              bar(day, 10, 0, float("nan")), bar(day, 11, 0, 73000),
              bar(day, 13, 0, 74000)]
        bc = [bar(day, 9, 0, 64000), bar(day, 10, 0, 65000),
              bar(day, 11, 0, 0), bar(day, 13, 0, 66000, volume=0)]
        result = build_ratio_series(
            cu, bc, [day], datetime(2026, 8, 17, 15, 0, tzinfo=SHANGHAI_TZ)
        )
        self.assertEqual(1, len(result))
        self.assertEqual(1.1266, result[0]["ratio"])

    def test_night_session_uses_next_trading_day_and_midnight_uses_current(self):
        monday, tuesday = date(2026, 8, 17), date(2026, 8, 18)
        calendar = [monday, tuesday]
        self.assertEqual(
            tuesday,
            trading_day_for(datetime(2026, 8, 17, 21, 0, tzinfo=SHANGHAI_TZ), calendar),
        )
        self.assertEqual(
            tuesday,
            trading_day_for(datetime(2026, 8, 18, 0, 30, tzinfo=SHANGHAI_TZ), calendar),
        )

    def test_only_selected_trading_days_are_returned(self):
        old_day, selected_day = date(2026, 8, 3), date(2026, 8, 4)
        cu = [bar(old_day, 9, 0, 70000), bar(selected_day, 9, 0, 72000)]
        bc = [bar(old_day, 9, 0, 63000), bar(selected_day, 9, 0, 64000)]
        result = build_ratio_series(
            cu, bc, [selected_day], datetime(2026, 8, 4, 12, 0, tzinfo=SHANGHAI_TZ)
        )
        self.assertEqual(["2026-08-04T09:00:00+08:00"], [p["time"] for p in result])

    def test_active_last_bar_is_partial_and_elapsed_bars_are_final(self):
        day = date(2026, 8, 17)
        cu = [bar(day, 9, 0, 72000), bar(day, 10, 0, 72200)]
        bc = [bar(day, 9, 0, 64000), bar(day, 10, 0, 64000)]
        result = build_ratio_series(
            cu, bc, [day], datetime(2026, 8, 17, 10, 20, tzinfo=SHANGHAI_TZ)
        )
        self.assertEqual([False, True], [p["partial"] for p in result])


if __name__ == "__main__":
    unittest.main()
